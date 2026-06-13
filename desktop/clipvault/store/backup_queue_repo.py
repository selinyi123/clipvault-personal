"""Backup queue (GHB-1 producer side). Gate B: callers must never enqueue
secret clips; enqueue() re-checks as defence in depth.
"""

import sqlite3


class SecretEnqueueError(Exception):
    """Raised when a secret clip reaches the backup queue (gate violation)."""


class BackupQueueRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, clip_id: str, when: str) -> bool:
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
        self.conn.commit()
        return cur.rowcount > 0

    def pending_clip_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT clip_id FROM backup_queue WHERE state = 'pending' ORDER BY id"
        ).fetchall()
        return [r[0] for r in rows]

    def has(self, clip_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM backup_queue WHERE clip_id = ?", (clip_id,)
        ).fetchone()
        return row is not None
