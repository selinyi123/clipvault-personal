"""Paired device registry + sync cursors (SYNC-2, PAIR-1).

token_hash = sha256(token) — the plaintext token is never stored (it lives only
in the peer's Android Keystore). peer_cursor = highest seq applied of the peer's
outbox; my_acked_seq = how much of OUR outbox the peer has confirmed.
"""

import sqlite3


_PAIRING_CURSOR_MAX = 9_223_372_036_854_775_806
_SQLITE_INT_MAX = 9_223_372_036_854_775_807


class InvalidPeerAckState(RuntimeError):
    """A persisted peer ACK cannot refer to this outbox history."""


class PeersRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_pair(
        self,
        device_id: str,
        device_name: str,
        token_hash: str,
        when: str,
        *,
        peer_cursor: int | None = None,
        commit: bool = True,
    ) -> None:
        if peer_cursor is not None and (
            isinstance(peer_cursor, bool)
            or not isinstance(peer_cursor, int)
            or not 0 <= peer_cursor <= _PAIRING_CURSOR_MAX
        ):
            raise ValueError(
                "pairing peer_cursor must be an integer between 0 and "
                f"{_PAIRING_CURSOR_MAX}"
            )
        if peer_cursor is None:
            # Legacy clients do not announce the first sequence retained in
            # their outbox. Preserve an existing cursor on re-pair, and let a
            # newly inserted row use the schema default of zero.
            self.conn.execute(
                "INSERT INTO sync_peers(device_id, device_name, token_hash, paired_at) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET device_name=excluded.device_name, "
                "token_hash=excluded.token_hash, paired_at=excluded.paired_at",
                (device_id, device_name, token_hash, when),
            )
        else:
            # A pairing client that announces its durable outbox base lets the
            # desktop distinguish an intentional prefix gap from data loss.
            # Reset exactly, including to a lower value after local app-data
            # restore; MAX() would permanently wedge the new stream.
            self.conn.execute(
                "INSERT INTO sync_peers"
                "(device_id, device_name, token_hash, paired_at, peer_cursor) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET device_name=excluded.device_name, "
                "token_hash=excluded.token_hash, paired_at=excluded.paired_at, "
                "peer_cursor=excluded.peer_cursor",
                (device_id, device_name, token_hash, when, peer_cursor),
            )
        if commit:
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

    def set_my_acked(self, device_id: str, seq: int, *, high_water: int) -> None:
        if (
            isinstance(seq, bool)
            or not isinstance(seq, int)
            or isinstance(high_water, bool)
            or not isinstance(high_water, int)
            or not 0 <= seq <= high_water <= _SQLITE_INT_MAX
        ):
            raise ValueError(
                "sync ack and high_water must be integers within durable "
                "outbox history"
            )
        self.conn.execute(
            "UPDATE sync_peers SET my_acked_seq = CASE "
            "WHEN my_acked_seq < 0 OR my_acked_seq > ? THEN ? "
            "ELSE MAX(my_acked_seq, ?) END WHERE device_id = ?",
            (high_water, seq, seq, device_id),
        )
        self.conn.commit()

    def min_my_acked(self, *, high_water: int) -> int | None:
        """Lowest my_acked_seq across all peers, or None if no peers paired.
        Events at or below this seq are confirmed by every peer (prunable).

        A value outside the durable outbox history fails closed. In particular,
        removing a lagging peer must never expose a poisoned, far-future ACK as
        a valid pruning cursor.
        """
        if (
            isinstance(high_water, bool)
            or not isinstance(high_water, int)
            or not 0 <= high_water <= _SQLITE_INT_MAX
        ):
            raise ValueError(
                "sync ack high_water must be an integer within SQLite range"
            )
        row = self.conn.execute(
            "SELECT MIN(my_acked_seq), MAX(my_acked_seq) FROM sync_peers"
        ).fetchone()
        if row[0] is None:
            return None
        try:
            minimum = int(row[0])
            maximum = int(row[1])
        except (TypeError, ValueError, OverflowError):
            raise InvalidPeerAckState(
                "peer sync ack is outside outbox history"
            ) from None
        if minimum < 0 or maximum > high_water:
            raise InvalidPeerAckState(
                "peer sync ack is outside outbox history"
            )
        return minimum

    def touch_last_seen(self, device_id: str, when: str) -> None:
        self.conn.execute(
            "UPDATE sync_peers SET last_seen_at = ? WHERE device_id = ?",
            (when, device_id),
        )
        self.conn.commit()

    def summary(self) -> dict:
        """Paired-device count and the most recent peer contact, for status
        display. No tokens or device identifiers are exposed."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, MAX(last_seen_at) AS last FROM sync_peers"
        ).fetchone()
        return {"paired_devices": int(row["n"]), "last_peer_sync_at": row["last"]}

    def list_peers(self) -> list[dict]:
        """Paired devices for the management UI. The token hash is never exposed."""
        rows = self.conn.execute(
            "SELECT device_id, device_name, paired_at, last_seen_at "
            "FROM sync_peers ORDER BY paired_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def unpair(self, device_id: str) -> bool:
        """Revoke a device: delete its row so the bearer token it holds no longer
        authenticates (by_token_hash will miss). Returns whether a row was removed."""
        cur = self.conn.execute(
            "DELETE FROM sync_peers WHERE device_id = ?", (device_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0
