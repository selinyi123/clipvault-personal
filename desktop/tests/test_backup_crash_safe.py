import json
import os
import subprocess
from pathlib import Path

import pytest

from clipvault.backup import git_repo, jsonl_store
from clipvault.backup.github_backup import BackupWorker
from clipvault.pipeline import ingest as pipeline
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


NOW = "2026-06-13T10:00:00Z"


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path, *, with_remote: bool = False) -> tuple[Path, Path]:
    repo = tmp_path / "backup"
    remote = tmp_path / "remote.git"
    git_repo.init(repo)
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    if with_remote:
        subprocess.run(
            ["git", "init", "--bare", "-b", "main", str(remote)],
            check=True,
            capture_output=True,
        )
        _git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def _worker(conn, repo: Path, *, push_enabled: bool = False) -> BackupWorker:
    return BackupWorker(
        conn,
        str(repo),
        push_enabled=push_enabled,
        now_fn=lambda: NOW,
    )


def _daily_lines(repo: Path) -> list[dict]:
    path = repo / "clips" / "2026" / "06" / "2026-06-13.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_commit_then_db_ack_failure_converges_without_duplicate(conn, tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path)
    out = pipeline.ingest(
        conn,
        "commit survives database acknowledgement crash",
        source_device="d",
        now_fn=lambda: NOW,
    )
    first = _worker(conn, repo)
    real_mark_done = first.queue.mark_done

    def fail_after_queue_update(clip_id, when, *, commit=True):
        assert real_mark_done(clip_id, when, commit=commit) is True
        raise RuntimeError("simulated database acknowledgement crash")

    monkeypatch.setattr(first.queue, "mark_done", fail_after_queue_update)

    with pytest.raises(RuntimeError, match="acknowledgement crash"):
        first.run_once()

    first_head = git_repo.head_commit(repo)
    assert first_head is not None
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "pending"
    assert ClipsRepo(conn).get(out.clip.id).backed_up_at is None
    assert len(_daily_lines(repo)) == 1
    assert not conn.in_transaction

    second = _worker(conn, repo)
    stats = second.run_once()

    assert stats["committed"] is None
    assert git_repo.head_commit(repo) == first_head
    assert len(_daily_lines(repo)) == 1
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"
    assert ClipsRepo(conn).get(out.clip.id).backed_up_at == NOW


def test_latest_state_dedupe_preserves_public_deleted_public(conn, tmp_path):
    repo, remote = _repo(tmp_path, with_remote=True)
    out = pipeline.ingest(
        conn,
        "state cycles must restore the latest value",
        source_device="d",
        now_fn=lambda: NOW,
    )
    worker = _worker(conn, repo)
    worker.run_once()

    conn.execute("UPDATE clips SET deleted=1 WHERE id=?", (out.clip.id,))
    conn.commit()
    BackupQueueRepo(conn).reenqueue(out.clip.id, NOW)
    worker.run_once()

    conn.execute("UPDATE clips SET deleted=0 WHERE id=?", (out.clip.id,))
    conn.commit()
    BackupQueueRepo(conn).reenqueue(out.clip.id, NOW)
    worker.run_once()

    records = [row for row in _daily_lines(repo) if row["id"] == out.clip.id]
    assert [row["deleted"] for row in records] == [False, True, False]
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"
    worker.push_enabled = True
    assert worker._try_push(monotonic=100.0) is True
    remote_lines = _git(
        remote,
        "show",
        "refs/heads/main:clips/2026/06/2026-06-13.jsonl",
    ).stdout.splitlines()
    assert [json.loads(line)["deleted"] for line in remote_lines] == [
        False,
        True,
        False,
    ]


