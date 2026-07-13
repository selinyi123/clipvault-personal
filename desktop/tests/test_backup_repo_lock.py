import os
import subprocess
import sys
import threading
import time

import pytest

from clipvault.backup.repo_lock import RepoLockTimeout, RepoWriteLock


def _repo(tmp_path):
    repo = tmp_path / "backup"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_same_process_threads_are_mutually_exclusive(tmp_path):
    repo = _repo(tmp_path)
    started = threading.Event()
    acquired = threading.Event()

    def contender():
        started.set()
        with RepoWriteLock(repo, timeout_s=1.0, poll_interval_s=0.01):
            acquired.set()

    with RepoWriteLock(repo):
        thread = threading.Thread(target=contender)
        thread.start()
        assert started.wait(1.0)
        assert not acquired.wait(0.1)

    thread.join(1.0)
    assert not thread.is_alive()
    assert acquired.is_set()


def test_wait_is_bounded_and_times_out(tmp_path):
    repo = _repo(tmp_path)
    result = []

    def contender():
        started_at = time.monotonic()
        with pytest.raises(RepoLockTimeout):
            with RepoWriteLock(repo, timeout_s=0.1, poll_interval_s=0.01):
                pass
        result.append(time.monotonic() - started_at)

    with RepoWriteLock(repo):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(1.0)

    assert not thread.is_alive()
    assert 0.08 <= result[0] < 0.75


def test_release_allows_reacquire_and_lock_file_persists(tmp_path):
    repo = _repo(tmp_path)
    lock_path = repo / ".git" / "clipvault-backup.lock"

    with RepoWriteLock(repo, timeout_s=0.1):
        assert lock_path.exists()
    with RepoWriteLock(repo, timeout_s=0.1):
        assert lock_path.exists()

    assert lock_path.exists()


def test_context_exception_releases_lock(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(RuntimeError, match="simulated failure"):
        with RepoWriteLock(repo, timeout_s=0.1):
            raise RuntimeError("simulated failure")

    with RepoWriteLock(repo, timeout_s=0.1):
        pass


def test_other_process_observes_os_lock_timeout(tmp_path):
    repo = _repo(tmp_path)
    script = (
        "from clipvault.backup.repo_lock import RepoLockTimeout, RepoWriteLock; "
        f"repo={str(repo)!r}; "
        "\ntry:\n"
        "  with RepoWriteLock(repo, timeout_s=0.1, poll_interval_s=0.01): pass\n"
        "except RepoLockTimeout:\n"
        "  raise SystemExit(23)\n"
    )

    with RepoWriteLock(repo):
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    assert completed.returncode == 23, completed.stderr


def test_hardlinked_lock_carrier_is_rejected_without_touching_external_file(
    tmp_path,
):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-private.bin"
    outside.write_bytes(b"")
    carrier = repo / ".git" / "clipvault-backup.lock"
    try:
        os.link(outside, carrier)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")
    assert outside.stat().st_nlink == 2

    with pytest.raises(ValueError, match="carrier is unsafe"):
        with RepoWriteLock(repo, timeout_s=0.1):
            pass

    assert outside.read_bytes() == b""
    assert carrier.samefile(outside)
    assert outside.stat().st_nlink == 2
