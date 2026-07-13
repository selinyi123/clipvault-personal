"""Concurrency and interrupted-recovery contracts for the backup worker."""

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from clipvault.backup import git_repo, jsonl_store
from clipvault.backup.github_backup import BackupWorker
from clipvault.pipeline import ingest as pipeline
from clipvault.store import db
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


NOW = "2026-06-13T10:00:00Z"
RELPATH = "clips/2026/06/2026-06-13.jsonl"


def _git(
    repo: Path,
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
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


def _worktree_records(repo: Path) -> list[dict]:
    path = repo.joinpath(*RELPATH.split("/"))
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_two_workers_serialize_repo_persistence_without_losing_clips(
    tmp_path,
    monkeypatch,
):
    repo, _ = _repo(tmp_path)
    database = tmp_path / "clipvault.db"
    setup = db.connect(str(database))
    db.migrate(setup)
    first = pipeline.ingest(
        setup,
        "concurrent backup alpha",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    second = pipeline.ingest(
        setup,
        "concurrent backup beta",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    setup.close()

    real_append = jsonl_store.append_latest_clip_states
    active = 0
    max_active = 0
    active_guard = threading.Lock()

    def observed_append(*args, **kwargs):
        nonlocal active, max_active
        with active_guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return real_append(*args, **kwargs)
        finally:
            with active_guard:
                active -= 1

    monkeypatch.setattr(jsonl_store, "append_latest_clip_states", observed_append)
    start = threading.Barrier(3)
    errors: list[BaseException] = []
    results: list[dict] = []

    def run_one(clip_id: str) -> None:
        conn = db.connect(str(database))
        try:
            worker = _worker(conn, repo)
            # Give each independent worker one distinct durable intent. The
            # repository lock, rather than queue ordering, must serialize them.
            worker.queue.claim_pending = lambda _limit=200: [clip_id]
            start.wait(timeout=2)
            results.append(worker.run_once())
        except BaseException as exc:  # preserve worker-thread evidence
            errors.append(exc)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=run_one, args=(first.id,)),
        threading.Thread(target=run_one, args=(second.id,)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert max_active == 1

    verify = db.connect(str(database))
    try:
        queue = BackupQueueRepo(verify)
        clips = ClipsRepo(verify)
        assert queue.state_of(first.id) == "done"
        assert queue.state_of(second.id) == "done"
        assert clips.get(first.id).backed_up_at == NOW
        assert clips.get(second.id).backed_up_at == NOW
        expected = {
            first.id: jsonl_store.serialize_clip(clips.get(first.id)),
            second.id: jsonl_store.serialize_clip(clips.get(second.id)),
        }
    finally:
        verify.close()

    durable_head = git_repo.head_commit(repo)
    assert durable_head is not None
    assert git_repo.commit_latest_clip_lines(
        repo,
        durable_head,
        RELPATH,
        [first.id, second.id],
    ) == expected


def test_worker_does_not_ack_when_durable_head_omits_expected_clip(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, _ = _repo(tmp_path)
    clip = pipeline.ingest(
        conn,
        "durable commit proof must include this clip",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip

    def commit_empty_tree(repo_path, _message, *, paths):
        assert paths == [RELPATH]
        tree = _git(repo_path, "mktree", input_text="").stdout.strip()
        committed = _git(
            repo_path,
            "commit-tree",
            tree,
            "-m",
            "simulated durable commit without expected clip",
        ).stdout.strip()
        _git(repo_path, "update-ref", "refs/heads/main", committed)
        return committed

    monkeypatch.setattr(git_repo, "add_commit", commit_empty_tree)
    stats = _worker(conn, repo).run_once()

    assert stats["committed"] == git_repo.head_commit(repo)
    assert git_repo.commit_latest_clip_lines(
        repo,
        stats["committed"],
        RELPATH,
        [clip.id],
    ) == {}
    assert BackupQueueRepo(conn).state_of(clip.id) == "pending"
    assert ClipsRepo(conn).get(clip.id).backed_up_at is None


def test_recovery_cas_failure_resumes_without_non_append_commit(
    conn,
    tmp_path,
    monkeypatch,
):
    repo, remote = _repo(tmp_path, with_remote=True)
    quarantined = pipeline.ingest(
        conn,
        "public before a newer secret classification",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    retained = pipeline.ingest(
        conn,
        "retained public recovery row",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    worker = _worker(conn, repo)
    worker.run_once()
    contaminated_head = git_repo.head_commit(repo)
    assert contaminated_head is not None

    conn.execute("UPDATE clips SET is_secret=1 WHERE id=?", (quarantined.id,))
    conn.commit()
    worker.push_enabled = True

    real_run = git_repo._run
    failed = False

    def fail_recovery_cas(repo_path, args, *positional, **kwargs):
        nonlocal failed
        if not failed and args[:2] == ["update-ref", "refs/heads/main"]:
            failed = True
            return subprocess.CompletedProcess(
                args,
                returncode=1,
                stdout="",
                stderr="simulated recovery CAS failure",
            )
        return real_run(repo_path, args, *positional, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(git_repo, "_run", fail_recovery_cas)
        assert worker._try_push(monotonic=100.0) is False

    assert failed
    assert git_repo.head_commit(repo) == contaminated_head
    assert {row["id"] for row in _worktree_records(repo)} == {retained.id}
    assert _git(remote, "show-ref", check=False).stdout == ""

    # Force the next run through the dangerous path: it first appends a new
    # pending row to the already-scrubbed worktree. add_commit must refuse to
    # create a non-append child, after which recovery must resume from Git data.
    later = pipeline.ingest(
        conn,
        "public row arriving after interrupted recovery",
        source_device="d",
        now_fn=lambda: NOW,
    ).clip
    recovered = worker.run_once(monotonic=300.0)

    assert recovered["pushed"] is True
    assert BackupQueueRepo(conn).state_of(later.id) == "pending"
    remote_lines = _git(
        remote,
        "show",
        f"refs/heads/main:{RELPATH}",
    ).stdout.splitlines()
    assert {json.loads(line)["id"] for line in remote_lines} == {retained.id}
    assert quarantined.id not in "\n".join(remote_lines)
    assert _git(remote, "rev-list", "--count", "refs/heads/main").stdout.strip() == "1"

    # The safe recovered head is now an append base; the deferred row can be
    # committed and pushed normally on the following pass.
    completed = worker.run_once(monotonic=400.0)
    assert completed["pushed"] is True
    assert BackupQueueRepo(conn).state_of(later.id) == "done"
    final_lines = _git(
        remote,
        "show",
        f"refs/heads/main:{RELPATH}",
    ).stdout.splitlines()
    assert {json.loads(line)["id"] for line in final_lines} == {
        retained.id,
        later.id,
    }
    assert _git(remote, "rev-list", "--count", "refs/heads/main").stdout.strip() == "2"


def test_slow_network_push_does_not_hold_sqlite_writer_lock(
    tmp_path,
    monkeypatch,
):
    repo, _ = _repo(tmp_path, with_remote=True)
    database = tmp_path / "clipvault.db"
    setup = db.connect(str(database))
    db.migrate(setup)
    pipeline.ingest(
        setup,
        "durable candidate before slow push",
        source_device="d",
        now_fn=lambda: NOW,
    )
    _worker(setup, repo).run_once()
    setup.close()

    entered_push = threading.Event()
    release_push = threading.Event()
    real_run = git_repo._run

    def block_network_push(repo_path, args, *positional, **kwargs):
        if args and args[0] == "push":
            entered_push.set()
            assert release_push.wait(timeout=10)
        return real_run(repo_path, args, *positional, **kwargs)

    monkeypatch.setattr(git_repo, "_run", block_network_push)
    results: list[bool] = []
    errors: list[BaseException] = []

    def push_in_worker_thread() -> None:
        conn = db.connect(str(database))
        try:
            results.append(_worker(conn, repo, push_enabled=True)._try_push(100.0))
        except BaseException as exc:
            errors.append(exc)
        finally:
            conn.close()

    thread = threading.Thread(target=push_in_worker_thread)
    thread.start()
    assert entered_push.wait(timeout=5)

    writer = db.connect(str(database))
    started = time.monotonic()
    try:
        concurrent = pipeline.ingest(
            writer,
            "capture must not wait for the network push",
            source_device="d",
            now_fn=lambda: NOW,
        )
        elapsed = time.monotonic() - started
    finally:
        writer.close()
        release_push.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert errors == []
    assert results == [True]
    assert concurrent.status == pipeline.STATUS_NEW
    assert elapsed < 2.0
