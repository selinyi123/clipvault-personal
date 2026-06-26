"""S004 gates D1-D9. Handler-level tests (no socket) plus one end-to-end
http.client test for routing + loopback guard (D8)."""

import http.client
import json
import logging
import os
import tempfile
import threading
import time

import pytest

from clipvault.api.handlers import Api
from clipvault.api import server as api_server
from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.outbox_repo import OutboxRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@pytest.fixture
def cfg(tmp_path):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", device_name="test-desktop",
        db_path=":memory:", max_clip_bytes=1_048_576, poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


@pytest.fixture
def api(conn, cfg):
    return Api(ClipVaultService(conn, cfg))


def test_d1_health(api):
    code, obj = api.health()
    assert code == 200 and obj["db_ok"] is True


def test_d2_list_and_search(api):
    api.create_clip({"content": "the quick brown fox"})
    api.create_clip({"content": "git status"})
    code, obj = api.list_clips({})
    assert code == 200 and len(obj["clips"]) == 2
    # FTS search
    _, found = api.list_clips({"q": "quick"})
    assert len(found["clips"]) == 1 and found["clips"][0]["content"] == "the quick brown fox"
    # type filter
    _, cmds = api.list_clips({"type": "command"})
    assert len(cmds["clips"]) == 1 and cmds["clips"][0]["content_type"] == "command"


def test_d2_bad_limit_params_return_400(api):
    assert api.list_clips({"limit": "abc"})[0] == 400
    assert api.list_memory({"limit": "abc"})[0] == 400
    assert api.suggest({"limit": "abc"})[0] == 400
    assert api.list_clips({"limit": "-1"})[0] == 400


def test_d2_high_limit_params_are_clamped_for_compatibility(api):
    api.create_clip({"content": "one"})
    api.create_clip({"content": "two"})
    code, obj = api.list_clips({"limit": "9999"})
    assert code == 200
    assert len(obj["clips"]) == 2


def test_d3_secret_hidden_then_redacted(api):
    api.create_clip({"content": FAKE_AWS_KEY})
    # default list excludes secrets
    _, normal = api.list_clips({})
    assert normal["clips"] == []
    # secret=1 returns redacted content
    _, sec = api.list_clips({"secret": "1"})
    assert len(sec["clips"]) == 1
    c = sec["clips"][0]
    assert c["is_secret"] is True
    assert FAKE_AWS_KEY not in c["content"]
    assert c["content"].startswith("AKIA") and "••••" in c["content"]
    assert c["length"] is None  # secret previews must not leak exact length


def test_d4_create_writes_obsidian(api, cfg, tmp_path):
    code, obj = api.create_clip({"content": "hello api", "source_app": "ui"})
    assert code == 201
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1


def test_d4_create_rejects_empty(api):
    code, obj = api.create_clip({"content": "   "})
    assert code == 400


def test_d5_patch_flags_and_delete_removes_from_fts(api, conn):
    _, obj = api.create_clip({"content": "patch me please"})
    cid = obj["clip"]["id"]
    api.patch_clip(cid, {"pinned": True, "favorite": True})
    clip = ClipsRepo(conn).get(cid)
    assert clip.pinned and clip.favorite
    # delete removes from FTS
    api.patch_clip(cid, {"deleted": True})
    _, found = api.list_clips({"q": "patch"})
    assert found["clips"] == []


def test_d6_release_runs_pipeline(api, conn, tmp_path):
    _, obj = api.create_clip({"content": FAKE_AWS_KEY})
    cid = obj["clip"]["id"]
    code, rel = api.release_clip(cid)
    assert code == 200 and rel["released"] is True
    clip = ClipsRepo(conn).get(cid)
    assert clip.is_secret is False and clip.released is True
    assert ClipsRepo(conn).fts_contains(cid)              # back in FTS
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1  # written to vault


def test_d6_release_reenters_sync_outbox(api, conn):
    _, obj = api.create_clip({"content": FAKE_AWS_KEY})
    cid = obj["clip"]["id"]
    assert OutboxRepo(conn).list_since(0) == []
    assert api.release_clip(cid)[0] == 200
    rows = OutboxRepo(conn).list_since(0)
    assert len(rows) == 1
    assert rows[0]["kind"] == "clip_new"
    assert rows[0]["payload"]["id"] == cid


def test_d6_release_missing_returns_404(api):
    code, _ = api.release_clip("01NONEXISTENT00000000000000")
    assert code == 404


def test_d7_status_matches_db(api):
    api.create_clip({"content": "one"})
    api.create_clip({"content": "two"})
    api.create_clip({"content": FAKE_AWS_KEY})
    code, st = api.status()
    assert code == 200
    assert st["clips_total"] == 3
    assert st["quarantined"] == 1
    assert st["backup_pending"] == 2  # secret not queued


def test_d8_loopback_guard_and_routing(api):
    """End-to-end: real socket, verify health route works from loopback."""
    stop = threading.Event()
    httpd = api_server.build_server(api, "127.0.0.1", 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        assert resp.status == 200
        assert json.loads(resp.read())["status"] == "ok"
        conn.close()
    finally:
        stop.set()


def _serve_until(httpd, stop):
    httpd.timeout = 0.2
    while not stop.is_set():
        httpd.handle_request()
    httpd.server_close()


def test_d8_binds_configured_host(api):
    # S006: the socket binds the configured host so LAN devices can sync;
    # management routes stay loopback-only via the handler guard (see test_h2).
    httpd = api_server.build_server(api, "127.0.0.1", 0)
    assert httpd.server_address[0] == "127.0.0.1"
    httpd.server_close()


def test_d8_pair_rejects_large_body(api):
    stop = threading.Event()
    httpd = api_server.build_server(api, "127.0.0.1", 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/pair", body="x" * 5000, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        assert resp.status == 413
        resp.read()
        conn.close()
    finally:
        stop.set()


def test_d8_release_endpoint_remains_bodyless(cfg):
    # Drive the real server (serve() owns its connection on the serving thread,
    # S004) so /release is exercised end-to-end: a non-JSON body must be ignored,
    # not parsed, so the call still returns 200 instead of 400. Uses a file DB so
    # the clip created over the socket is visible to the same serving thread.
    cfg.db_path = os.path.join(tempfile.mkdtemp(), "cv.db")
    cfg.port = 8796
    stop = threading.Event()
    threading.Thread(target=api_server.serve, args=(cfg, stop), daemon=True).start()
    time.sleep(0.5)
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8796, timeout=5)
        c.request("POST", "/api/clips", body=json.dumps({"content": FAKE_AWS_KEY}),
                  headers={"Content-Type": "application/json"})
        resp = c.getresponse()
        assert resp.status == 201
        cid = json.loads(resp.read())["clip"]["id"]
        c.close()

        # FAKE_AWS_KEY is quarantined as a secret, so releasing it yields 200; the
        # invalid body proves the route never tries to JSON-parse the request.
        c = http.client.HTTPConnection("127.0.0.1", 8796, timeout=5)
        c.request("POST", f"/api/clips/{cid}/release", body="not-json",
                  headers={"Content-Type": "application/json"})
        assert c.getresponse().status == 200
        c.close()
    finally:
        stop.set()
        time.sleep(0.6)


def test_d9_api_logs_no_content(api, caplog):
    with caplog.at_level(logging.INFO, logger="clipvault"):
        api.create_clip({"content": "topsecretwords in content"})
    assert "topsecretwords" not in caplog.text
