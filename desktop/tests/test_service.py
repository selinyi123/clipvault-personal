"""B2/B3/B4/B7: service orchestration, obsidian retry, log hygiene."""

import logging
import sqlite3
import threading

import pytest

from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store import db as db_mod
from clipvault.store.backup_queue_repo import BackupQueueRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _file_cfg(tmp_path):
    """Config backed by an on-disk DB so connections opened on different threads
    share the same database (unlike :memory:, which is per-connection)."""
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="test-desktop",
        db_path=str(tmp_path / "clipvault.db"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


@pytest.fixture
def cfg(tmp_path):
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="test-desktop",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


@pytest.fixture
def service(conn, cfg):
    return ClipVaultService(conn, cfg)


def test_b2_public_clip_reaches_obsidian(service, cfg, conn, tmp_path):
    outcome = service.handle_clipboard_text("hello obsidian", "notepad.exe")
    assert outcome.status == "new"
    files = list((tmp_path / "vault").rglob("*.md"))
    assert len(files) == 1
    clip = service.clips.get(outcome.clip.id)
    assert clip.obsidian_path == str(files[0])
    assert BackupQueueRepo(conn).has(clip.id)


def test_b3_secret_clip_never_reaches_vault(service, tmp_path):
    outcome = service.handle_clipboard_text(FAKE_AWS_KEY)
    assert outcome.clip.is_secret
    assert list((tmp_path / "vault").rglob("*")) == []


def test_b4_obsidian_failure_then_sweep_repairs(service, cfg, tmp_path):
    # Make the vault path unusable: a regular file blocks directory creation.
    (tmp_path / "vault").write_text("not a directory", encoding="utf-8")
    outcome = service.handle_clipboard_text("survives vault outage")
    clip = service.clips.get(outcome.clip.id)
    assert clip is not None and clip.obsidian_path is None  # saved, not written

    (tmp_path / "vault").unlink()  # vault becomes available again
    assert service.retry_obsidian_sweep() == 1
    clip = service.clips.get(outcome.clip.id)
    assert clip.obsidian_path is not None
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1


def test_b4_sweep_noop_when_clean(service):
    service.handle_clipboard_text("already written")
    assert service.retry_obsidian_sweep() == 0


def test_b7_logs_never_contain_content(service, caplog):
    with caplog.at_level(logging.DEBUG, logger="clipvault.service"):
        public = service.handle_clipboard_text("the quick brown fox")
        secret = service.handle_clipboard_text(FAKE_AWS_KEY)
        service.handle_clipboard_text("the quick brown fox")  # duplicate path
    assert "quick" not in caplog.text
    assert "AKIA" not in caplog.text
    assert public.clip.id in caplog.text
    assert secret.clip.id in caplog.text
    assert "SG-AWS-ID" in caplog.text  # reasons are loggable, content is not


def test_duplicate_does_not_rewrite_obsidian(service, tmp_path):
    service.handle_clipboard_text("dup me")
    service.handle_clipboard_text("dup me")
    assert len(list((tmp_path / "vault").rglob("*.md"))) == 1


def test_watcher_captures_with_own_thread_connection(tmp_path):
    """Regression for the watcher cross-thread bug: the watcher runs on its own
    thread, so it must own its DB connection (as main.watch_loop now does). A
    capture dispatched from a worker thread must succeed and persist."""
    cfg = _file_cfg(tmp_path)
    db_mod.migrate(db_mod.connect(cfg.db_path))
    captured: dict[str, str] = {}

    def worker() -> None:
        # Mirrors main.watch_loop: connect *inside* the thread, then capture.
        svc = ClipVaultService(db_mod.connect(cfg.db_path), cfg)
        captured["id"] = svc.handle_clipboard_text("from the watcher thread", "notepad.exe").clip.id

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    # Visible through a separate connection -> the capture really persisted.
    verify = ClipVaultService(db_mod.connect(cfg.db_path), cfg)
    assert verify.clips.get(captured["id"]) is not None


def test_connection_shared_across_threads_is_rejected(tmp_path):
    """Documents what the fix avoids: a connection created on one thread cannot
    be used from another (sqlite3 check_same_thread), which previously made every
    real clipboard capture on the watcher thread raise."""
    cfg = _file_cfg(tmp_path)
    shared = db_mod.connect(cfg.db_path)  # created on the main (test) thread
    db_mod.migrate(shared)
    svc = ClipVaultService(shared, cfg)  # service bound to a foreign-thread conn
    errors: dict[str, Exception] = {}

    def worker() -> None:
        try:
            svc.handle_clipboard_text("cross-thread use")
        except Exception as exc:  # noqa: BLE001 - asserting the failure mode
            errors["e"] = exc

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert isinstance(errors.get("e"), sqlite3.ProgrammingError)
