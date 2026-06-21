"""S003 gates: C1-C6, C8. (C7 is in test_backup_git.py.)"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from clipvault.backup import git_repo, jsonl_store
from clipvault.backup.github_backup import BackupWorker
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def work_repo(tmp_path):
    """A git work-copy whose origin is a local bare repo."""
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)],
                   check=True, capture_output=True)
    repo = tmp_path / "backup"
    git_repo.init(repo)  # creates the work copy already on branch main
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "remote", "add", "origin", str(bare))
    return repo, bare


def test_c1_public_clip_serialized(conn, work_repo):
    repo, _ = work_repo
    # Fix the ingest clock too: the daily JSONL path derives from clip.created_at,
    # so a real wall clock would make this assert date-dependent (rollover flake).
    out = pipeline.ingest(conn, "hello backup", source_device="d",
                          now_fn=lambda: "2026-06-13T10:00:00Z")
    worker = BackupWorker(conn, str(repo), push_enabled=False,
                          now_fn=lambda: "2026-06-13T10:00:00Z")
    stats = worker.run_once()
    assert stats["written"] == 1

    jsonl = repo / "clips" / "2026" / "06" / "2026-06-13.jsonl"
    assert jsonl.exists()
    obj = json.loads(jsonl.read_text(encoding="utf-8").strip())
    assert obj["id"] == out.clip.id
    assert obj["content"] == "hello backup"
    assert obj["content_hash"] == out.clip.content_hash
    assert ClipsRepo(conn).get(out.clip.id).backed_up_at == "2026-06-13T10:00:00Z"
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"


def test_c2_gate_c_drops_secret(conn, work_repo):
    repo, _ = work_repo
    # Force a secret clip into the queue, bypassing gate B.
    out = pipeline.ingest(conn, "plain", source_device="d")
    conn.execute("UPDATE clips SET content=?, is_secret=0 WHERE id=?",
                 (FAKE_AWS_KEY, out.clip.id))  # poison content after enqueue
    conn.commit()
    worker = BackupWorker(conn, str(repo), push_enabled=False)
    stats = worker.run_once()
    assert stats["written"] == 0 and stats["dropped"] == 1
    assert not (repo / "clips").exists()
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "dropped_secret"


def test_c3_commit_and_push(conn, work_repo):
    repo, bare = work_repo
    pipeline.ingest(conn, "commit me", source_device="d")
    worker = BackupWorker(conn, str(repo), push_enabled=True,
                          now_fn=lambda: "2026-06-13T10:00:00Z")
    stats = worker.run_once()
    assert stats["committed"] is not None
    assert stats["pushed"] is True
    log = subprocess.run(["git", "-C", str(bare), "log", "--oneline"],
                         capture_output=True, text=True)
    assert "backup:" in log.stdout


def test_c3_commit_failure_keeps_queue_pending(conn, work_repo, monkeypatch):
    repo, _ = work_repo
    out = pipeline.ingest(conn, "commit must happen before done", source_device="d",
                          now_fn=lambda: "2026-06-13T10:00:00Z")

    def boom(*args, **kwargs):
        raise git_repo.GitError("simulated commit failure")

    monkeypatch.setattr(git_repo, "add_commit", boom)
    worker = BackupWorker(conn, str(repo), push_enabled=False)
    with pytest.raises(git_repo.GitError):
        worker.run_once()
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "pending"
    assert ClipsRepo(conn).get(out.clip.id).backed_up_at is None


def test_c3_commit_failure_retry_does_not_duplicate_jsonl(conn, work_repo, monkeypatch):
    repo, _ = work_repo
    out = pipeline.ingest(conn, "retry idempotently", source_device="d",
                          now_fn=lambda: "2026-06-13T10:00:00Z")
    jsonl = repo / "clips" / "2026" / "06" / "2026-06-13.jsonl"
    real_add_commit = git_repo.add_commit

    def boom(*args, **kwargs):
        raise git_repo.GitError("simulated commit failure")

    monkeypatch.setattr(git_repo, "add_commit", boom)
    worker = BackupWorker(conn, str(repo), push_enabled=False,
                          now_fn=lambda: "2026-06-13T10:00:00Z")
    with pytest.raises(git_repo.GitError):
        worker.run_once()
    assert jsonl.exists()
    assert len(jsonl.read_text(encoding="utf-8").splitlines()) == 1

    monkeypatch.setattr(git_repo, "add_commit", real_add_commit)
    stats = worker.run_once()
    assert stats["committed"] is not None
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == out.clip.id
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"


def test_c4_push_failure_backs_off_then_recovers(conn, tmp_path, monkeypatch):
    repo = tmp_path / "backup"
    git_repo.init(repo)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    pipeline.ingest(conn, "resilient", source_device="d")
    worker = BackupWorker(conn, str(repo), push_enabled=True)

    stats = worker.run_once(monotonic=100.0)
    assert stats["committed"] is not None     # data is safe locally
    assert stats["pushed"] is False
    assert worker._backoff_s == 120           # 60 -> 120
    assert worker._monotonic_blocked_until == 220.0
    assert git_repo.head_commit(repo) is not None

    # Fix the remote; a too-early retry is still blocked, a later one succeeds.
    subprocess.run(["git", "init", "--bare", str(tmp_path / "does-not-exist.git")],
                   check=True, capture_output=True)
    assert worker._try_push(monotonic=150.0) is False   # within backoff window
    assert worker._try_push(monotonic=300.0) is True
    assert worker._backoff_s == 60                       # reset after success


def test_c4_run_once_retries_previous_unpushed_commit(conn, tmp_path):
    repo = tmp_path / "backup"
    remote = tmp_path / "remote.git"
    git_repo.init(repo)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "remote", "add", "origin", str(remote))
    pipeline.ingest(conn, "retry later", source_device="d")
    worker = BackupWorker(conn, str(repo), push_enabled=True)
    assert worker.run_once(monotonic=100.0)["pushed"] is False

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    stats = worker.run_once(monotonic=300.0)
    assert stats["written"] == 0
    assert stats["pushed"] is True


def test_c5_no_double_backup(conn, work_repo):
    repo, _ = work_repo
    pipeline.ingest(conn, "once only", source_device="d")
    worker = BackupWorker(conn, str(repo), push_enabled=False)
    assert worker.run_once()["written"] == 1
    assert worker.run_once()["written"] == 0   # already done


def test_c6_restore_roundtrip(conn, work_repo, tmp_path):
    repo, _ = work_repo
    contents = ["alpha", "beta gamma", "https://example.com", "git status", FAKE_AWS_KEY]
    public_hashes = set()
    for text in contents:
        out = pipeline.ingest(conn, text, source_device="d")
        if not out.clip.is_secret:
            public_hashes.add(out.clip.content_hash)
    BackupWorker(conn, str(repo), push_enabled=False).run_once()

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import restore
    out_db = tmp_path / "restored.db"
    count = restore.restore(str(repo), str(out_db))

    from clipvault.store import db
    rconn = db.connect(str(out_db))
    restored = {c.content_hash for c in ClipsRepo(rconn).all_clips()}
    rconn.close()
    assert count == len(public_hashes)          # secret never backed up
    assert restored == public_hashes


def test_c8_logs_no_content(conn, work_repo, caplog):
    repo, _ = work_repo
    pipeline.ingest(conn, "secret-free payload words", source_device="d")
    # poison one to trigger gate C log
    out = pipeline.ingest(conn, "tmp", source_device="d")
    conn.execute("UPDATE clips SET content=? WHERE id=?", (FAKE_AWS_KEY, out.clip.id))
    conn.commit()
    with caplog.at_level(logging.DEBUG, logger="clipvault.backup"):
        BackupWorker(conn, str(repo), push_enabled=False).run_once()
    assert "payload" not in caplog.text
    assert "AKIA" not in caplog.text
