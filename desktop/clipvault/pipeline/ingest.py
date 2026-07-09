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


@dataclass(frozen=True)
class IngestPlan:
    """Pure ingest decision output before persistence side effects.

    The plan is built after normalization, rejection, and DB dedup have already
    happened. It captures the deterministic Gate A/classification result plus
    which downstream queues may receive the new clip.
    """

    content: str
    content_hash: str
    clip: Clip
    is_secret: bool
    should_backup: bool
    should_sync: bool
    should_write_obsidian: bool


@dataclass
class IngestOutcome:
    status: str
    clip: Clip | None = None
    needs_obsidian: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_ingest_plan(
    content: str,
    *,
    content_hash: str,
    source_device: str,
    source_app: str | None,
    now: str,
    new_id_fn: Callable[[], str] = ulid.new,
) -> IngestPlan:
    """Build the deterministic ingest plan for a non-duplicate clip.

    This function performs no database, filesystem, network, Obsidian, backup,
    or sync writes. Callers remain responsible for rejection and dedup so the
    contractual order stays normalize -> reject -> dedup -> secret guard.

    ``content`` must already be normalized and ``content_hash`` must be the
    corresponding normalized-content hash. The check protects future callers
    that reuse this helper outside the main ingest path.
    """

    if content_hash != normalize.content_hash(content):
        raise ValueError("content_hash does not match normalized content")

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
    public = not clip.is_secret
    return IngestPlan(
        content=content,
        content_hash=content_hash,
        clip=clip,
        is_secret=clip.is_secret,
        should_backup=public,
        should_sync=public,
        should_write_obsidian=public,
    )


def _duplicate_outcome(clips: ClipsRepo, clip_id: str, now: str) -> IngestOutcome:
    # Deleted clips stay deleted (no resurrection); seen-count still grows.
    clips.touch_seen(clip_id, now, commit=False)
    return IngestOutcome(STATUS_DUPLICATE, clips.get(clip_id))


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

    # Fast duplicate path preserves the contractual order: dedup happens before
    # Secret Guard/classification/ID generation. The write is still performed
    # inside a unit_of_work so touch_seen can join outer transactions safely.
    existing = clips.get_by_hash(content_hash)
    if existing is not None:
        with unit_of_work(conn):
            current = clips.get_by_hash(content_hash)
            if current is not None:
                return _duplicate_outcome(clips, current.id, now)
        # Extremely defensive: if a row was hard-deleted between the pre-check
        # and the transaction, fall through to the new-clip path below.

    # Build the deterministic plan outside the writer transaction. This avoids
    # holding SQLite's write lock while Secret Guard, classification, ID
    # generation, or lazy sync import work runs.
    plan = build_ingest_plan(
        content,
        content_hash=content_hash,
        source_device=source_device,
        source_app=source_app,
        now=now,
        new_id_fn=new_id_fn,
    )
    sync_emit_clip_new = None
    if plan.should_sync:
        from clipvault.sync import engine
        sync_emit_clip_new = engine.emit_clip_new

    with unit_of_work(conn):
        # Race-safe dedup: another writer may have inserted the same content
        # after the pre-check and before this transaction acquired the writer lock.
        existing = clips.get_by_hash(content_hash)
        if existing is not None:
            return _duplicate_outcome(clips, existing.id, now)

        clips.insert(plan.clip, commit=False)

        if plan.should_backup:
            backup_queue.enqueue(plan.clip.id, now, commit=False)
        if sync_emit_clip_new is not None:
            # Publish to the sync outbox (gate B inside emit_clip_new skips secrets).
            # Local captures only; remote-applied clips bypass ingest so there is no echo.
            sync_emit_clip_new(conn, plan.clip, now, commit=False)

        return IngestOutcome(
            STATUS_NEW,
            plan.clip,
            needs_obsidian=plan.should_write_obsidian,
        )
