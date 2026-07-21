"""S012 gates I1-I2: outbox pruning of peer-acked events."""

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import InvalidPeerAckState, PeersRepo


_SQLITE_INT_MAX = 9_223_372_036_854_775_807


@pytest.fixture
def cfg(tmp_path):
    return Config(device_id="01DESK", device_name="d", db_path=":memory:",
                  max_clip_bytes=1_048_576, poll_ms=500, vault_path=str(tmp_path / "v"))


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


def test_i1_prune_keeps_unacked(api, conn):
    for i in range(5):
        api.create_clip({"content": f"clip {i}"})   # 5 outbox events, seq 1..5
    outbox = OutboxRepo(conn)
    assert outbox.max_seq() == 5
    deleted = outbox.prune_acked(3)
    assert deleted == 3
    remaining = [e["seq"] for e in outbox.list_since(0)]
    assert remaining == [4, 5]


def test_i1_no_peer_no_prune(conn):
    peers = PeersRepo(conn)
    assert peers.min_my_acked(high_water=0) is None  # no peers -> don't prune
    assert OutboxRepo(conn).prune_acked(0) == 0


def test_i1_min_across_peers(conn):
    outbox = OutboxRepo(conn)
    for i in range(10):
        outbox.append("test", {"index": i}, "2026-06-13T10:00:00Z")
    high_water = outbox.sequence_high_water()
    peers = PeersRepo(conn)
    peers.upsert_pair("a", "A", "h1", "2026-06-13T10:00:00Z")
    peers.upsert_pair("b", "B", "h2", "2026-06-13T10:00:00Z")
    peers.set_my_acked("a", 10, high_water=high_water)
    peers.set_my_acked("b", 4, high_water=high_water)
    assert peers.min_my_acked(high_water=high_water) == 4


def test_i2_pull_after_prune_ok(api, conn):
    for i in range(5):
        api.create_clip({"content": f"clip {i}"})
    OutboxRepo(conn).prune_acked(3)
    # a peer pulling from 0 now sees only surviving events, no error
    out = OutboxRepo(conn).list_since(0)
    assert [e["seq"] for e in out] == [4, 5]


def test_i1_sequence_high_water_survives_empty_and_fully_pruned_outbox(conn):
    outbox = OutboxRepo(conn)
    assert outbox.sequence_high_water() == 0

    final_seq = outbox.append("test", {"safe": True}, "2026-06-13T10:00:00Z")
    assert outbox.prune_acked(final_seq) == 1

    assert outbox.max_seq() == 0
    assert outbox.sequence_high_water() == final_seq


@pytest.mark.parametrize(
    ("seq", "high_water"),
    [
        (_SQLITE_INT_MAX, 0),
        (2, 1),
        (-1, 1),
        (True, 1),
        (0, -1),
        (0, True),
        (0, _SQLITE_INT_MAX + 1),
    ],
)
def test_i1_peer_ack_rejects_values_outside_outbox_history(
    conn, seq, high_water
):
    peers = PeersRepo(conn)
    peers.upsert_pair("a", "A", "h1", "2026-06-13T10:00:00Z")

    with pytest.raises(ValueError, match="sync ack"):
        peers.set_my_acked("a", seq, high_water=high_water)

    assert peers.get("a")["my_acked_seq"] == 0


def test_i1_valid_ack_repairs_preexisting_ahead_value(conn):
    outbox = OutboxRepo(conn)
    seq = outbox.append("test", {"safe": True}, "2026-06-13T10:00:00Z")
    peers = PeersRepo(conn)
    peers.upsert_pair("a", "A", "h1", "2026-06-13T10:00:00Z")
    conn.execute(
        "UPDATE sync_peers SET my_acked_seq = ? WHERE device_id = ?",
        (_SQLITE_INT_MAX, "a"),
    )
    conn.commit()

    peers.set_my_acked("a", 0, high_water=outbox.sequence_high_water())

    assert peers.get("a")["my_acked_seq"] == 0
    peers.set_my_acked("a", seq, high_water=outbox.sequence_high_water())
    assert peers.get("a")["my_acked_seq"] == seq


def test_i1_min_ack_fails_closed_if_any_peer_is_ahead(conn):
    outbox = OutboxRepo(conn)
    high_water = outbox.append("test", {"safe": True}, "2026-06-13T10:00:00Z")
    peers = PeersRepo(conn)
    peers.upsert_pair("poisoned", "P", "h1", "2026-06-13T10:00:00Z")
    peers.upsert_pair("lagging", "L", "h2", "2026-06-13T10:00:00Z")
    conn.execute(
        "UPDATE sync_peers SET my_acked_seq = ? WHERE device_id = ?",
        (_SQLITE_INT_MAX, "poisoned"),
    )
    conn.commit()

    with pytest.raises(InvalidPeerAckState, match="outside outbox history"):
        peers.min_my_acked(high_water=high_water)

    # Removing the legitimate low-water peer must not turn the poisoned value
    # into a pruning cursor.
    assert peers.unpair("lagging") is True
    with pytest.raises(InvalidPeerAckState, match="outside outbox history"):
        peers.min_my_acked(high_water=high_water)
    assert outbox.list_since(0)[0]["seq"] == high_water