def test_state_change_between_commit_and_ack_stays_pending(conn, tmp_path, monkeypatch):
    repo, _ = _repo(tmp_path)
    out = pipeline.ingest(
        conn,
        "delete races the local commit",
        source_device="d",
        now_fn=lambda: NOW,
    )
    real_add_commit = git_repo.add_commit

    def commit_then_delete(*args, **kwargs):
        committed = real_add_commit(*args, **kwargs)
        conn.execute("UPDATE clips SET deleted=1 WHERE id=?", (out.clip.id,))
        conn.commit()
        BackupQueueRepo(conn).reenqueue(out.clip.id, NOW)
        return committed

    with monkeypatch.context() as patch:
        patch.setattr(git_repo, "add_commit", commit_then_delete)
        _worker(conn, repo).run_once()

    assert BackupQueueRepo(conn).state_of(out.clip.id) == "pending"
    assert ClipsRepo(conn).get(out.clip.id).backed_up_at is None

    _worker(conn, repo).run_once()
    records = [row for row in _daily_lines(repo) if row["id"] == out.clip.id]
    assert [row["deleted"] for row in records] == [False, True]
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"


def test_unpublished_clip_reclassified_secret_never_reaches_remote(conn, tmp_path):
    repo, remote = _repo(tmp_path)
    out = pipeline.ingest(
        conn,
        "initially public unpublished backup",
        source_device="d",
        now_fn=lambda: NOW,
    )
    worker = _worker(conn, repo, push_enabled=False)
    worker.run_once()
    assert BackupQueueRepo(conn).state_of(out.clip.id) == "done"

    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (out.clip.id,))
    conn.commit()
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    _git(repo, "remote", "add", "origin", str(remote))
    worker.push_enabled = True

    assert worker._try_push(monotonic=100.0) is False
    assert _git(remote, "show-ref", check=False).stdout == ""


def test_missing_managed_worktree_is_restored_from_durable_head(conn, tmp_path):
    repo, _ = _repo(tmp_path)
    first = pipeline.ingest(
        conn,
        "durable row before a lost worktree entry",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    worker = _worker(conn, repo)
    worker.run_once()
    path = repo / "clips" / "2026" / "06" / "2026-06-13.jsonl"
    durable = path.read_bytes()
    path.unlink()

    second = pipeline.ingest(
        conn,
        "new row after a lost worktree entry",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    result = worker.run_once()

    assert result["committed"] is not None
    assert path.read_bytes().startswith(durable)
    assert {row["id"] for row in _daily_lines(repo)} == {first.id, second.id}
    assert BackupQueueRepo(conn).state_of(second.id) == "done"
    assert _git(repo, "status", "--porcelain").stdout == ""


def test_complete_old_prefix_is_restored_before_new_append(conn, tmp_path):
    repo, _ = _repo(tmp_path)
    first = pipeline.ingest(
        conn,
        "first durable prefix row",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    second = pipeline.ingest(
        conn,
        "second durable prefix row",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    worker = _worker(conn, repo)
    worker.run_once()
    path = repo / "clips" / "2026" / "06" / "2026-06-13.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    assert len(lines) == 2
    path.write_bytes(lines[0])

    third = pipeline.ingest(
        conn,
        "third row after prefix recovery",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    worker.run_once()

    assert [row["id"] for row in _daily_lines(repo)] == [
        first.id,
        second.id,
        third.id,
    ]
    assert _git(repo, "status", "--porcelain").stdout == ""


def test_worker_rejects_hardlinked_daily_file_before_git_commit(conn, tmp_path):
    repo, _ = _repo(tmp_path)
    outcome = pipeline.ingest(
        conn,
        "safe row must not launder linked content",
        source_device="d",
        now_fn=lambda: NOW,
    )
    target = repo / Path(jsonl_store.daily_relpath(outcome.clip.created_at))
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside-private.jsonl"
    secret = b'{"id":"outside","content":"AKIAIOSFODNN7EXAMPLE"}\n'
    outside.write_bytes(secret)
    try:
        os.link(outside, target)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")

    with pytest.raises(jsonl_store.JsonlIntegrityError, match="private regular"):
        _worker(conn, repo).run_once()

    assert git_repo.head_commit(repo) is None
    assert BackupQueueRepo(conn).state_of(outcome.clip.id) == "pending"
    assert outside.read_bytes() == secret
    assert target.samefile(outside)
    assert outside.stat().st_nlink == 2
