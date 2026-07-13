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
from clipvault.api import handlers as api_handlers
from clipvault.api import server as api_server
from clipvault.config import Config
from clipvault.core import normalize
from clipvault.core.models import Clip
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.unit_of_work import unit_of_work
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


def _outbox_clip_payload(content: str, *, is_secret: bool = False) -> dict:
    content_hash = normalize.content_hash(content)
    return {
        "id": f"01OUTBOX{content_hash[:18]}",
        "content": content,
        "content_hash": content_hash,
        "content_type": "text",
        "is_secret": is_secret,
        "secret_level": "hard" if is_secret else None,
        "secret_reasons": ["TEST"] if is_secret else [],
        "source_device": "desktop-test",
        "source_app": None,
        "created_at": "2026-06-13T10:00:00Z",
        "last_seen_at": "2026-06-13T10:00:00Z",
        "times_seen": 1,
        "pinned": False,
        "favorite": False,
        "deleted": False,
    }


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

    # A genuinely newer restore must recreate both schema-9 search rows.  Older
    # versions flipped deleted=0 but left the clip permanently absent from FTS.
    fresh = {"origin_device": PEER, "seq": 4, "kind": "clip_meta",
             "ts": "2026-06-13T10:30:00Z",
             "data": {"content_hash": content_hash, "patch": {"deleted": False},
                      "ts": "2026-06-13T10:30:00Z"}}
    api.sync_push(token, {"events": [fresh]})
    assert ClipsRepo(conn).get(clip.id).deleted is False
    assert ClipsRepo(conn).fts_contains(clip.id)


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


def test_h7_secret_patch_stays_local_and_legacy_clip_events_are_filtered(
    api, conn, caplog
):
    token = _pair(api)
    _, created = api.create_clip({"content": FAKE_AWS_KEY})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    assert clip is not None and clip.is_secret is True

    status, body = api.patch_clip(clip_id, {"pinned": True})

    assert status == 200 and body["applied"] == {"pinned": True}
    assert ClipsRepo(conn).get(clip_id).pinned is True
    assert sync_engine.emit_clip_meta(
        conn,
        clip.content_hash,
        {"favorite": True},
        "2026-06-13T10:20:00Z",
        "2026-06-13T10:20:00Z",
    ) is None
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert OutboxRepo(conn).max_seq() == 0
    assert BackupQueueRepo(conn).state_of(clip_id) is None

    remote_meta = {
        "origin_device": PEER,
        "seq": 1,
        "kind": "clip_meta",
        "ts": "2026-06-13T10:20:00Z",
        "data": {
            "content_hash": clip.content_hash,
            "patch": {"favorite": True},
            "ts": "2026-06-13T10:20:00Z",
        },
    }
    caplog.clear()
    with caplog.at_level("ERROR", logger="clipvault.sync"):
        push_status, pushed = api.sync_push(token, {"events": [remote_meta]})
    assert push_status == 200 and pushed["acked_upto"] == 1
    assert ClipsRepo(conn).get(clip_id).favorite is False
    assert clip.content_hash not in caplog.text

    # Defence in depth for rows written by a pre-gate version: neither a full
    # secret clip nor its hash-only metadata may leave through pull.
    outbox = OutboxRepo(conn)
    outbox.append(
        "clip_new",
        sync_engine.clip_to_data(clip),
        "2026-06-13T10:21:00Z",
    )
    outbox.append(
        "clip_meta",
        {
            "content_hash": clip.content_hash,
            "patch": {"pinned": True},
            "ts": "2026-06-13T10:22:00Z",
        },
        "2026-06-13T10:22:00Z",
    )
    final_seq = outbox.append(
        "clip_meta",
        {},
        "2026-06-13T10:23:00Z",
    )
    caplog.clear()
    with caplog.at_level("ERROR", logger="clipvault.sync"):
        pull_status, pulled = api.sync_pull(token, {"since_seq": "0"})

    assert pull_status == 200
    assert pulled["events"] == []
    assert pulled["next_seq"] == final_seq
    assert sync_engine.pull_blocked_summary(conn, max_bytes=1) is None
    assert FAKE_AWS_KEY not in caplog.text
    assert clip.content_hash not in caplog.text

    # Release is an explicit Owner decision.  Its new public event must not be
    # silently discarded merely because the original text still scans as a
    # secret-shaped value.
    assert api.release_clip(clip_id)[0] == 200
    release_status, released = api.sync_pull(
        token, {"since_seq": str(final_seq)}
    )
    assert release_status == 200
    assert len(released["events"]) == 1
    assert released["events"][0]["kind"] == "clip_new"
    assert released["events"][0]["payload"]["id"] == clip_id
    assert released["events"][0]["payload"]["is_secret"] is False
    assert released["events"][0]["payload"]["content"] == FAKE_AWS_KEY


