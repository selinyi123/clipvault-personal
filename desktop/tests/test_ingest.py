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
