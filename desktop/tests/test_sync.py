"""S006 gates H1-H10: pairing, auth, push/pull event-log sync (SYNC-2).
The Android peer is simulated with the handler API directly (H1-H9) plus one
real-socket auth check (H2)."""

import threading
import http.client
import json
import tempfile
import os

import pytest

from clipvault.api.handlers import Api
from clipvault.api import server as api_server
from clipvault.config import Config
from clipvault.core import normalize
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.sync import engine as sync_engine
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
        "content_hash": kw.get("content_hash", normalize.content_hash(content)),
        "content_type": kw.get("content_type", "text"),
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


def test_h1_pair_rejects_unsafe_device_id(api):
    code = api.pairing.mint_code()
    for device_id in (
        "",
        "../phone",
        "x" * 81,
        'android-1" autofocus onfocus=alert(1)',
        ["android-phone"],
    ):
        status, body = api.pair({
            "code": code,
            "device_id": device_id,
            "device_name": "Pixel",
        })
        assert status == 400
        assert body["error"]["code"] == "bad_request"
    # Validation happens before code redemption, so a corrected URL-safe id can
    # still use the same one-time code.
    assert api.pair({"code": code, "device_id": "android_phone-01"})[0] == 200


def test_h1_pair_normalizes_device_name_metadata(api):
    code = api.pairing.mint_code()
    status, body = api.pair({
        "code": code,
        "device_id": PEER,
        "device_name": "  张三的 Pixel 8  ",
    })

    assert status == 200 and len(body["token"]) > 20
    peer = api.list_peers()[1]["peers"][0]
    assert peer["device_name"] == "张三的 Pixel 8"


def test_h1_pair_defaults_blank_device_name(api):
    code = api.pairing.mint_code()

    status, _ = api.pair({"code": code, "device_id": PEER, "device_name": "   "})

    assert status == 200
    assert api.list_peers()[1]["peers"][0]["device_name"] == "device"


def test_h1_pair_rejects_unsafe_device_name_without_redeeming_code(api):
    code = api.pairing.mint_code()
    for device_name in (
        ["Pixel"],
        "Pixel\n8",
        "x" * 81,
    ):
        status, body = api.pair({
            "code": code,
            "device_id": PEER,
            "device_name": device_name,
        })
        assert status == 400
        assert body["error"]["code"] == "bad_request"

    # Validation happens before code redemption, so a corrected display name can
    # still use the same one-time pairing code.
    assert api.pair({"code": code, "device_id": PEER, "device_name": "Pixel 8"})[0] == 200


def test_h1_pair_rate_limited_after_repeated_bad_codes(api):
    # /api/pair is LAN-reachable; repeated bad codes must lock out (429), not just
    # 403 forever, to bound brute-force and flood of the single-threaded server.
    for _ in range(10):
        assert api.pair({"code": "00000000", "device_id": PEER})[0] == 403
    assert api.pair({"code": "00000000", "device_id": PEER})[0] == 429


def test_h1_rate_limit_clears_after_window(conn, cfg):
    clk = {"t": 0.0}
    api = Api(ClipVaultService(conn, cfg),
              pairing=Pairing(clock=lambda: clk["t"], max_failures=3, lockout_seconds=60))
    for _ in range(3):
        assert api.pair({"code": "00000000", "device_id": PEER})[0] == 403
    assert api.pair({"code": "00000000", "device_id": PEER})[0] == 429
    clk["t"] = 61.0  # window elapsed
    code = api.pairing.mint_code()
    assert api.pair({"code": code, "device_id": PEER})[0] == 200  # pairing works again


def test_h1_successful_pairing_resets_consecutive_failures(conn, cfg):
    clk = {"t": 0.0}
    api = Api(ClipVaultService(conn, cfg),
              pairing=Pairing(clock=lambda: clk["t"], max_failures=3, lockout_seconds=60))
    for _ in range(2):
        assert api.pair({"code": "not-a-code", "device_id": PEER})[0] == 403
    code = api.pairing.mint_code()
    assert api.pair({"code": code, "device_id": PEER})[0] == 200

    # The next failures start a new consecutive window; old failures before the
    # successful pairing must not make a legitimate device hit 429 early.
    for _ in range(3):
        assert api.pair({"code": "not-a-code", "device_id": PEER})[0] == 403
    assert api.pair({"code": "not-a-code", "device_id": PEER})[0] == 429