def test_h7_release_does_not_retroactively_publish_old_secret_snapshot(api, conn):
    _, created = api.create_clip({"content": FAKE_AWS_KEY})
    clip_id = created["clip"]["id"]
    secret = ClipsRepo(conn).get(clip_id)
    outbox = OutboxRepo(conn)
    old_secret_seq = outbox.append(
        "clip_new",
        sync_engine.clip_to_data(secret),
        "2026-06-13T10:10:00Z",
    )

    assert api.release_clip(clip_id)[0] == 200
    release_seq = outbox.max_seq()
    assert release_seq > old_secret_seq

    pulled = sync_engine.build_pull(conn, 0)
    assert [event["seq"] for event in pulled["events"]] == [release_seq]
    assert pulled["events"][0]["payload"]["is_secret"] is False
    assert pulled["events"][0]["payload"]["id"] == clip_id
    assert pulled["next_seq"] == release_seq


def test_h7_legacy_clip_new_cannot_borrow_public_hash_for_secret_payload(
    api, conn
):
    _, created = api.create_clip({"content": "safe local identity"})
    public = ClipsRepo(conn).get(created["clip"]["id"])
    legitimate_seq = OutboxRepo(conn).max_seq()
    malicious = _outbox_clip_payload(FAKE_AWS_KEY)
    malicious["content_hash"] = public.content_hash
    malicious["id"] = public.id
    malicious_seq = OutboxRepo(conn).append(
        "clip_new", malicious, "2026-06-13T10:20:00Z"
    )

    pulled = sync_engine.build_pull(conn, legitimate_seq)

    assert pulled["events"] == []
    assert pulled["next_seq"] == malicious_seq
    assert FAKE_AWS_KEY not in json.dumps(pulled, ensure_ascii=False)


def test_h7_malformed_clip_events_cannot_hide_secret_extra_fields(api, conn):
    _, created = api.create_clip({"content": "strict outbox payload"})
    public = ClipsRepo(conn).get(created["clip"]["id"])
    legitimate_seq = OutboxRepo(conn).max_seq()

    clip_new = sync_engine.clip_to_data(public)
    clip_new["secret_dump"] = FAKE_AWS_KEY
    extra_field_seq = OutboxRepo(conn).append(
        "clip_new", clip_new, "2026-06-13T10:20:00Z"
    )
    malformed_meta_seq = OutboxRepo(conn).append(
        "clip_meta",
        {
            "content_hash": public.content_hash,
            "patch": {"pinned": FAKE_AWS_KEY},
            "ts": "2026-06-13T10:21:00Z",
        },
        "2026-06-13T10:21:00Z",
    )

    pulled = sync_engine.build_pull(conn, legitimate_seq)

    assert pulled["events"] == []
    assert pulled["next_seq"] == malformed_meta_seq
    assert extra_field_seq < malformed_meta_seq
    assert FAKE_AWS_KEY not in json.dumps(pulled, ensure_ascii=False)


def test_h7_corrupt_json_and_unknown_outbox_kinds_fail_closed(conn):
    conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) VALUES (?,?,?)",
        ("clip_new", "{not-json", "2026-06-13T10:20:00Z"),
    )
    final_seq = OutboxRepo(conn).append(
        "future_kind",
        {"secret_dump": FAKE_AWS_KEY},
        "2026-06-13T10:21:00Z",
    )
    envelope_seq = OutboxRepo(conn).append(
        "memory_delete",
        {
            "kind": "term",
            "text": "safe term",
            "ts": "2026-06-13T10:22:00Z",
        },
        FAKE_AWS_KEY,
    )
    surrogate = conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) VALUES (?,?,?)",
        (
            "memory_upsert",
            '{"kind":"term","text":"\\ud800","label":null,'
            '"pinned":false,"use_count":0,"source":"manual"}',
            "2026-06-13T10:22:00Z",
        ),
    )
    surrogate_seq = surrogate.lastrowid
    conn.commit()

    pulled = sync_engine.build_pull(conn, 0)

    assert pulled["events"] == []
    assert final_seq < envelope_seq
    assert envelope_seq < surrogate_seq
    assert pulled["next_seq"] == surrogate_seq
    assert FAKE_AWS_KEY not in json.dumps(pulled, ensure_ascii=False)


