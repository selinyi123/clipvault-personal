"""S008 gates K1-K6, K8: memory events emitted/applied + pulled."""

import logging

import pytest

from clipvault.api.handlers import Api
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.memory_repo import MemoryRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.sync import engine


@pytest.fixture
def cfg(tmp_path):
    return Config(device_id="01DESK", device_name="d", db_path=":memory:",
                  max_clip_bytes=1_048_576, poll_ms=500, vault_path=str(tmp_path / "v"))


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


def _outbox(conn):
    return OutboxRepo(conn).list_since(0)


def test_k1_create_memory_emits_upsert(api, conn):
    api.create_memory({"kind": "command", "text": "git fetch --all"})
    evs = _outbox(conn)
    assert any(e["kind"] == "memory_upsert" and e["payload"]["text"] == "git fetch --all" for e in evs)


def test_k2_delete_memory_emits_delete(api, conn):
    _, created = api.create_memory({"kind": "term", "text": "kustomize"})
    api.delete_memory(created["memory"]["id"])
    evs = _outbox(conn)
    assert any(e["kind"] == "memory_delete" and e["payload"]["text"] == "kustomize" for e in evs)


def test_k3_promote_emits_upsert(api, conn):
    _, obj = api.create_clip({"content": "kubectl get pods"})  # command
    api.promote_clip(obj["clip"]["id"])
    evs = _outbox(conn)
    assert any(e["kind"] == "memory_upsert" and e["payload"]["kind"] == "command" for e in evs)


def test_k4_apply_memory_upsert_idempotent(conn):
    ev = {"origin_device": "peer", "seq": 1, "kind": "memory_upsert",
          "ts": "t", "data": {"kind": "prompt", "text": "You are X", "label": None,
                              "pinned": False, "use_count": 7, "source": "manual"}}
    engine.apply_push(conn, "peer", [ev], service=None)
    # add a peer cursor so apply runs (no peer -> cursor 0, still applies seq>0)
    item = MemoryRepo(conn).by_kind_text("prompt", "You are X")
    assert item is not None and item.use_count == 7
    # replay with lower use_count must not regress
    ev2 = dict(ev); ev2["seq"] = 2
    ev2["data"] = dict(ev["data"]); ev2["data"]["use_count"] = 0
    engine.apply_push(conn, "peer", [ev2], service=None)
    assert MemoryRepo(conn).by_kind_text("prompt", "You are X").use_count == 7


def test_k5_apply_memory_delete(conn):
    MemoryRepo(conn).upsert("term", "doomed")
    ev = {"origin_device": "peer", "seq": 1, "kind": "memory_delete",
          "ts": "t", "data": {"kind": "term", "text": "doomed", "ts": "t"}}
    engine.apply_push(conn, "peer", [ev], service=None)
    assert MemoryRepo(conn).list(kind="term") == []


def test_k6_pull_returns_memory_events(api, conn):
    from clipvault.sync.pairing import Pairing, hash_token
    from clipvault.store.peers_repo import PeersRepo
    api.create_memory({"kind": "command", "text": "make build"})
    # simulate a paired peer pulling
    PeersRepo(conn).upsert_pair("phone", "P", hash_token("tok"), "2026-06-13T10:00:00Z")
    code, result = api.sync_pull("tok", {"since_seq": "0"})
    assert code == 200
    assert any(e["kind"] == "memory_upsert" for e in result["events"])


def test_k8_no_content_in_logs(api, caplog):
    with caplog.at_level(logging.DEBUG, logger="clipvault"):
        api.create_memory({"kind": "command", "text": "secretmemorywords"})
    assert "secretmemorywords" not in caplog.text
