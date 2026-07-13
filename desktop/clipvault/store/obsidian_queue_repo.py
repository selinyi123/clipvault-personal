"""Bounded, leased Obsidian retry queue.

The queue stores only clip ids and content-safe error classes.  A claim token is
encoded in the existing ``state`` column (``claimed:<token>``), while
``next_attempt_at`` doubles as the lease expiry for claimed rows.  This keeps the
v6 queue rows compatible while v7 adds only bounded-maintenance cursors and
indexes.  Foreground and maintenance workers cannot own the same row at once.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from clipvault.store.unit_of_work import unit_of_work

_BACKOFF_SECONDS = (60, 120, 300, 900, 1800)
_DEFAULT_LEASE_SECONDS = 300
_CLAIMED_PREFIX = "claimed:"
_ERROR_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,79}$")


@dataclass(frozen=True)
class ObsidianClaim:
    clip_id: str
    token: str

    @property
    def state(self) -> str:
        return f"{_CLAIMED_PREFIX}{self.token}"


def _parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plus_seconds(now: str, seconds: int) -> str:
    return _format_utc(_parse_utc(now) + timedelta(seconds=max(1, int(seconds))))


def _next_attempt_at(now: str, attempts_after_failure: int) -> str:
    idx = max(0, min(attempts_after_failure - 1, len(_BACKOFF_SECONDS) - 1))
    return _plus_seconds(now, _BACKOFF_SECONDS[idx])


def _safe_error(error: str) -> str:
    """Keep only a class-like identifier; never persist exception messages."""

    candidate = str(error or "obsidian_write_failed").split(":", 1)[0].strip()
    if _ERROR_CLASS.fullmatch(candidate):
        return candidate
    return "obsidian_write_failed"


def _bounded_limit(limit: int, *, maximum: int = 500) -> int:
    return max(1, min(int(limit), maximum))


class ObsidianQueueRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, clip_id: str, when: str, *, commit: bool = True) -> bool:
        """Queue an eligible public clip; return False if already queued."""

        cur = self.conn.execute(
            "INSERT OR IGNORE INTO obsidian_queue "
            "(clip_id, state, attempts, next_attempt_at, created_at, updated_at) "
            "SELECT id, 'pending', 0, ?, ?, ? FROM clips "
            "WHERE id = ? AND is_secret = 0 AND deleted = 0 AND obsidian_path IS NULL",
            (when, when, when, clip_id),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def reconcile_missing(
        self,
        now: str,
        *,
        limit: int = 50,
        commit: bool = True,
    ) -> int:
        """Inspect and repair at most ``limit`` eligible clips using a keyset cursor.

        Work is bounded even when every eligible clip already has a queue row;
        the cursor wraps only after reaching the end of the partial index.
        """

        if commit:
            with unit_of_work(self.conn):
                return self.reconcile_missing(now, limit=limit, commit=False)
        limit = _bounded_limit(limit)
        cursor = self.conn.execute(
            "SELECT last_created_at, last_clip_id FROM obsidian_reconcile_state "
            "WHERE singleton=1"
        ).fetchone()
        last_created_at, last_clip_id = cursor if cursor else ("", "")
        rows = self.conn.execute(
            "SELECT id, created_at FROM clips "
            "WHERE obsidian_path IS NULL AND is_secret=0 AND deleted=0 "
            "AND (created_at, id) > (?, ?) "
            "ORDER BY created_at, id LIMIT ?",
            (last_created_at, last_clip_id, limit),
        ).fetchall()
        inserted = 0
        for row in rows:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO obsidian_queue "
                "(clip_id, state, attempts, next_attempt_at, created_at, updated_at) "
                "VALUES (?, 'pending', 0, ?, ?, ?)",
                (row["id"], now, now, now),
            )
            inserted += max(0, cur.rowcount)
        if rows and len(rows) == limit:
            next_created_at, next_clip_id = rows[-1]["created_at"], rows[-1]["id"]
        else:
            # End reached: the next maintenance pass starts a fresh bounded cycle.
            next_created_at, next_clip_id = "", ""
        self.conn.execute(
            "UPDATE obsidian_reconcile_state SET last_created_at=?, last_clip_id=? "
            "WHERE singleton=1",
            (next_created_at, next_clip_id),
        )
        return inserted

    def cleanup_ineligible(
        self,
        *,
        limit: int = 100,
        commit: bool = True,
    ) -> int:
        """Remove at most ``limit`` rows whose clips no longer need a write."""

        if commit:
            with unit_of_work(self.conn):
                return self.cleanup_ineligible(limit=limit, commit=False)
        limit = _bounded_limit(limit)
        cursor = self.conn.execute(
            "SELECT cleanup_updated_at, cleanup_clip_id FROM obsidian_reconcile_state "
            "WHERE singleton=1"
        ).fetchone()
        last_updated_at, last_clip_id = cursor if cursor else ("", "")
        rows = self.conn.execute(
            "SELECT q.clip_id, q.updated_at, c.id AS existing_id, c.is_secret, "
            "c.deleted, c.obsidian_path FROM obsidian_queue q "
            "LEFT JOIN clips c ON c.id = q.clip_id "
            "WHERE q.state='pending' AND (q.updated_at, q.clip_id) > (?, ?) "
            "ORDER BY q.updated_at, q.clip_id LIMIT ?",
            (last_updated_at, last_clip_id, limit),
        ).fetchall()
        ids = [
            row["clip_id"]
            for row in rows
            if row["existing_id"] is None
            or row["is_secret"]
            or row["deleted"]
            or row["obsidian_path"] is not None
        ]
        if ids:
            self.conn.executemany(
                "DELETE FROM obsidian_queue WHERE clip_id = ?", ((clip_id,) for clip_id in ids)
            )
        if rows and len(rows) == limit:
            next_updated_at, next_clip_id = rows[-1]["updated_at"], rows[-1]["clip_id"]
        else:
            next_updated_at, next_clip_id = "", ""
        self.conn.execute(
            "UPDATE obsidian_reconcile_state SET cleanup_updated_at=?, cleanup_clip_id=? "
            "WHERE singleton=1",
            (next_updated_at, next_clip_id),
        )
        return len(ids)

    def _recover_expired(self, now: str, *, limit: int = 100) -> int:
        limit = _bounded_limit(limit)
        rows = self.conn.execute(
            "SELECT clip_id FROM obsidian_queue "
            "INDEXED BY idx_obsidian_queue_claim_expiry "
            "WHERE state >= 'claimed:' AND state < 'claimed;' "
            "AND next_attempt_at <= ? "
            "ORDER BY next_attempt_at, clip_id LIMIT ?",
            (now, limit),
        ).fetchall()
        ids = [row[0] for row in rows]
        if ids:
            self.conn.executemany(
                "UPDATE obsidian_queue SET state='pending', next_attempt_at=?, updated_at=? "
                "WHERE clip_id=? AND state >= 'claimed:' AND state < 'claimed;' "
                "AND next_attempt_at <= ?",
                ((now, now, clip_id, now) for clip_id in ids),
            )
        return len(ids)

    def claim_ready(
        self,
        now: str,
        *,
        limit: int = 50,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> list[ObsidianClaim]:
        """Atomically lease a ready batch and return ownership-bearing claims."""

        limit = _bounded_limit(limit)
        lease_until = _plus_seconds(now, lease_seconds)
        token = uuid4().hex
        state = f"{_CLAIMED_PREFIX}{token}"
        claimed: list[ObsidianClaim] = []
        with unit_of_work(self.conn):
            self._recover_expired(now, limit=limit)
            rows = self.conn.execute(
                "SELECT q.clip_id FROM obsidian_queue q "
                "JOIN clips c ON c.id = q.clip_id "
                "WHERE q.state = 'pending' AND q.next_attempt_at <= ? "
                "AND c.is_secret = 0 AND c.deleted = 0 AND c.obsidian_path IS NULL "
                "ORDER BY q.next_attempt_at, q.created_at, q.clip_id LIMIT ?",
                (now, limit),
            ).fetchall()
            for row in rows:
                clip_id = row[0]
                cur = self.conn.execute(
                    "UPDATE obsidian_queue SET state=?, next_attempt_at=?, updated_at=? "
                    "WHERE clip_id=? AND state='pending' AND next_attempt_at <= ?",
                    (state, lease_until, now, clip_id, now),
                )
                if cur.rowcount:
                    claimed.append(ObsidianClaim(clip_id, token))
        return claimed

    def claim_one(
        self,
        clip_id: str,
        now: str,
        *,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> ObsidianClaim | None:
        """Atomically lease one specific ready row for a foreground write."""

        lease_until = _plus_seconds(now, lease_seconds)
        claim = ObsidianClaim(clip_id, uuid4().hex)
        with unit_of_work(self.conn):
            self.conn.execute(
                "UPDATE obsidian_queue SET state='pending', next_attempt_at=?, updated_at=? "
                "WHERE clip_id=? AND state >= 'claimed:' AND state < 'claimed;' "
                "AND next_attempt_at <= ?",
                (now, now, clip_id, now),
            )
            cur = self.conn.execute(
                "UPDATE obsidian_queue SET state=?, next_attempt_at=?, updated_at=? "
                "WHERE clip_id=? AND state='pending' AND next_attempt_at <= ?",
                (claim.state, lease_until, now, clip_id, now),
            )
        return claim if cur.rowcount else None

    def mark_done(self, claim: ObsidianClaim, *, commit: bool = True) -> bool:
        cur = self.conn.execute(
            "DELETE FROM obsidian_queue WHERE clip_id = ? AND state = ?",
            (claim.clip_id, claim.state),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def record_failure(
        self,
        claim: ObsidianClaim,
        error: str,
        now: str,
        *,
        commit: bool = True,
    ) -> int:
        """Release an owned claim with safe exponential backoff."""

        row = self.conn.execute(
            "SELECT q.attempts FROM obsidian_queue q "
            "JOIN clips c ON c.id = q.clip_id "
            "WHERE q.clip_id=? AND q.state=? AND c.is_secret=0 AND c.deleted=0 "
            "AND c.obsidian_path IS NULL",
            (claim.clip_id, claim.state),
        ).fetchone()
        if row is None:
            if commit:
                self.conn.commit()
            return 0
        attempts = int(row[0]) + 1
        cur = self.conn.execute(
            "UPDATE obsidian_queue SET state='pending', attempts=?, next_attempt_at=?, "
            "last_error=?, updated_at=? WHERE clip_id=? AND state=?",
            (
                attempts,
                _next_attempt_at(now, attempts),
                _safe_error(error),
                now,
                claim.clip_id,
                claim.state,
            ),
        )
        if commit:
            self.conn.commit()
        return attempts if cur.rowcount else 0

    def stats(self, now: str) -> dict[str, int | str | None]:
        total = self.conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0]
        ready = self.conn.execute(
            "SELECT COUNT(*) FROM obsidian_queue q "
            "JOIN clips c ON c.id = q.clip_id "
            "WHERE q.state = 'pending' AND q.next_attempt_at <= ? "
            "AND c.is_secret = 0 AND c.deleted = 0 AND c.obsidian_path IS NULL",
            (now,),
        ).fetchone()[0]
        row = self.conn.execute(
            "SELECT MIN(next_attempt_at) FROM obsidian_queue WHERE state = 'pending'"
        ).fetchone()
        max_attempts = self.conn.execute(
            "SELECT COALESCE(MAX(attempts), 0) FROM obsidian_queue"
        ).fetchone()[0]
        return {
            "pending": int(total),
            "ready": int(ready),
            "blocked": int(total) - int(ready),
            "max_attempts": int(max_attempts),
            "next_attempt_at": row[0] if row else None,
        }