def test_h7_invalid_utf8_outbox_text_fails_closed_before_sqlite_decoding(
    api, conn
):
    _pair(api)
    invalid_payload = conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) "
        "VALUES ('clip_new', CAST(x'80' AS TEXT), '2026-06-13T10:20:00Z')"
    )
    invalid_kind = conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) "
        "VALUES (CAST(x'80' AS TEXT), '{}', '2026-06-13T10:21:00Z')"
    )
    invalid_created_at = conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) "
        "VALUES ('memory_delete', '{}', CAST(x'80' AS TEXT))"
    )
    conn.commit()

    rows = OutboxRepo(conn).list_since(0)
    pulled = sync_engine.build_pull(conn, 0)

    assert [row["seq"] for row in rows] == [
        invalid_payload.lastrowid,
        invalid_kind.lastrowid,
        invalid_created_at.lastrowid,
    ]
    assert rows[0]["payload"] is None
    assert rows[1]["kind"] is None
    assert rows[2]["created_at"] is None
    assert pulled["events"] == []
    assert pulled["next_seq"] == invalid_created_at.lastrowid
    assert sync_engine.pull_blocked_summary(conn, max_bytes=1) is None


def test_h7_excessively_nested_outbox_json_fails_closed(api, conn):
    _pair(api)
    nested_json = ("[" * 10_000) + "0" + ("]" * 10_000)
    row = conn.execute(
        "INSERT INTO sync_outbox(kind, payload, created_at) VALUES (?,?,?)",
        ("clip_new", nested_json, "2026-06-13T10:20:00Z"),
    )
    conn.commit()

    events = OutboxRepo(conn).list_since(0)
    pulled = sync_engine.build_pull(conn, 0)

    assert events[0]["payload"] is None
    assert pulled["events"] == []
    assert pulled["next_seq"] == row.lastrowid
    assert sync_engine.pull_blocked_summary(conn, max_bytes=1) is None


def test_h7_local_patch_rolls_back_flags_meta_outbox_and_backup_intent(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "atomic local metadata patch"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    queue = BackupQueueRepo(conn)
    queue.mark_done(clip_id, "2026-06-13T10:00:00Z")
    outbox_before = OutboxRepo(conn).max_seq()
    original_reenqueue = BackupQueueRepo.reenqueue

    def fail_after_reenqueue(self, candidate_id, when, *, commit=True):
        assert commit is False
        original_reenqueue(self, candidate_id, when, commit=False)
        raise RuntimeError("injected backup intent failure")

    monkeypatch.setattr(BackupQueueRepo, "reenqueue", fail_after_reenqueue)

    with pytest.raises(RuntimeError, match="backup intent failure"):
        api.patch_clip(
            clip_id,
            {"pinned": True, "favorite": True, "deleted": True},
        )

    restored = ClipsRepo(conn).get(clip_id)
    assert restored.pinned is False
    assert restored.favorite is False
    assert restored.deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert OutboxRepo(conn).max_seq() == outbox_before
    assert queue.state_of(clip_id) == "done"
    assert conn.in_transaction is False


def test_h7_emit_clip_meta_default_path_is_atomic(conn, monkeypatch):
    outcome = Clip(
        id="01ATOMICMETA00000000000001",
        content="direct metadata emission",
        content_hash=normalize.content_hash("direct metadata emission"),
        content_type="text",
        is_secret=False,
        secret_level=None,
        secret_reasons=[],
        source_device="desktop",
        source_app=None,
        created_at="2026-06-13T10:00:00Z",
        last_seen_at="2026-06-13T10:00:00Z",
    )
    ClipsRepo(conn).insert(outcome)
    original_append = OutboxRepo.append

    def fail_after_append(self, kind, payload, when, *, commit=True):
        assert kind == "clip_meta"
        assert commit is False
        original_append(self, kind, payload, when, commit=False)
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(OutboxRepo, "append", fail_after_append)

    with pytest.raises(RuntimeError, match="outbox failure"):
        sync_engine.emit_clip_meta(
            conn,
            outcome.content_hash,
            {"pinned": True},
            "2026-06-13T10:20:00Z",
            "2026-06-13T10:20:00Z",
        )

    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (outcome.content_hash,),
    ).fetchone()[0] == 0
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.in_transaction is False


