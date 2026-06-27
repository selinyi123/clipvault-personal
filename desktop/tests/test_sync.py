"""S006 gates H1-H10: pairing, auth, push/pull event-log sync (SYNC-2).
The Android peer is simulated with the handler API directly (H1-H9) plus one
real-socket auth check (H2)."""

import threading
import http.client
import tempfile
import os

import pytest

from clipvault.api.handlers import Api
from clipvault.api import server as api_server
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.sync.pairing import Pairing, hash_token

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
PEER = "android-pixel"


@pytest.fixture
def cfg(tmp_path):
    return Config(device_id="01DESKTOPDEVICEID000000000", device_name="desktop-main",
                  db_path=":memory:", max_clip_bytes=1_048_576, poll_ms=500,
                  vault_path=str(tmp_path / "vault"))


@pytest.fixture
def api(conn, cfg):
    # deterministic pairing clock so codes don't expire mid-test
    return Api(ClipVaultService(conn, cfg), pairing=Pairing(clock=lambda: 0.0))


def _pair(api) -> str:
    code = api.pairing.mint_code()
    _, body = api.pair({"code": code, "device_id": PEER, "device_name": "Pixel"})
    return body["token"]


def _clip_new_event(seq, content, **kw):
    data = {
        "id": kw.get("id", f"01CLIP{seq:020d}"), "content": content,
        "content_hash": kw.get("hash", f"hash{seq}"), "content_type": "text",
        "is_secret": kw.get("is_secret", False), "secret_level": None, "secret_reasons": [],
        "source_device": PEER, "source_app": None,
        "created_at": "2026-06-13T10:00:00Z", "last_seen_at": "2026-06-13T10:00:00Z",
        "times_seen": 1, "pinned": False, "favorite": False, "deleted": False,
    }
    return {"origin_device": PEER, "seq": seq, "kind": "clip_new",
            "ts": "2026-06-13T10:00:00Z", "data": data}


def test_h1_pairing(api):
    code = api.pairing.mint_code()
    code2, _ = api.mint_pair_code()
    s, body = api.pair({"code": code, "device_id": PEER, "device_name": "Pixel"})
    assert s == 200 and len(body["token"]) > 20
    # token stored only as hash
    assert api.peers.by_token_hash(hash_token(body["token"]))["device_id"] == PEER
    # bad / reused code rejected
    assert api.pair({"code": "00000000", "device_id": PEER})[0] == 403
    assert api.pair({"code": code, "device_id": PEER})[0] == 403  # single use


def test_h1_expired_code(conn, cfg):
    clk = {"t": 0.0}
    api = Api(ClipVaultService(conn, cfg), pairing=Pairing(ttl_seconds=300, clock=lambda: clk["t"]))
    code = api.pairing.mint_code()
    clk["t"] = 301.0
    assert api.pair({"code": code, "device_id": PEER})[0] == 403


def test_pair_rate_limit_locks_then_recovers():
    clk = {"t": 0.0}
    p = Pairing(ttl_seconds=300, clock=lambda: clk["t"], max_attempts=5,
                attempt_window_seconds=60.0)
    code = p.mint_code()
    for _ in range(5):  # exhaust the attempt budget with bad codes
        assert p.redeem("00000000") is None
    assert p.locked()
    assert p.redeem(code) is None  # even the valid code is refused while locked
    clk["t"] = 61.0                # window drains
    assert not p.locked()
    assert p.redeem(code) is not None  # valid code still usable after recovery


def test_pair_success_resets_failure_counter():
    clk = {"t": 0.0}
    p = Pairing(clock=lambda: clk["t"], max_attempts=5, attempt_window_seconds=60.0)
    for _ in range(4):  # below the threshold
        assert p.redeem("00000000") is None
    code = p.mint_code()
    assert p.redeem(code) is not None  # success clears prior failures
    for _ in range(4):  # a fresh budget, so still not locked
        assert p.redeem("00000000") is None
    assert not p.locked()


def test_h2_auth_required(api):
    assert api.sync_pull(None, {})[0] == 401
    assert api.sync_push("wrong-token", {"events": []})[0] == 401
    token = _pair(api)
    assert api.sync_pull(token, {"since_seq": "0"})[0] == 200


def test_h2_bad_since_seq_returns_400(api):
    token = _pair(api)
    assert api.sync_pull(token, {"since_seq": "abc"})[0] == 400
    assert api.sync_pull(token, {"since_seq": "-1"})[0] == 400


def test_h3_push_clip_new_lands(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, "hello from phone", hash="abc123")
    s, body = api.sync_push(token, {"events": [ev]})
    assert s == 200 and body["acked_upto"] == 1
    clip = ClipsRepo(conn).get_by_hash("abc123")
    assert clip is not None and clip.content == "hello from phone"
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1  # obsidian written


