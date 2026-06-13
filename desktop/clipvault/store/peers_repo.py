"""Paired device registry + sync cursors (SYNC-2, PAIR-1).

token_hash = sha256(token) — the plaintext token is never stored (it lives only
in the peer's Android Keystore). peer_cursor = highest seq applied of the peer's
outbox; my_acked_seq = how much of OUR outbox the peer has confirmed.
"""

import sqlite3


class PeersRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_pair(self, device_id: str, device_name: str, token_hash: str, when: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_peers(device_id, device_name, token_hash, paired_at) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(device_id) DO UPDATE SET device_name=excluded.device_name, "
            "token_hash=excluded.token_hash, paired_at=excluded.paired_at",
            (device_id, device_name, token_hash, when),
        )
        self.conn.commit()

    def by_token_hash(self, token_hash: str) -> dict | None:
        r = self.conn.execute(
            "SELECT device_id, device_name, my_acked_seq, peer_cursor "
            "FROM sync_peers WHERE token_hash = ?", (token_hash,),
        ).fetchone()
        return dict(r) if r else None

    def get(self, device_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT device_id, device_name, my_acked_seq, peer_cursor "
            "FROM sync_peers WHERE device_id = ?", (device_id,),
        ).fetchone()
        return dict(r) if r else None

    def set_peer_cursor(self, device_id: str, cursor: int) -> None:
        self.conn.execute(
            "UPDATE sync_peers SET peer_cursor = ? WHERE device_id = ?",
            (cursor, device_id),
        )
        self.conn.commit()

    def set_my_acked(self, device_id: str, seq: int) -> None:
        self.conn.execute(
            "UPDATE sync_peers SET my_acked_seq = MAX(my_acked_seq, ?) WHERE device_id = ?",
            (seq, device_id),
        )
        self.conn.commit()

    def min_my_acked(self) -> int | None:
        """Lowest my_acked_seq across all peers, or None if no peers paired.
        Events at or below this seq are confirmed by every peer (prunable)."""
        row = self.conn.execute(
            "SELECT MIN(my_acked_seq) FROM sync_peers"
        ).fetchone()
        return None if row[0] is None else int(row[0])

    def touch_last_seen(self, device_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE sync_peers SET last_seen_at = ? WHERE device_id = ?",
            (when, device_id),
        )
        self.conn.commit()