@pytest.mark.parametrize(
    "invalid_patch",
    [
        {},
        {"pinned": FAKE_AWS_KEY},
        {"unknown": True},
        {"favorite": True, "unknown": False},
    ],
)
def test_h7_emit_clip_meta_rejects_invalid_patch_without_side_effects(
    api, conn, invalid_patch
):
    _, created = api.create_clip({"content": "validated metadata emission"})
    clip = ClipsRepo(conn).get(created["clip"]["id"])
    outbox_before = OutboxRepo(conn).max_seq()

    with pytest.raises(ValueError, match="invalid clip metadata"):
        sync_engine.emit_clip_meta(
            conn,
            clip.content_hash,
            invalid_patch,
            "2026-06-13T10:20:00Z",
            "2026-06-13T10:20:00Z",
        )

    assert OutboxRepo(conn).max_seq() == outbox_before
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert conn.in_transaction is False


def test_h7_emit_clip_meta_standalone_success_commits_atomically(api, conn):
    _, created = api.create_clip({"content": "standalone metadata emission"})
    clip = ClipsRepo(conn).get(created["clip"]["id"])

    seq = sync_engine.emit_clip_meta(
        conn,
        clip.content_hash,
        {"pinned": True},
        "2026-06-13T10:20:00Z",
        "2026-06-13T10:20:00Z",
    )

    assert isinstance(seq, int)
    assert OutboxRepo(conn).max_seq() == seq
    assert conn.execute(
        "SELECT ts FROM clip_meta_ts WHERE content_hash = ? AND field = 'pinned'",
        (clip.content_hash,),
    ).fetchone()[0] == "2026-06-13T10:20:00Z"
    assert conn.in_transaction is False


def test_h7_emit_clip_meta_default_path_joins_outer_unit_of_work(api, conn):
    _, created = api.create_clip({"content": "nested metadata emission"})
    clip = ClipsRepo(conn).get(created["clip"]["id"])
    outbox_before = OutboxRepo(conn).max_seq()

    with pytest.raises(RuntimeError, match="outer rollback"):
        with unit_of_work(conn):
            sync_engine.emit_clip_meta(
                conn,
                clip.content_hash,
                {"favorite": True},
                "2026-06-13T10:20:00Z",
                "2026-06-13T10:20:00Z",
            )
            assert conn.in_transaction is True
            raise RuntimeError("outer rollback")

    assert OutboxRepo(conn).max_seq() == outbox_before
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert conn.in_transaction is False


def test_h7_remote_clip_meta_rolls_back_flags_clocks_fts_and_backup(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "remote metadata atomic rollback"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    queue = BackupQueueRepo(conn)
    queue.mark_done(clip_id, "2026-06-13T10:00:00Z")
    original_reenqueue = BackupQueueRepo.reenqueue

    def fail_after_reenqueue(self, candidate_id, when, *, commit=True):
        assert commit is False
        original_reenqueue(self, candidate_id, when, commit=False)
        raise RuntimeError("injected remote backup failure")

    monkeypatch.setattr(BackupQueueRepo, "reenqueue", fail_after_reenqueue)

    with pytest.raises(RuntimeError, match="remote backup failure"):
        sync_engine._apply_clip_meta(
            conn,
            {
                "content_hash": clip.content_hash,
                "patch": {"pinned": True, "deleted": True},
                "ts": "2026-06-13T10:20:00Z",
            },
        )

    restored = ClipsRepo(conn).get(clip_id)
    assert restored.pinned is False
    assert restored.deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (clip.content_hash,),
    ).fetchone()[0] == 0
    assert queue.state_of(clip_id) == "done"
    assert conn.in_transaction is False