def test_h4_push_idempotent(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, "dup phone clip", hash="dup1")
    api.sync_push(token, {"events": [ev]})
    s, body = api.sync_push(token, {"events": [ev]})  # replay
    assert body["acked_upto"] == 1
    assert conn.execute("SELECT COUNT(*) FROM clips WHERE content_hash='dup1'").fetchone()[0] == 1
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1  # not rewritten


def test_h5_push_secret_quarantined_not_propagated(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, FAKE_AWS_KEY, hash="seekrit")
    api.sync_push(token, {"events": [ev]})
    clip = ClipsRepo(conn).get_by_hash("seekrit")
    assert clip.is_secret is True
    assert not ClipsRepo(conn).fts_contains(clip.id)             # not indexed
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0  # no echo
    assert list((tmp_path / "vault").rglob("*.md")) == []


def test_h6_pull_returns_local_public_clips(api, conn):
    token = _pair(api)
    api.create_clip({"content": "desktop clip one"})
    api.create_clip({"content": FAKE_AWS_KEY})          # secret -> not in outbox
    api.create_clip({"content": "desktop clip two"})
    s, body = api.sync_pull(token, {"since_seq": "0"})
    kinds = [e["kind"] for e in body["events"]]
    contents = [e["payload"]["content"] for e in body["events"]]
    assert kinds == ["clip_new", "clip_new"]
    assert "desktop clip one" in contents and "desktop clip two" in contents
    assert FAKE_AWS_KEY not in contents


def test_h7_clip_meta_lww(api, conn):
    token = _pair(api)
    # land a clip from the peer
    api.sync_push(token, {"events": [_clip_new_event(1, "meta target", hash="mt1")]})
    clip = ClipsRepo(conn).get_by_hash("mt1")
    # peer deletes it at t=20
    meta_del = {"origin_device": PEER, "seq": 2, "kind": "clip_meta",
                "ts": "2026-06-13T10:20:00Z",
                "data": {"content_hash": "mt1", "patch": {"deleted": True},
                         "ts": "2026-06-13T10:20:00Z"}}
    api.sync_push(token, {"events": [meta_del]})
    assert ClipsRepo(conn).get(clip.id).deleted is True
    assert not ClipsRepo(conn).fts_contains(clip.id)
    # a STALE un-delete at t=10 must not resurrect
    stale = {"origin_device": PEER, "seq": 3, "kind": "clip_meta",
             "ts": "2026-06-13T10:10:00Z",
             "data": {"content_hash": "mt1", "patch": {"deleted": False},
                      "ts": "2026-06-13T10:10:00Z"}}
    api.sync_push(token, {"events": [stale]})
    assert ClipsRepo(conn).get(clip.id).deleted is True  # unchanged


def test_h7_clip_meta_pins_and_favorites(api, conn):
    # A peer's clip_meta carrying pinned/favorite must mirror onto the desktop
    # clip, not just the deleted flag (the Android cache consumes the same patch).
    token = _pair(api)
    api.sync_push(token, {"events": [_clip_new_event(1, "pin me", hash="pf1")]})
    clip = ClipsRepo(conn).get_by_hash("pf1")
    assert not clip.pinned and not clip.favorite
    meta = {"origin_device": PEER, "seq": 2, "kind": "clip_meta",
            "ts": "2026-06-13T10:20:00Z",
            "data": {"content_hash": "pf1",
                     "patch": {"pinned": True, "favorite": True},
                     "ts": "2026-06-13T10:20:00Z"}}
    api.sync_push(token, {"events": [meta]})
    row = ClipsRepo(conn).get(clip.id)
    assert row.pinned is True and row.favorite is True


def test_h7_clip_meta_pin_lww_rejects_stale(api, conn):
    # Same-field LWW: an older-ts un-pin must not override a newer pin.
    token = _pair(api)
    api.sync_push(token, {"events": [_clip_new_event(1, "lww pin", hash="pf2")]})
    clip = ClipsRepo(conn).get_by_hash("pf2")
    api.sync_push(token, {"events": [{"origin_device": PEER, "seq": 2, "kind": "clip_meta",
        "ts": "2026-06-13T10:20:00Z",
        "data": {"content_hash": "pf2", "patch": {"pinned": True},
                 "ts": "2026-06-13T10:20:00Z"}}]})
    assert ClipsRepo(conn).get(clip.id).pinned is True
    api.sync_push(token, {"events": [{"origin_device": PEER, "seq": 3, "kind": "clip_meta",
        "ts": "2026-06-13T10:10:00Z",
        "data": {"content_hash": "pf2", "patch": {"pinned": False},
                 "ts": "2026-06-13T10:10:00Z"}}]})
    assert ClipsRepo(conn).get(clip.id).pinned is True  # stale un-pin ignored


