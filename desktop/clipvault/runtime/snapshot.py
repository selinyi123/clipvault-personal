"""Bounded, context-free ClipVault candidate snapshots for local IME hosts.

The publisher deliberately has no query or application-context parameter.  It
reads already-authorized local data, re-applies Secret Guard to complete values,
and returns an in-memory snapshot whose opaque IDs are valid for one publisher
epoch/generation only.  Platform IPC adapters live outside this module.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from clipvault.core import secret_guard, suggest
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.memory_repo import MemoryRepo, memory_contains_secret

PROTOCOL_VERSION = 1
MAX_ITEMS = 8
MAX_CANDIDATE_ID_BYTES = 128
MAX_LABEL_BYTES = 64
MAX_TEXT_BYTES = 16_384
MAX_RESPONSE_BYTES = 65_536
MAX_SNAPSHOT_LIFETIME_MS = 30_000
_SUGGEST_WINDOW_DAYS = 30


@dataclass(frozen=True)
class SnapshotItem:
    candidate_id: str
    source: int
    label: str
    text: str


@dataclass(frozen=True)
class RuntimeSnapshot:
    request_id: int
    publisher_epoch: str
    generation: int
    expires_at_ms: int
    items: tuple[SnapshotItem, ...]


class RuntimeSnapshotPublisher:
    """Create snapshots from one worker-owned SQLite connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        weights: suggest.Weights | None = None,
        publisher_epoch: str | None = None,
        now_ms: Callable[[], int] | None = None,
        candidate_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._conn = conn
        self._memory = MemoryRepo(conn)
        self._clips = ClipsRepo(conn)
        self._weights = weights or suggest.Weights()
        self._publisher_epoch = str(
            uuid.UUID(publisher_epoch) if publisher_epoch else uuid.uuid4()
        )
        self._generation = 0
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self._candidate_id_factory = candidate_id_factory or (
            lambda: secrets.token_urlsafe(18)
        )

    @property
    def publisher_epoch(self) -> str:
        return self._publisher_epoch

    def publish(self, *, request_id: int, limit: int) -> RuntimeSnapshot:
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            raise ValueError("request_id must be an integer")
        if not 1 <= request_id <= (2**63 - 1):
            raise ValueError("request_id out of range")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_ITEMS:
            raise ValueError("limit out of range")

        now_ms = int(self._now_ms())
        now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        ranked = suggest.rank(
            self._eligible_candidates(now),
            "",
            None,
            self._weights,
            now,
            limit=max(limit * 4, 32),
        )
        self._generation += 1
        if self._generation > 2**63 - 1:
            raise OverflowError("snapshot generation exhausted")

        items: list[SnapshotItem] = []
        used_ids: set[str] = set()
        for candidate, _score in ranked:
            if len(items) >= limit:
                break
            source = 1 if candidate.origin == "memory" else 2
            label = candidate.label or ("Memory" if source == 1 else "Clipboard")
            if not _valid_text(candidate.text, MAX_TEXT_BYTES):
                continue
            if not _valid_optional_text(label, MAX_LABEL_BYTES):
                continue
            if secret_guard.scan(candidate.text).is_secret:
                continue
            if secret_guard.scan(label).is_secret:
                continue

            candidate_id = self._candidate_id_factory()
            if (
                not _valid_text(candidate_id, MAX_CANDIDATE_ID_BYTES)
                or candidate_id in used_ids
            ):
                continue
            item = SnapshotItem(candidate_id, source, label, candidate.text)
            trial = RuntimeSnapshot(
                request_id=request_id,
                publisher_epoch=self._publisher_epoch,
                generation=self._generation,
                expires_at_ms=now_ms + MAX_SNAPSHOT_LIFETIME_MS,
                items=tuple([*items, item]),
            )
            # Use the real wire encoder as the aggregate bound.  An item that
            # would overflow is omitted; no displayed content is truncated.
            from clipvault.runtime.snapshot_protocol import encode_snapshot_response

            if len(encode_snapshot_response(trial)) > MAX_RESPONSE_BYTES:
                continue
            used_ids.add(candidate_id)
            items.append(item)

        return RuntimeSnapshot(
            request_id=request_id,
            publisher_epoch=self._publisher_epoch,
            generation=self._generation,
            expires_at_ms=now_ms + MAX_SNAPSHOT_LIFETIME_MS,
            items=tuple(items),
        )

    def _eligible_candidates(self, now: datetime) -> list[suggest.Candidate]:
        candidates: list[suggest.Candidate] = []
        for item in self._memory.list(limit=500):
            if memory_contains_secret(item.text, item.label):
                continue
            candidates.append(
                suggest.Candidate(
                    id=item.id,
                    kind=item.kind,
                    text=item.text,
                    label=item.label,
                    pinned=item.pinned,
                    use_count=item.use_count,
                    last_used_at=item.last_used_at,
                    origin="memory",
                )
            )

        since = (now - timedelta(days=_SUGGEST_WINDOW_DAYS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for clip in self._clips.suggest_candidates(since, limit=500):
            # Persisted is_secret reflects the rules at capture time.  Always
            # re-scan the complete value before it crosses the IME boundary.
            if secret_guard.scan(clip.content).is_secret:
                self._clips.quarantine_current_secret(clip.id)
                continue
            candidates.append(
                suggest.Candidate(
                    id=clip.id,
                    kind=clip.content_type,
                    text=clip.content,
                    label="Clipboard",
                    pinned=clip.pinned,
                    use_count=clip.times_seen,
                    last_used_at=clip.last_seen_at,
                    origin="clip",
                )
            )
        return candidates


def _valid_text(value: object, maximum: int) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        return False
    return 1 <= size <= maximum


def _valid_optional_text(value: object, maximum: int) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode("utf-8", "strict")) <= maximum
    except UnicodeError:
        return False
