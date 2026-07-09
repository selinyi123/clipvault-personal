"""Bounded Obsidian retry queue.

The queue keeps Obsidian write retries explicit and bounded. It is content-safe:
only clip ids and error classes/messages are stored; clip text never appears in
this table or logs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

_BACKOFF_SECONDS = (60, 120, 300, 900, 1800)
_MAX_ERROR_CHARS = 500


def _parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_attempt_at(now: str, attempts_after_failure: int) -> str:
    idx = max(0, min(attempts_after_failure - 1, len(_BACKOFF_SECONDS) - 1))
    return _format_utc(_parse_utc(now) + timedelta(seconds=_BACKOFF_SECONDS[idx]))


def _safe_error(error: str) -> str:
    return str(error or "obsidian_write_failed")[:_MAX_ERROR_CHARS]


class ObsidianQueueRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, clip_id: str, when: str, *, commit: bool = True) -> bool:
        """Queue a public clip for Obsidian write retry.

        Returns False when the row already exists or the clip is ineligible.
        Secret/deleted/already-written clips are refused by the SELECT guard and
        therefore never queued.
        """

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

    def claim_ready(self, now: str, *, limit: int = 50) -> list[str]:
        limit = max(1, min(int(limit), 500))
        rows = self.conn.execute(
            "SELECT q.clip_id FROM obsidian_queue q "
            "JOIN clips c ON c.id = q.clip_id "
            "WHERE q.state = 'pending' "
            "AND q.next_attempt_at <= ? "
            "AND c.is_secret = 0 AND c.deleted = 0 AND c.obsidian_path IS NULL "
            "ORDER BY q.next_attempt_at, q.created_at, q.clip_id "
            "LIMIT ?",
            (now, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def mark_done(self, clip_id: str, *, commit: bool = True) -> None:
        self.conn.execute("DELETE FROM obsidian_queue WHERE clip_id = ?", (clip_id,))
        if commit:
            self.conn.commit()

    def record_failure(
        self,
        clip_id: str,
        error: str,
        now: str,
        *,
        commit: bool = True,
    ) -> int:
        row = self.conn.execute(
            "SELECT attempts FROM obsidian_queue WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        attempts = int(row[0]) + 1 if row else 1
        next_at = _next_attempt_at(now, attempts)
        safe_error = _safe_error(error)
        cur = self.conn.execute(
            "INSERT INTO obsidian_queue "
            "(clip_id, state, attempts, next_attempt_at, last_error, created_at, updated_at) "
            "SELECT id, 'pending', ?, ?, ?, ?, ? FROM clips "
            "WHERE id = ? AND is_secret = 0 AND deleted = 0 AND obsidian_path IS NULL "
            "ON CONFLICT(clip_id) DO UPDATE SET "
            "state='pending', attempts=?, next_attempt_at=?, last_error=?, updated_at=?",
            (
                attempts,
                next_at,
                safe_error,
                now,
                now,
                clip_id,
                attempts,
                next_at,
                safe_error,
                now,
            ),
        )
        if commit:
            self.conn.commit()
        return attempts if cur.rowcount > 0 else 0

    def cleanup_ineligible(self, *, commit: bool = True) -> int:
        """Remove stale queue rows for clips that no longer need Obsidian writes."""

        cur = self.conn.execute(
            "DELETE FROM obsidian_queue "
            "WHERE clip_id NOT IN ("
            "  SELECT id FROM clips "
            "  WHERE is_secret = 0 AND deleted = 0 AND obsidian_path IS NULL"
            ")"
        )
        if commit:
            self.conn.commit()
        return cur.rowcount

    def stats(self, now: str) -> dict[str, int | str | None]:
        total = self.conn.execute(
            "SELECT COUNT(*) FROM obsidian_queue WHERE state = 'pending'"
        ).fetchone()[0]
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