def test_h7_local_patch_emits_clip_meta_for_pull(api, conn):
    # Desktop->phone contract: patching pin/favorite must emit a clip_meta event
    # that build_pull returns under the `payload` key with the patch fields the
    # Android applyClipMeta reads. Guards the desktop<->Android wire shape.
    token = _pair(api)
    _, obj = api.create_clip({"content": "pull my pin"})
    cid = obj["clip"]["id"]
    chash = ClipsRepo(conn).get(cid).content_hash
    api.patch_clip(cid, {"pinned": True, "favorite": True})
    _, pulled = api.sync_pull(token, {"since_seq": "0"})
    metas = [e for e in pulled["events"] if e["kind"] == "clip_meta"]
    assert metas, "patch must emit a clip_meta event for peers"
    payload = metas[-1]["payload"]
    assert payload["content_hash"] == chash
    assert payload["patch"].get("pinned") is True
    assert payload["patch"].get("favorite") is True


def test_h8_cursor_resume(api, conn):
    token = _pair(api)
    for i in range(5):
        api.create_clip({"content": f"clip number {i}"})
    s, first = api.sync_pull(token, {"since_seq": "0"})
    # pull again from the last seq -> no repeats
    last = first["next_seq"]
    _, second = api.sync_pull(token, {"since_seq": str(last)})
    assert second["events"] == []
    # all 5 were delivered exactly once
    assert len(first["events"]) == 5


def test_h8_push_gap_does_not_advance_ack(api, conn):
    token = _pair(api)
    # Event 2 can be applied idempotently, but ack must remain at 0 because seq 1 is missing.
    _, first = api.sync_push(token, {"events": [_clip_new_event(2, "gap two", hash="gap2")]})
    assert first["acked_upto"] == 0
    assert ClipsRepo(conn).get_by_hash("gap2") is not None
    # When seq 1 arrives later and seq 2 is replayed, the contiguous ack can advance to 2.
    _, second = api.sync_push(token, {"events": [
        _clip_new_event(1, "gap one", hash="gap1"),
        _clip_new_event(2, "gap two", hash="gap2"),
    ]})
    assert second["acked_upto"] == 2


def test_h9_local_public_in_outbox_secret_not(api, conn):
    api.create_clip({"content": "public goes to outbox"})
    api.create_clip({"content": FAKE_AWS_KEY})
    rows = OutboxRepo(conn).list_since(0)
    assert len(rows) == 1 and rows[0]["payload"]["content"] == "public goes to outbox"


def test_status_reports_paired_device_summary(api, conn):
    # Release-state display: status surfaces how many devices are paired and the
    # most recent peer contact, without exposing any device identifiers.
    assert api.status()[1]["sync"] == {"paired_devices": 0, "last_peer_sync_at": None}
    token = _pair(api)
    assert api.status()[1]["sync"]["paired_devices"] == 1
    # a pull updates last_seen, which then shows as the most recent sync
    api.sync_pull(token, {"since_seq": "0"})
    sync = api.status()[1]["sync"]
    assert sync["paired_devices"] == 1
    assert sync["last_peer_sync_at"] is not None


def test_unpair_revokes_device_access(api):
    token = _pair(api)
    # listed for management, without exposing the token hash
    peers = api.list_peers()[1]["peers"]
    assert len(peers) == 1 and peers[0]["device_id"] == PEER
    assert all("token" not in key for key in peers[0])
    assert api.sync_pull(token, {"since_seq": "0"})[0] == 200  # works while paired
    # revoke: the bearer token must stop authenticating immediately
    assert api.unpair(PEER)[1]["unpaired"] is True
    assert api.sync_pull(token, {"since_seq": "0"})[0] == 401
    assert api.sync_push(token, {"events": []})[0] == 401
    assert api.list_peers()[1]["peers"] == []


def test_unpair_unknown_device_returns_404(api):
    assert api.unpair("not-a-device")[0] == 404


def test_h2_socket_auth_end_to_end(cfg):
    """Real socket: unauthorized sync push is 401; management route from
    loopback still works on a fresh connection after the rejected request."""
    import clipvault.store.db as db
    t = tempfile.mkdtemp()
    cfg.db_path = os.path.join(t, "cv.db")
    cfg.port = 8795
    stop = threading.Event()
    threading.Thread(target=api_server.serve, args=(cfg, stop), daemon=True).start()
    import time
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8795, timeout=5)
        c.request("POST", "/api/sync/push", body="{}",
                  headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 401  # no token
        c.close()

        c = http.client.HTTPConnection("127.0.0.1", 8795, timeout=5)
        c.request("GET", "/api/health")
        assert c.getresponse().status == 200
        c.close()
    finally:
        stop.set()
        time.sleep(0.6)