def test_h1_expired_code(conn, cfg):
    clk = {"t": 0.0}
    api = Api(ClipVaultService(conn, cfg), pairing=Pairing(ttl_seconds=300, clock=lambda: clk["t"]))
    code = api.pairing.mint_code()
    clk["t"] = 301.0
    assert api.pair({"code": code, "device_id": PEER})[0] == 403


def test_h2_auth_required(api):
    assert api.sync_pull(None, {})[0] == 401
    assert api.sync_push("wrong-token", {"events": []})[0] == 401
    token = _pair(api)
    assert api.sync_pull(token, {"since_seq": "0"})[0] == 200


def test_h2_sync_push_rejects_non_array_events(api, caplog):
    token = _pair(api)
    with caplog.at_level("ERROR", logger="clipvault.sync"):
        status, body = api.sync_push(token, {"events": "x" * 1000})
    assert status == 400
    assert body["error"]["message"] == "events must be an array"
    assert "sync event without integer seq" not in caplog.text


def test_h2_sync_push_rejects_batches_above_android_limit(api):
    token = _pair(api)
    events = [_clip_new_event(i + 1, f"clip {i}") for i in range(101)]
    status, body = api.sync_push(token, {"events": events})
    assert status == 400
    assert "at most 100" in body["error"]["message"]


def test_h2_bad_since_seq_returns_400(api):
    token = _pair(api)
    assert api.sync_pull(token, {"since_seq": "abc"})[0] == 400
    assert api.sync_pull(token, {"since_seq": "-1"})[0] == 400


def test_h3_push_clip_new_lands(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, "hello from phone")
    s, body = api.sync_push(token, {"events": [ev]})
    assert s == 200 and body["acked_upto"] == 1
    clip = ClipsRepo(conn).get_by_hash(normalize.content_hash("hello from phone"))
    assert clip is not None and clip.content == "hello from phone"
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1  # obsidian written


def test_h4_push_idempotent(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, "dup phone clip")
    api.sync_push(token, {"events": [ev]})
    s, body = api.sync_push(token, {"events": [ev]})  # replay
    assert body["acked_upto"] == 1
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("dup phone clip")) is not None
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1  # not rewritten


def test_h4_duplicate_seq_in_same_batch_is_applied_once(api, conn):
    token = _pair(api)
    events = [
        _clip_new_event(1, "first seq payload", id="01DUPSEQ000000000000001"),
        _clip_new_event(1, "second seq payload", id="01DUPSEQ000000000000002"),
    ]

    status, body = api.sync_push(token, {"events": events})

    assert status == 200 and body["acked_upto"] == 1
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("first seq payload")) is not None
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("second seq payload")) is None


def test_h5_push_secret_quarantined_not_propagated(api, conn, tmp_path):
    token = _pair(api)
    ev = _clip_new_event(1, FAKE_AWS_KEY)
    api.sync_push(token, {"events": [ev]})
    clip = ClipsRepo(conn).get_by_hash(normalize.content_hash(FAKE_AWS_KEY))
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
    content_hash = normalize.content_hash("meta target")
    api.sync_push(token, {"events": [_clip_new_event(1, "meta target")]})
    clip = ClipsRepo(conn).get_by_hash(content_hash)
    # peer deletes it at t=20
    meta_del = {"origin_device": PEER, "seq": 2, "kind": "clip_meta",
                "ts": "2026-06-13T10:20:00Z",
                "data": {"content_hash": content_hash, "patch": {"deleted": True},
                         "ts": "2026-06-13T10:20:00Z"}}
    api.sync_push(token, {"events": [meta_del]})
    assert ClipsRepo(conn).get(clip.id).deleted is True
    assert not ClipsRepo(conn).fts_contains(clip.id)
    # a STALE un-delete at t=10 must not resurrect
    stale = {"origin_device": PEER, "seq": 3, "kind": "clip_meta",
             "ts": "2026-06-13T10:10:00Z",
             "data": {"content_hash": content_hash, "patch": {"deleted": False},
                      "ts": "2026-06-13T10:10:00Z"}}
    api.sync_push(token, {"events": [stale]})
    assert ClipsRepo(conn).get(clip.id).deleted is True  # unchanged


