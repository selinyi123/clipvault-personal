"""S012 gates I1-I2: outbox pruning of peer-acked events."""

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo


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
    assert peers.min_my_acked() is None        # no peers -> don't prune
    assert OutboxRepo(conn).prune_acked(0) == 0


def test_i1_min_across_peers(conn):
    peers = PeersRepo(conn)
    peers.upsert_pair("a", "A", "h1", "2026-06-13T10:00:00Z")
    peers.upsert_pair("b", "B", "h2", "2026-06-13T10:00:00Z")
    peers.set_my_acked("a", 10)
    peers.set_my_acked("b", 4)
    assert peers.min_my_acked() == 4           # prune only what BOTH have


def test_i2_pull_after_prune_ok(api, conn):
    for i in range(5):
        api.create_clip({"content": f"clip {i}"})
    OutboxRepo(conn).prune_acked(3)
    # a peer pulling from 0 now sees only surviving events, no error
    out = OutboxRepo(conn).list_since(0)
    assert [e["seq"] for e in out] == [4, 5]
