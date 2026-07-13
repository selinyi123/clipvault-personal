"""Sync outbox (SYNC-2). Append-only event log the desktop publishes to peers.
Each event gets a monotonic seq (AUTOINCREMENT); peers pull by seq cursor.
"""

import json
import sqlite3


class OutboxRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def append(self, kind: str, payload: dict, when: str, *, commit: bool = True) -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_outbox(kind, payload, created_at) VALUES (?,?,?)",
            (kind, json.dumps(payload, ensure_ascii=False), when),
        )
        if commit:
            self.conn.commit()
        return cur.lastrowid

    def list_since(self, since_seq: int, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT seq, kind, payload, created_at FROM sync_outbox "
            "WHERE seq > ? ORDER BY seq LIMIT ?",
            (since_seq, limit),
        ).fetchall()
        events = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError, UnicodeError):
                # The pull boundary treats a non-dict payload as blocked and
                # advances over it.  Do not let one corrupted legacy row crash
                # every pull/status attempt before Gate B can run.
                payload = None
            events.append(
                {
                    "seq": row["seq"],
                    "kind": row["kind"],
                    "payload": payload,
                    "created_at": row["created_at"],
                }
            )
        return events

    def max_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM sync_outbox").fetchone()
        return int(row[0])

    def prune_acked(self, min_acked: int) -> int:
        """Drop events every peer has confirmed (seq <= min_acked). Keeps the
        outbox bounded for long-running self-use. Returns rows deleted."""
        if min_acked <= 0:
            return 0
        cur = self.conn.execute("DELETE FROM sync_outbox WHERE seq <= ?", (min_acked,))
        self.conn.commit()
        return cur.rowcount
