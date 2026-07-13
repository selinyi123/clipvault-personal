"""B2/B3/B4/B7: service orchestration, obsidian retry, log hygiene."""

import logging

import pytest

from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store.backup_queue_repo import BackupQueueRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


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
    next_attempt_at = service.conn.execute(
        "SELECT next_attempt_at FROM obsidian_queue WHERE clip_id=?", (clip.id,)
    ).fetchone()[0]
    assert service.retry_obsidian_sweep(now_fn=lambda: next_attempt_at) == 1
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


def test_release_rolls_back_public_state_when_outbox_emit_fails(service, conn, monkeypatch):
    from clipvault.store.outbox_repo import OutboxRepo
    from clipvault.sync import engine

    secret = service.handle_clipboard_text(FAKE_AWS_KEY).clip

    def fail_after_append(db_conn, clip, when, *, commit=True):
        OutboxRepo(db_conn).append("clip_new", {"id": clip.id}, when, commit=commit)
        raise RuntimeError("simulated release outbox failure")

    monkeypatch.setattr(engine, "emit_clip_new", fail_after_append)

    with pytest.raises(RuntimeError, match="release outbox failure"):
        service.release_clip(secret.id)

    persisted = service.clips.get(secret.id)
    assert persisted.is_secret is True
    assert persisted.released is False
    assert service.clips.fts_contains(secret.id) is False
    assert conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM obsidian_queue").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
