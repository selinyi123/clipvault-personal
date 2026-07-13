"""Backup queue (GHB-1). Gate B: callers must never enqueue secret clips;
enqueue() re-checks as defence in depth. Worker-side state transitions
(claim/done/dropped/attempt) live here too.
"""

import sqlite3


class SecretEnqueueError(Exception):
    """Raised when a secret clip reaches the backup queue (gate violation)."""


class BackupQueueRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, clip_id: str, when: str, *, commit: bool = True) -> bool:
        """Queue a clip for backup. Returns False if already queued."""
        secret = self.conn.execute(
            "SELECT is_secret FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        if secret is not None and secret[0]:
            raise SecretEnqueueError(f"secret clip must not be backed up: {clip_id}")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO backup_queue(clip_id, created_at) VALUES (?, ?)",
            (clip_id, when),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def reenqueue(self, clip_id: str, when: str, *, commit: bool = True) -> None:
        """Re-activate a clip for backup after its metadata changed (pin/favorite/
        delete). Unlike enqueue(), this resets an already-'done' row back to
        'pending' so the worker re-serializes the current state — otherwise a
        deletion made after the first backup is never reflected in the JSONL and
        the clip resurrects on restore (GHB-1). Gate B still applies: secrets must
        not be backed up, so callers skip secret clips and this re-checks."""
        secret = self.conn.execute(
            "SELECT is_secret FROM clips WHERE id = ?", (clip_id,)
        ).fetchone()
        if secret is not None and secret[0]:
            raise SecretEnqueueError(f"secret clip must not be backed up: {clip_id}")
        self.conn.execute(
            "INSERT INTO backup_queue(clip_id, created_at, state) VALUES (?, ?, 'pending') "
            "ON CONFLICT(clip_id) DO UPDATE SET state='pending', done_at=NULL",
            (clip_id, when),
        )
        if commit:
            self.conn.commit()

    def pending_clip_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT clip_id FROM backup_queue WHERE state = 'pending' ORDER BY id"
        ).fetchall()
        return [r[0] for r in rows]

    def claim_pending(self, limit: int = 200) -> list[str]:
        rows = self.conn.execute(
            "SELECT clip_id FROM backup_queue WHERE state = 'pending' ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    def mark_done(self, clip_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE backup_queue SET state='done', done_at=? WHERE clip_id=?",
            (when, clip_id),
        )
        self.conn.commit()

    def mark_dropped(self, clip_id: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE backup_queue SET state='dropped_secret', last_error=? WHERE clip_id=?",
            (reason, clip_id),
        )
        self.conn.commit()

    def record_attempt(self, clip_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE backup_queue SET attempts=attempts+1, last_error=? WHERE clip_id=?",
            (error, clip_id),
        )
        self.conn.commit()

    def has(self, clip_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM backup_queue WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row is not None

    def state_of(self, clip_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT state FROM backup_queue WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row[0] if row else None
