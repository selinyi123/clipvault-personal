"""Pipeline orchestration: rejection, gate A, FTS/backup-queue exclusion (A6)."""

import pytest

from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo, SecretEnqueueError
from clipvault.store.clips_repo import ClipsRepo

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def test_rejects_empty(conn):
    assert pipeline.ingest(conn, "", source_device="d").status == pipeline.STATUS_REJECTED_EMPTY
    assert pipeline.ingest(conn, "  \n\t ", source_device="d").status == pipeline.STATUS_REJECTED_EMPTY
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


def test_rejects_too_large(conn):
    outcome = pipeline.ingest(conn, "x" * 11, source_device="d", max_bytes=10)
    assert outcome.status == pipeline.STATUS_REJECTED_TOO_LARGE
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0


def test_public_clip_full_path(conn):
    outcome = pipeline.ingest(conn, "git status", source_device="d", source_app="wt.exe")
    assert outcome.status == pipeline.STATUS_NEW
    assert outcome.needs_obsidian is True
    assert outcome.clip.content_type == "command"
    clips = ClipsRepo(conn)
    assert clips.fts_contains(outcome.clip.id)
    assert BackupQueueRepo(conn).has(outcome.clip.id)


def test_fts_search_finds_public_clip(conn):
    outcome = pipeline.ingest(
        conn, "the quick brown fox jumps", source_device="d"
    )
    results = ClipsRepo(conn).search_fts("quick")
    assert [c.id for c in results] == [outcome.clip.id]


def test_a6_secret_clip_quarantined(conn):
    outcome = pipeline.ingest(conn, FAKE_AWS_KEY, source_device="d")
    assert outcome.status == pipeline.STATUS_NEW
    assert outcome.needs_obsidian is False
    clip = outcome.clip
    assert clip.is_secret and clip.secret_level == "hard"
    assert clip.secret_reasons == ["SG-AWS-ID"]
    clips = ClipsRepo(conn)
    assert not clips.fts_contains(clip.id)            # not in FTS
    assert clips.search_fts("AKIAIOSFODNN7EXAMPLE") == []
    assert not BackupQueueRepo(conn).has(clip.id)     # not queued for backup


def test_backup_queue_refuses_secret_directly(conn):
    outcome = pipeline.ingest(conn, FAKE_AWS_KEY, source_device="d")
    with pytest.raises(SecretEnqueueError):
        BackupQueueRepo(conn).enqueue(outcome.clip.id, "2026-06-12T08:30:00Z")


def test_backup_enqueue_idempotent(conn):
    outcome = pipeline.ingest(conn, "plain text", source_device="d")
    queue = BackupQueueRepo(conn)
    assert queue.enqueue(outcome.clip.id, "2026-06-12T08:30:00Z") is False  # already queued
    assert queue.pending_clip_ids() == [outcome.clip.id]


def _write_counts(conn) -> dict[str, int]:
    return {
        "clips": conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0],
        "fts": conn.execute("SELECT COUNT(*) FROM clips_fts").fetchone()[0],
        "backup_queue": conn.execute("SELECT COUNT(*) FROM backup_queue").fetchone()[0],
        "sync_outbox": conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0],
    }


def test_ingest_rolls_back_clip_when_backup_enqueue_fails(conn, monkeypatch):
    def fail_enqueue(self, clip_id, when, *, commit=True):
        raise RuntimeError("simulated backup queue failure")

    monkeypatch.setattr(BackupQueueRepo, "enqueue", fail_enqueue)

    with pytest.raises(RuntimeError, match="backup queue failure"):
        pipeline.ingest(conn, "atomic backup failure", source_device="d")

    assert _write_counts(conn) == {
        "clips": 0,
        "fts": 0,
        "backup_queue": 0,
        "sync_outbox": 0,
    }


def test_ingest_rolls_back_clip_and_backup_when_sync_emit_fails(conn, monkeypatch):
    from clipvault.store.outbox_repo import OutboxRepo
    from clipvault.sync import engine

    def fail_after_append(conn, clip, when, *, commit=True):
        OutboxRepo(conn).append("clip_new", {"id": clip.id}, when, commit=commit)
        raise RuntimeError("simulated sync outbox failure")

    monkeypatch.setattr(engine, "emit_clip_new", fail_after_append)

    with pytest.raises(RuntimeError, match="sync outbox failure"):
        pipeline.ingest(conn, "atomic sync failure", source_device="d")

    assert _write_counts(conn) == {
        "clips": 0,
        "fts": 0,
        "backup_queue": 0,
        "sync_outbox": 0,
    }


def test_duplicate_touch_seen_joins_outer_transaction(conn):
    first = pipeline.ingest(
        conn,
        "repeatable duplicate",
        source_device="d",
        now_fn=lambda: "2026-07-08T00:00:00Z",
    )
    assert first.status == pipeline.STATUS_NEW
    assert first.clip.times_seen == 1

    conn.execute("BEGIN")
    duplicate = pipeline.ingest(
        conn,
        "repeatable duplicate",
        source_device="d",
        now_fn=lambda: "2026-07-08T00:01:00Z",
    )
    assert duplicate.status == pipeline.STATUS_DUPLICATE
    assert duplicate.clip.times_seen == 2
    conn.rollback()

    persisted = ClipsRepo(conn).get(first.clip.id)
    assert persisted.times_seen == 1
    assert persisted.last_seen_at == "2026-07-08T00:00:00Z"
