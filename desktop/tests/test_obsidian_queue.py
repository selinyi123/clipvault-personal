from pathlib import Path

import pytest

from clipvault.config import Config
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.service import ClipVaultService
from clipvault.store.clips_repo import ClipsRepo
from clipvault.store.obsidian_queue_repo import ObsidianQueueRepo


def _cfg(tmp_path: Path) -> Config:
    return Config(
        device_id="desktop-test",
        device_name="desktop-test",
        db_path=":memory:",
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


def test_obsidian_queue_backoff_and_ready_limit(conn):
    first = pipeline.ingest(
        conn,
        "obsidian pending one",
        source_device="d",
        new_id_fn=lambda: "clip-0001",
    )
    second = pipeline.ingest(
        conn,
        "obsidian pending two",
        source_device="d",
        new_id_fn=lambda: "clip-0002",
    )
    queue = ObsidianQueueRepo(conn)
    now = "2026-07-09T00:00:00Z"

    assert queue.enqueue(first.clip.id, now) is True
    assert queue.enqueue(second.clip.id, now) is True
    assert queue.claim_ready(now, limit=1) == [first.clip.id]

    attempts = queue.record_failure(first.clip.id, "PermissionError", now)

    assert attempts == 1
    assert queue.claim_ready(now, limit=10) == [second.clip.id]
    stats = queue.stats(now)
    assert stats["pending"] == 2
    assert stats["ready"] == 1
    assert stats["blocked"] == 1


def test_service_queues_failed_obsidian_write(conn, tmp_path, monkeypatch):
    def fail_write_clip(*args, **kwargs):
        raise PermissionError("vault locked")

    monkeypatch.setattr(writer, "write_clip", fail_write_clip)
    svc = ClipVaultService(conn, _cfg(tmp_path))

    outcome = svc.handle_clipboard_text("obsidian unavailable", source_app="pytest")

    assert outcome.status == pipeline.STATUS_NEW
    assert outcome.needs_obsidian is True
    stats = svc.obsidian_retry_stats()
    assert stats["pending"] == 1
    assert stats["ready"] == 0
    assert stats["max_attempts"] == 1
    assert ClipsRepo(conn).get(outcome.clip.id).obsidian_path is None


def test_retry_obsidian_sweep_is_bounded(conn, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    svc = ClipVaultService(conn, cfg)
    queue = ObsidianQueueRepo(conn)
    now = "2026-07-09T00:00:00Z"
    ids = []
    for index, text in enumerate(("obsidian retry one", "obsidian retry two", "obsidian retry three"), start=1):
        outcome = pipeline.ingest(
            conn,
            text,
            source_device="d",
            new_id_fn=lambda index=index: f"clip-retry-{index:04d}",
        )
        ids.append(outcome.clip.id)
        queue.enqueue(outcome.clip.id, now)

    def write_clip(clip, vault_path, type_dirs):
        path = Path(vault_path) / f"{clip.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return path

    monkeypatch.setattr(writer, "write_clip", write_clip)

    repaired = svc.retry_obsidian_sweep(
        limit=2,
        max_runtime_ms=10_000,
        now_fn=lambda: now,
    )

    assert repaired == 2
    assert svc.obsidian_retry_stats()["pending"] == 1
    assert sum(1 for clip_id in ids if ClipsRepo(conn).get(clip_id).obsidian_path) == 2


def test_successful_write_clears_existing_queue_row(conn, tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    svc = ClipVaultService(conn, cfg)
    outcome = pipeline.ingest(conn, "existing obsidian retry row", source_device="d")
    queue = ObsidianQueueRepo(conn)
    now = "2026-07-09T00:00:00Z"
    queue.enqueue(outcome.clip.id, now)
    queue.record_failure(outcome.clip.id, "PermissionError", now)

    def write_clip(clip, vault_path, type_dirs):
        path = Path(vault_path) / f"{clip.id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
        return path

    monkeypatch.setattr(writer, "write_clip", write_clip)

    assert svc.write_obsidian_or_queue(outcome.clip) is True
    assert queue.stats(now)["pending"] == 0
    assert ClipsRepo(conn).get(outcome.clip.id).obsidian_path is not None


def test_legacy_write_obsidian_entrypoint_queues_failures(conn, tmp_path, monkeypatch):
    def fail_write_clip(*args, **kwargs):
        raise PermissionError("vault locked")

    monkeypatch.setattr(writer, "write_clip", fail_write_clip)
    svc = ClipVaultService(conn, _cfg(tmp_path))
    outcome = pipeline.ingest(conn, "legacy sync entrypoint failure", source_device="d")

    assert svc._write_obsidian(outcome.clip) is False
    assert svc.obsidian_retry_stats()["pending"] == 1


def test_secret_clip_is_not_queued_for_obsidian(conn):
    outcome = pipeline.ingest(conn, "AKIAIOSFODNN7EXAMPLE", source_device="d")
    queue = ObsidianQueueRepo(conn)

    assert outcome.clip.is_secret
    assert queue.enqueue(outcome.clip.id, "2026-07-09T00:00:00Z") is False
    assert queue.record_failure(outcome.clip.id, "PermissionError", "2026-07-09T00:00:00Z") == 0
    assert queue.stats("2026-07-09T00:00:00Z")["pending"] == 0