def test_h7_clip_meta_pins_and_favorites(api, conn):
    # A peer's clip_meta carrying pinned/favorite must mirror onto the desktop
    # clip, not just the deleted flag (the Android cache consumes the same patch).
    token = _pair(api)
    content_hash = normalize.content_hash("pin me")
    api.sync_push(token, {"events": [_clip_new_event(1, "pin me")]})
    clip = ClipsRepo(conn).get_by_hash(content_hash)
    assert not clip.pinned and not clip.favorite
    meta = {"origin_device": PEER, "seq": 2, "kind": "clip_meta",
            "ts": "2026-06-13T10:20:00Z",
            "data": {"content_hash": content_hash,
                     "patch": {"pinned": True, "favorite": True},
                     "ts": "2026-06-13T10:20:00Z"}}
    api.sync_push(token, {"events": [meta]})
    row = ClipsRepo(conn).get(clip.id)
    assert row.pinned is True and row.favorite is True


def test_h7_clip_meta_pin_lww_rejects_stale(api, conn):
    # Same-field LWW: an older-ts un-pin must not override a newer pin.
    token = _pair(api)
    content_hash = normalize.content_hash("lww pin")
    api.sync_push(token, {"events": [_clip_new_event(1, "lww pin")]})
    clip = ClipsRepo(conn).get_by_hash(content_hash)
    api.sync_push(token, {"events": [{"origin_device": PEER, "seq": 2, "kind": "clip_meta",
        "ts": "2026-06-13T10:20:00Z",
        "data": {"content_hash": content_hash, "patch": {"pinned": True},
                 "ts": "2026-06-13T10:20:00Z"}}]})
    assert ClipsRepo(conn).get(clip.id).pinned is True
    api.sync_push(token, {"events": [{"origin_device": PEER, "seq": 3, "kind": "clip_meta",
        "ts": "2026-06-13T10:10:00Z",
        "data": {"content_hash": content_hash, "patch": {"pinned": False},
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


def test_h7_clip_meta_per_field_lww(api, conn):
    # v1.8: a newer change to one field must not be masked by an older change to a
    # different field that happened to bump a shared timestamp.
    token = _pair(api)
    content_hash = normalize.content_hash("field lww")
    api.sync_push(token, {"events": [_clip_new_event(1, "field lww")]})
    clip = ClipsRepo(conn).get_by_hash(content_hash)

    def meta(seq, patch, ts):
        return {"origin_device": PEER, "seq": seq, "kind": "clip_meta", "ts": ts,
                "data": {"content_hash": content_hash, "patch": patch, "ts": ts}}

    api.sync_push(token, {"events": [meta(2, {"pinned": True}, "2026-06-13T10:10:00Z")]})
    api.sync_push(token, {"events": [meta(3, {"favorite": True}, "2026-06-13T10:20:00Z")]})
    row = ClipsRepo(conn).get(clip.id)
    assert row.pinned is True and row.favorite is True
    # un-pin at t=15 is newer than the pin (t=10); the favorite's t=20 must not mask it
    api.sync_push(token, {"events": [meta(4, {"pinned": False}, "2026-06-13T10:15:00Z")]})
    row = ClipsRepo(conn).get(clip.id)
    assert row.pinned is False   # the fix: independent per-field timestamps
    assert row.favorite is True  # untouched


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


def test_h8_pull_response_byte_budget_pages_without_skipping(conn):
    outbox = OutboxRepo(conn)
    when = "2026-06-13T10:00:00Z"
    first_seq = outbox.append("clip_new", {"content": "a" * 200, "content_hash": "pull-budget-1"}, when)
    second_seq = outbox.append("clip_new", {"content": "b" * 200, "content_hash": "pull-budget-2"}, when)

    first_event_size = sync_engine._event_wire_size(outbox.list_since(0, limit=1)[0])
    first = sync_engine.build_pull(conn, since_seq=0, max_bytes=first_event_size)

    assert [event["seq"] for event in first["events"]] == [first_seq]
    assert first["next_seq"] == first_seq
    assert first["has_more"] is True

    second_event_size = sync_engine._event_wire_size(outbox.list_since(first["next_seq"], limit=1)[0])
    second = sync_engine.build_pull(conn, since_seq=first["next_seq"], max_bytes=second_event_size)
    assert [event["seq"] for event in second["events"]] == [second_seq]
    assert second["next_seq"] == second_seq
    assert second["has_more"] is False


def test_h8_pull_continues_across_bounded_sqlite_fetch_pages(conn):
    outbox = OutboxRepo(conn)
    expected = []
    for index in range(17):
        expected.append(
            outbox.append(
                "clip_new",
                {"content": f"page-row-{index}", "content_hash": f"page-hash-{index}"},
                "2026-07-13T00:00:00Z",
            )
        )

    since = 0
    pages = []
    received = []
    while True:
        page = sync_engine.build_pull(conn, since_seq=since)
        pages.append(len(page["events"]))
        received.extend(event["seq"] for event in page["events"])
        since = page["next_seq"]
        if not page["has_more"]:
            break

    assert pages == [8, 8, 1]
    assert received == expected
    assert since == expected[-1]


def test_h8_pull_single_event_over_response_budget_fails_without_skipping(conn, caplog):
    outbox = OutboxRepo(conn)
    when = "2026-06-13T10:00:00Z"
    seq = outbox.append("clip_new", {"content": "oversized-content", "content_hash": "pull-too-big"}, when)

    with pytest.raises(sync_engine.SyncPullEventTooLarge) as exc_info:
        sync_engine.build_pull(conn, since_seq=0, max_bytes=10)

    assert exc_info.value.seq == seq
    assert outbox.list_since(0, limit=1)[0]["seq"] == seq
    assert "oversized-content" not in caplog.text


def test_h8_pull_accepts_max_clip_with_worst_case_json_escaping(conn):
    content = "\0" * normalize.DEFAULT_MAX_CLIP_BYTES
    outbox = OutboxRepo(conn)
    seq = outbox.append(
        "clip_new",
        {
            "id": "01J00000000000000000000000",
            "content": content,
            "content_hash": "a" * 64,
            "content_type": "text",
            "is_secret": False,
            "secret_level": None,
            "secret_reasons": [],
            "source_device": "desktop-test",
            "source_app": None,
            "created_at": "2026-07-13T00:00:00Z",
            "last_seen_at": "2026-07-13T00:00:00Z",
            "times_seen": 1,
            "pinned": False,
            "favorite": False,
            "deleted": False,
        },
        "2026-07-13T00:00:00Z",
    )
    event = outbox.list_since(0, limit=1)[0]
    event_bytes = sync_engine._event_wire_size(event)

    page = sync_engine.build_pull(conn, since_seq=0)
    response_bytes = len(
        json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

    assert event_bytes > 4 * 1024 * 1024
    assert event_bytes <= sync_engine.SYNC_PULL_RESPONSE_BYTES
    assert response_bytes <= sync_engine.SYNC_PULL_HTTP_RESPONSE_BYTES
    assert [item["seq"] for item in page["events"]] == [seq]
    assert page["next_seq"] == seq
    assert page["has_more"] is False


def test_h8_pull_single_event_over_response_budget_returns_413(api, conn):
    token = _pair(api)
    outbox = OutboxRepo(conn)
    when = "2026-06-13T10:00:00Z"
    seq = outbox.append(
        "clip_new",
        {"content": "x" * sync_engine.SYNC_PULL_HTTP_RESPONSE_BYTES, "content_hash": "pull-api-too-big"},
        when,
    )
    assert sync_engine._event_wire_size(outbox.list_since(0, limit=1)[0]) > (
        sync_engine.SYNC_PULL_HTTP_RESPONSE_BYTES
    )

    status, body = api.sync_pull(token, {"since_seq": "0"})

    assert status == 413
    assert body["error"]["code"] == "sync_event_too_large"
    assert f"seq={seq}" in body["error"]["message"]
    assert outbox.list_since(0, limit=1)[0]["seq"] == seq


def test_h8_push_gap_does_not_advance_ack(api, conn):
    token = _pair(api)
    # Event 2 can be applied idempotently, but ack must remain at 0 because seq 1 is missing.
    _, first = api.sync_push(token, {"events": [_clip_new_event(2, "gap two")]})
    assert first["acked_upto"] == 0
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("gap two")) is not None
    # When seq 1 arrives later and seq 2 is replayed, the contiguous ack can advance to 2.
    _, second = api.sync_push(token, {"events": [
        _clip_new_event(1, "gap one"),
        _clip_new_event(2, "gap two"),
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
    assert api.status()[1]["sync"] == {
        "paired_devices": 0,
        "last_peer_sync_at": None,
        "blocked_pull": None,
    }
    token = _pair(api)
    assert api.status()[1]["sync"]["paired_devices"] == 1
    # a pull updates last_seen, which then shows as the most recent sync
    api.sync_pull(token, {"since_seq": "0"})
    sync = api.status()[1]["sync"]
    assert sync["paired_devices"] == 1
    assert sync["last_peer_sync_at"] is not None
    assert sync["blocked_pull"] is None


def test_status_reports_oversized_pull_block_without_content(api, conn):
    _pair(api)
    outbox = OutboxRepo(conn)
    secret_text = "status-visible-content-must-not-leak"
    oversized_content = secret_text + ("x" * sync_engine.SYNC_PULL_HTTP_RESPONSE_BYTES)
    seq = outbox.append(
        "clip_new",
        {"content": oversized_content, "content_hash": "blocked-status"},
        "2026-06-13T10:00:00Z",
    )

    blocked = sync_engine.pull_blocked_summary(conn)

    assert blocked is not None
    assert blocked["code"] == "sync_event_too_large"
    assert blocked["blocked_devices"] == 1
    assert blocked["first_seq"] == seq
    assert blocked["event_bytes"] > blocked["max_bytes"]
    assert blocked["max_bytes"] == sync_engine.SYNC_PULL_RESPONSE_BYTES

    status = api.status()[1]
    assert status["sync"]["blocked_pull"]["code"] == "sync_event_too_large"
    assert status["sync"]["blocked_pull"]["first_seq"] == seq
    assert secret_text not in json.dumps(status, ensure_ascii=False)


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


def test_h10_malformed_event_does_not_wedge_batch(api, conn):
    # One malformed event from a version-skewed/buggy peer must not crash the
    # whole push or drop the valid events around it; it is acked as an
    # unprocessable no-op so it is not resent forever.
    token = _pair(api)
    events = [
        _clip_new_event(1, "before bad"),
        {"origin_device": PEER, "seq": 2, "kind": "clip_meta", "data": {}},  # missing keys
        _clip_new_event(3, "after bad"),
    ]
    s, body = api.sync_push(token, {"events": events})
    assert s == 200 and body["acked_upto"] == 3          # malformed #2 acked, no wedge
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("before bad")) is not None
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("after bad")) is not None


def test_h10_integrity_conflict_event_does_not_wedge_batch(api, conn):
    # A seq-valid event that violates local DB constraints is permanently bad.
    # Ack it as a no-op so the peer does not retry it forever, but keep applying
    # later valid events in the same batch.
    token = _pair(api)
    duplicate_id = "01SAMEID000000000000001"
    events = [
        _clip_new_event(1, "before conflict", id=duplicate_id),
        _clip_new_event(2, "conflicting id", id=duplicate_id),
        _clip_new_event(3, "after conflict", id="01SAMEID000000000000003"),
    ]

    s, body = api.sync_push(token, {"events": events})

    assert s == 200 and body["acked_upto"] == 3
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("before conflict")) is not None
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("conflicting id")) is None
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("after conflict")) is not None


def test_h10_event_without_seq_is_dropped(api, conn):
    token = _pair(api)
    events = [
        {"origin_device": PEER, "kind": "clip_new", "data": {}},  # no seq -> unorderable
        _clip_new_event(1, "valid one"),
    ]
    s, body = api.sync_push(token, {"events": events})
    assert s == 200 and body["acked_upto"] == 1
    assert ClipsRepo(conn).get_by_hash(normalize.content_hash("valid one")) is not None


def test_h10_malformed_clip_contract_is_acked_without_landing(api, conn, caplog):
    token = _pair(api)
    events = []
    for seq, content in enumerate(
        ("bad hash", "bad time", "bad kind", "bad count", "not normalized "), start=1
    ):
        event = _clip_new_event(seq, content)
        events.append(event)
    events[0]["data"]["content_hash"] = "0" * 64
    events[1]["data"]["created_at"] = "not-a-time"
    events[2]["data"]["content_type"] = "unknown"
    events[3]["data"]["times_seen"] = True

    with caplog.at_level("ERROR", logger="clipvault.sync"):
        status, body = api.sync_push(token, {"events": events})

    assert status == 200 and body["acked_upto"] == len(events)
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert "not normalized" not in caplog.text


def test_h10_remote_frontmatter_metadata_injection_is_acked_noop(api, conn, caplog):
    token = _pair(api)
    marker = "must-not-enter-log-or-yaml"
    event = _clip_new_event(1, "safe body")
    event["data"]["source_app"] = f"phone\n{marker}: true"

    with caplog.at_level("ERROR", logger="clipvault.sync"):
        status, body = api.sync_push(token, {"events": [event]})

    assert status == 200 and body["acked_upto"] == 1
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert marker not in caplog.text


def test_h10_untrusted_event_kind_is_not_written_to_logs(api, conn, caplog):
    token = _pair(api)
    marker = "kind-must-not-leak-sensitive-payload"
    event = {"origin_device": PEER, "seq": 1, "kind": marker, "data": {}}

    with caplog.at_level("ERROR", logger="clipvault.sync"):
        status, body = api.sync_push(token, {"events": [event]})

    assert status == 200 and body["acked_upto"] == 1
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert marker not in caplog.text


def test_h10_nested_malformed_event_data_is_acked_without_db_or_log_leak(api, conn, caplog):
    token = _pair(api)
    marker = "nested-sensitive-marker-must-not-leak"
    valid_ts = "2026-06-13T10:00:00Z"
    events = [
        {"origin_device": PEER, "seq": 1, "kind": "memory_upsert", "data": {
            "kind": [marker], "text": "safe", "label": None,
            "pinned": False, "use_count": 0, "source": "manual",
        }},
        {"origin_device": PEER, "seq": 2, "kind": "memory_upsert", "data": {
            "kind": "term", "text": {marker: "value"}, "label": None,
            "pinned": False, "use_count": 0, "source": "manual",
        }},
        {"origin_device": PEER, "seq": 3, "kind": "clip_meta", "data": {
            "content_hash": [marker], "patch": {"pinned": True}, "ts": valid_ts,
        }},
        {"origin_device": PEER, "seq": 4, "kind": "memory_delete", "data": {
            "kind": "term", "text": {marker: "value"}, "ts": valid_ts,
        }},
        {"origin_device": PEER, "seq": 5, "kind": "clip_meta", "data": {
            "content_hash": "0" * 64,
            "patch": {"pinned": {marker: True}},
            "ts": valid_ts,
        }},
        {"origin_device": PEER, "seq": 6, "kind": "memory_upsert", "data": {
            "kind": "term", "text": "safe", "label": {marker: "value"},
            "pinned": False, "use_count": 0, "source": "manual",
        }},
    ]

    with caplog.at_level("ERROR", logger="clipvault.sync"):
        status, body = api.sync_push(token, {"events": events})

    assert status == 200 and body["acked_upto"] == len(events)
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM clip_meta_ts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memory_meta_ts").fetchone()[0] == 0
    assert marker not in caplog.text


def test_remote_db_intent_survives_crash_before_external_write(api, conn, monkeypatch, tmp_path):
    event = _clip_new_event(1, "remote crash recovery")

    def crash_before_write(_clip):
        raise RuntimeError("simulated process stop")

    monkeypatch.setattr(api.service, "write_obsidian_or_queue", crash_before_write)
    with pytest.raises(RuntimeError, match="process stop"):
        sync_engine._apply_clip_new(conn, event["data"], api.service)

    clip = ClipsRepo(conn).get_by_hash(normalize.content_hash("remote crash recovery"))
    assert clip is not None
    assert conn.execute(
        "SELECT 1 FROM backup_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is not None
    assert conn.in_transaction is False

    monkeypatch.undo()
    sync_engine._apply_clip_new(conn, event["data"], api.service)
    assert ClipsRepo(conn).get(clip.id).obsidian_path is not None
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1


def test_remote_duplicate_replay_repairs_missing_downstream_rows(api, conn, monkeypatch):
    event = _clip_new_event(1, "remote replay repair")
    monkeypatch.setattr(api.service, "write_obsidian_or_queue", lambda _clip: False)
    sync_engine._apply_clip_new(conn, event["data"], api.service)
    clip = ClipsRepo(conn).get_by_hash(normalize.content_hash("remote replay repair"))
    conn.execute("DELETE FROM backup_queue WHERE clip_id=?", (clip.id,))
    conn.execute("DELETE FROM obsidian_queue WHERE clip_id=?", (clip.id,))
    conn.commit()

    sync_engine._apply_clip_new(conn, event["data"], api.service)

    assert conn.execute(
        "SELECT 1 FROM backup_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT 1 FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone() is not None
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0


def test_remote_public_state_rolls_back_when_backup_intent_fails(api, conn, monkeypatch):
    event = _clip_new_event(1, "remote atomic rollback")
    original = sync_engine.BackupQueueRepo.enqueue

    def fail_after_enqueue(self, clip_id, when, *, commit=True):
        original(self, clip_id, when, commit=commit)
        raise RuntimeError("simulated remote backup failure")

    monkeypatch.setattr(sync_engine.BackupQueueRepo, "enqueue", fail_after_enqueue)

    with pytest.raises(RuntimeError, match="remote backup failure"):
        sync_engine._apply_clip_new(conn, event["data"], api.service)

    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM clips_fts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