def test_h7_remote_delete_reenqueue_requires_a_real_state_transition(api, conn):
    _, created = api.create_clip({"content": "idempotent remote deletion"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    queue = BackupQueueRepo(conn)
    queue.mark_done(clip_id, "2026-06-13T10:00:00Z")

    def apply(value: bool, ts: str) -> None:
        sync_engine._apply_clip_meta(
            conn,
            {
                "content_hash": clip.content_hash,
                "patch": {"deleted": value},
                "ts": ts,
            },
        )

    # A newer same-value update advances the field clock without waking backup.
    apply(False, "2026-06-13T10:10:00Z")
    assert queue.state_of(clip_id) == "done"
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "deleted"
    ) == "2026-06-13T10:10:00Z"

    # At an exact tie, delete=True wins and the real state transition is backed up.
    apply(True, "2026-06-13T10:10:00Z")
    assert ClipsRepo(conn).get(clip_id).deleted is True
    assert not ClipsRepo(conn).fts_contains(clip_id)
    assert queue.state_of(clip_id) == "pending"

    queue.mark_done(clip_id, "2026-06-13T10:11:00Z")
    apply(True, "2026-06-13T10:20:00Z")
    assert queue.state_of(clip_id) == "done"
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "deleted"
    ) == "2026-06-13T10:20:00Z"

    # An exact-tie undelete loses to the existing delete.
    apply(False, "2026-06-13T10:20:00Z")
    assert ClipsRepo(conn).get(clip_id).deleted is True
    assert queue.state_of(clip_id) == "done"

    apply(False, "2026-06-13T10:30:00Z")
    assert ClipsRepo(conn).get(clip_id).deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert queue.state_of(clip_id) == "pending"

    queue.mark_done(clip_id, "2026-06-13T10:31:00Z")
    apply(False, "2026-06-13T10:40:00Z")
    assert queue.state_of(clip_id) == "done"
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "deleted"
    ) == "2026-06-13T10:40:00Z"


def test_h7_local_same_second_pin_true_then_false_gets_monotonic_ts(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "same second pin clock"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    fixed = "2026-06-13T10:20:00Z"
    monkeypatch.setattr(api_handlers, "_now_iso", lambda: fixed)

    assert api.patch_clip(clip_id, {"pinned": True})[0] == 200
    assert api.patch_clip(clip_id, {"pinned": False})[0] == 200

    events = [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] == "clip_meta"
        and event["payload"]["content_hash"] == clip.content_hash
    ]
    assert [event["payload"]["ts"] for event in events] == [
        fixed,
        "2026-06-13T10:20:01Z",
    ]
    assert [event["created_at"] for event in events] == [fixed, fixed]
    assert ClipsRepo(conn).get(clip_id).pinned is False
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "pinned"
    ) == "2026-06-13T10:20:01Z"


def test_h7_local_same_second_delete_then_restore_gets_monotonic_ts(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "same second deletion clock"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    fixed = "2026-06-13T10:20:00Z"
    monkeypatch.setattr(api_handlers, "_now_iso", lambda: fixed)

    assert api.patch_clip(clip_id, {"deleted": True})[0] == 200
    assert api.patch_clip(clip_id, {"deleted": False})[0] == 200

    events = [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] == "clip_meta"
        and event["payload"]["content_hash"] == clip.content_hash
    ]
    assert [event["payload"]["ts"] for event in events] == [
        fixed,
        "2026-06-13T10:20:01Z",
    ]
    assert [event["created_at"] for event in events] == [fixed, fixed]
    assert ClipsRepo(conn).get(clip_id).deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "deleted"
    ) == "2026-06-13T10:20:01Z"


def test_h7_local_clock_rollback_advances_from_persisted_field_ts(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "wall clock rollback"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    candidates = iter(
        ("2026-06-13T10:20:00Z", "2026-06-13T09:00:00Z")
    )
    monkeypatch.setattr(api_handlers, "_now_iso", lambda: next(candidates))

    assert api.patch_clip(clip_id, {"pinned": True})[0] == 200
    assert api.patch_clip(clip_id, {"pinned": False})[0] == 200

    events = [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] == "clip_meta"
        and event["payload"]["content_hash"] == clip.content_hash
    ]
    assert [event["payload"]["ts"] for event in events] == [
        "2026-06-13T10:20:00Z",
        "2026-06-13T10:20:01Z",
    ]
    assert [event["created_at"] for event in events] == [
        "2026-06-13T10:20:00Z",
        "2026-06-13T09:00:00Z",
    ]


