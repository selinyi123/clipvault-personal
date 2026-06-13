"""S004 gates D1-D9. Handler-level tests (no socket) plus one end-to-end
http.client test for routing + loopback guard (D8)."""

import http.client
import logging
import threading

import pytest

from clipvault.api.handlers import Api
from clipvault.api import server as api_server
from clipvault.config import Config
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo

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
        import json
        assert json.loads(resp.read())["status"] == "ok"
        conn.close()
    finally:
        stop.set()


def _serve_until(httpd, stop):
    httpd.timeout = 0.2
    while not stop.is_set():
        httpd.handle_request()
    httpd.server_close()


def test_d8_non_loopback_bind_forced_to_loopback(api):
    # even if config says 0.0.0.0, server must bind loopback (S004 safety)
    httpd = api_server.build_server(api, "0.0.0.0", 0)
    assert httpd.server_address[0] == "127.0.0.1"
    httpd.server_close()


def test_d9_api_logs_no_content(api, caplog):
    with caplog.at_level(logging.INFO, logger="clipvault"):
        api.create_clip({"content": "topsecretwords in content"})
    assert "topsecretwords" not in caplog.text
