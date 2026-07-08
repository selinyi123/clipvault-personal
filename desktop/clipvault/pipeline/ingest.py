"""Ingest pipeline (ARCHITECTURE §5, SLICE_001 §4.4).

Order is contractual:
normalize -> reject check -> dedup -> secret guard (gate A) -> classify
-> store (FTS handled by repo) -> backup enqueue + obsidian indication
(public clips only).
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from clipvault.core import classifier, normalize, secret_guard, ulid
from clipvault.core.models import Clip
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.unit_of_work import unit_of_work

STATUS_NEW = "new"
STATUS_DUPLICATE = "duplicate"
STATUS_REJECTED_EMPTY = "rejected_empty"
STATUS_REJECTED_TOO_LARGE = "rejected_too_large"


@dataclass
class IngestOutcome:
    status: str
    clip: Clip | None = None
    needs_obsidian: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ingest(
    conn: sqlite3.Connection,
    raw_text: str,
    *,
    source_device: str,
    source_app: str | None = None,
    max_bytes: int = normalize.DEFAULT_MAX_CLIP_BYTES,
    now_fn: Callable[[], str] = _utc_now,
    new_id_fn: Callable[[], str] = ulid.new,
) -> IngestOutcome:
    clips = ClipsRepo(conn)
    backup_queue = BackupQueueRepo(conn)

    content = normalize.normalize(raw_text)
    reason = normalize.reject_reason(content, max_bytes)
    if reason == normalize.REJECT_EMPTY:
        return IngestOutcome(STATUS_REJECTED_EMPTY)
    if reason == normalize.REJECT_TOO_LARGE:
        return IngestOutcome(STATUS_REJECTED_TOO_LARGE)

    content_hash = normalize.content_hash(content)
    now = now_fn()

    with unit_of_work(conn):
        existing = clips.get_by_hash(content_hash)
        if existing is not None:
            # Deleted clips stay deleted (no resurrection); seen-count still grows.
            clips.touch_seen(existing.id, now, commit=False)
            return IngestOutcome(STATUS_DUPLICATE, clips.get(existing.id))

        verdict = secret_guard.scan(content)  # gate A
        content_type = classifier.classify(content)

        clip = Clip(
            id=new_id_fn(),
            content=content,
            content_hash=content_hash,
            content_type=content_type,
            is_secret=verdict.is_secret,
            secret_level=verdict.level,
            secret_reasons=verdict.reasons,
            source_device=source_device,
            source_app=source_app,
            created_at=now,
            last_seen_at=now,
        )
        clips.insert(clip, commit=False)

        if clip.is_secret:
            return IngestOutcome(STATUS_NEW, clip, needs_obsidian=False)

        backup_queue.enqueue(clip.id, now, commit=False)
        # Publish to the sync outbox (gate B inside emit_clip_new skips secrets).
        # Local captures only; remote-applied clips bypass ingest so there is no echo.
        from clipvault.sync import engine
        engine.emit_clip_new(conn, clip, now, commit=False)
        return IngestOutcome(STATUS_NEW, clip, needs_obsidian=True)