def test_h7_metadata_clock_ceiling_saturates_without_freezing_local_actions(
    api, conn, monkeypatch
):
    token = _pair(api)
    _, created = api.create_clip({"content": "metadata ceiling recovery"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    ceiling = "9999-12-31T23:59:59Z"
    gap_event = {
        "origin_device": PEER,
        "seq": 2,
        "kind": "clip_meta",
        "ts": "2026-06-13T10:19:00Z",
        "data": {
            "content_hash": clip.content_hash,
            "patch": {"pinned": True, "deleted": True},
            "ts": ceiling,
        },
    }
    push_status, pushed = api.sync_push(token, {"events": [gap_event]})
    assert push_status == 200 and pushed["acked_upto"] == 0
    monkeypatch.setattr(
        api_handlers, "_now_iso", lambda: "2026-06-13T10:20:00Z"
    )

    assert api.patch_clip(
        clip_id, {"pinned": False, "deleted": False}
    )[0] == 200

    current = ClipsRepo(conn).get(clip_id)
    assert current.pinned is False
    assert current.deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "deleted"
    ) == f"{ceiling}#local"
    event = [
        candidate
        for candidate in OutboxRepo(conn).list_since(0)
        if candidate["kind"] == "clip_meta"
    ][-1]
    assert event["payload"]["ts"] == ceiling

    # A gap event can be applied before its cursor advances and later replayed.
    # The old maximum timestamp must not undo the later local Owner action.
    BackupQueueRepo(conn).mark_done(clip_id, "2026-06-13T10:21:00Z")
    replay_status, replayed = api.sync_push(token, {"events": [gap_event]})
    assert replay_status == 200 and replayed["acked_upto"] == 0

    received = ClipsRepo(conn).get(clip_id)
    assert received.pinned is False
    assert received.deleted is False
    assert ClipsRepo(conn).fts_contains(clip_id)
    assert BackupQueueRepo(conn).state_of(clip_id) == "done"


def test_h7_local_multi_field_patch_advances_past_highest_field_clock(
    api, conn, monkeypatch
):
    _, created = api.create_clip({"content": "multi field logical clock"})
    clip_id = created["clip"]["id"]
    clip = ClipsRepo(conn).get(clip_id)
    candidates = iter(
        (
            "2026-06-13T10:20:00Z",
            "2026-06-13T10:30:00Z",
            "2026-06-13T10:10:00Z",
        )
    )
    monkeypatch.setattr(api_handlers, "_now_iso", lambda: next(candidates))

    assert api.patch_clip(clip_id, {"pinned": True})[0] == 200
    assert api.patch_clip(clip_id, {"favorite": True})[0] == 200
    assert api.patch_clip(
        clip_id, {"pinned": False, "favorite": False}
    )[0] == 200

    events = [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] == "clip_meta"
        and event["payload"]["content_hash"] == clip.content_hash
    ]
    final = events[-1]
    assert final["payload"]["ts"] == "2026-06-13T10:30:01Z"
    assert final["created_at"] == "2026-06-13T10:10:00Z"
    assert final["payload"]["patch"] == {"pinned": False, "favorite": False}
    rows = conn.execute(
        "SELECT field, ts FROM clip_meta_ts "
        "WHERE content_hash = ? AND field IN ('pinned', 'favorite')",
        (clip.content_hash,),
    ).fetchall()
    assert {row["field"]: row["ts"] for row in rows} == {
        "pinned": "2026-06-13T10:30:01Z",
        "favorite": "2026-06-13T10:30:01Z",
    }


def test_h7_local_logical_clock_observes_uncommitted_outer_uow(api, conn):
    _, created = api.create_clip({"content": "outer transaction logical clock"})
    clip = ClipsRepo(conn).get(created["clip"]["id"])
    candidate = "2026-06-13T10:20:00Z"

    with unit_of_work(conn):
        sync_engine.emit_clip_meta(
            conn,
            clip.content_hash,
            {"pinned": True},
            candidate,
            candidate,
            commit=False,
        )
        sync_engine.emit_clip_meta(
            conn,
            clip.content_hash,
            {"pinned": False},
            candidate,
            candidate,
            commit=False,
        )

    events = [
        event
        for event in OutboxRepo(conn).list_since(0)
        if event["kind"] == "clip_meta"
        and event["payload"]["content_hash"] == clip.content_hash
    ]
    assert [event["payload"]["ts"] for event in events] == [
        candidate,
        "2026-06-13T10:20:01Z",
    ]
    assert sync_engine._get_meta_ts(
        conn, clip.content_hash, "pinned"
    ) == "2026-06-13T10:20:01Z"


def test_h7_legacy_public_row_rechecks_current_secret_guard_at_sync_boundaries(
    api, conn
):
    content = "AKIAIOSFODNN7EXAMPLF"
    legacy = Clip(
        id="01LEGACYSECRETMETA00000001",
        content=content,
        content_hash=normalize.content_hash(content),
        content_type="text",
        is_secret=False,
        secret_level=None,
        secret_reasons=[],
        source_device="desktop",
        source_app=None,
        created_at="2026-06-13T10:00:00Z",
        last_seen_at="2026-06-13T10:00:00Z",
        deleted=True,
    )
    ClipsRepo(conn).insert(legacy)
    assert ClipsRepo(conn).fts_contains(legacy.id) is False
    assert sync_engine.emit_clip_new(
        conn, legacy, "2026-06-13T10:10:00Z"
    ) is None

    status, _ = api.patch_clip(
        legacy.id, {"pinned": True, "deleted": False}
    )

    assert status == 200
    assert ClipsRepo(conn).get(legacy.id).pinned is True
    assert ClipsRepo(conn).get(legacy.id).deleted is False
    assert ClipsRepo(conn).fts_contains(legacy.id) is False
    assert OutboxRepo(conn).max_seq() == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM clip_meta_ts WHERE content_hash = ?",
        (legacy.content_hash,),
    ).fetchone()[0] == 0

    token = _pair(api)
    remote_status, remote_body = api.sync_push(
        token,
        {
            "events": [
                {
                    "origin_device": PEER,
                    "seq": 1,
                    "kind": "clip_meta",
                    "ts": "2026-06-13T10:15:00Z",
                    "data": {
                        "content_hash": legacy.content_hash,
                        "patch": {"favorite": True},
                        "ts": "2026-06-13T10:15:00Z",
                    },
                }
            ]
        },
    )
    assert remote_status == 200 and remote_body["acked_upto"] == 1
    assert ClipsRepo(conn).get(legacy.id).favorite is False

    legacy_seq = OutboxRepo(conn).append(
        "clip_meta",
        {
            "content_hash": legacy.content_hash,
            "patch": {"pinned": True},
            "ts": "2026-06-13T10:20:00Z",
        },
        "2026-06-13T10:20:00Z",
    )
    pulled = sync_engine.build_pull(conn, 0)
    assert pulled["events"] == []
    assert pulled["next_seq"] == legacy_seq


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
    first_seq = outbox.append("clip_new", _outbox_clip_payload("a" * 200), when)
    second_seq = outbox.append("clip_new", _outbox_clip_payload("b" * 200), when)

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
                _outbox_clip_payload(f"page-row-{index}"),
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
    seq = outbox.append(
        "clip_new", _outbox_clip_payload("oversized-content"), when
    )

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
        _outbox_clip_payload(content),
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
        _outbox_clip_payload("x" * sync_engine.SYNC_PULL_HTTP_RESPONSE_BYTES),
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
        _outbox_clip_payload(oversized_content),
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


def test_status_scans_past_one_page_of_filtered_secret_clip_events(api, conn):
    _pair(api)
    _, created = api.create_clip({"content": FAKE_AWS_KEY})
    secret = ClipsRepo(conn).get(created["clip"]["id"])
    outbox = OutboxRepo(conn)
    last_secret_seq = 0
    for index in range(sync_engine.SYNC_PULL_FETCH_LIMIT):
        last_secret_seq = outbox.append(
            "clip_new",
            sync_engine.clip_to_data(secret),
            f"2026-06-13T10:00:{index:02d}Z",
        )
    public_content = "sendable event behind a filtered internal page"
    public_seq = outbox.append(
        "clip_new",
        _outbox_clip_payload(public_content),
        "2026-06-13T10:01:00Z",
    )

    first_page = sync_engine.build_pull(conn, 0)
    assert first_page["events"] == []
    assert first_page["next_seq"] == last_secret_seq
    assert first_page["has_more"] is True
    second_page = sync_engine.build_pull(conn, last_secret_seq)
    assert [event["seq"] for event in second_page["events"]] == [public_seq]

    blocked = sync_engine.pull_blocked_summary(conn, max_bytes=1)
    assert blocked is not None
    assert blocked["first_seq"] == public_seq
    assert blocked["blocked_devices"] == 1


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


def test_remote_db_intent_survives_crash_before_worker_dispatch(api, conn, monkeypatch, tmp_path):
    event = _clip_new_event(1, "remote crash recovery")

    def crash_before_dispatch(_clip):
        raise RuntimeError("simulated process stop")

    monkeypatch.setattr(api.service, "dispatch_obsidian_work", crash_before_dispatch)
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
