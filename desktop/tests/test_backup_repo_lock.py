import gc
import os
import subprocess
import sys
import threading
import time

import pytest

from clipvault.backup import cancellation, repo_lock
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


def test_constructor_keeps_duration_validation_and_public_float_attributes(tmp_path):
    missing_repo = tmp_path / "missing"
    with pytest.raises(TypeError, match="timeout_s must be a non-negative number"):
        RepoWriteLock(missing_repo, timeout_s=True)
    with pytest.raises(ValueError, match="poll_interval_s must be positive"):
        RepoWriteLock(missing_repo, poll_interval_s=0)

    lock = RepoWriteLock(_repo(tmp_path), timeout_s=1, poll_interval_s=1)
    assert type(lock.timeout_s) is float
    assert type(lock.poll_interval_s) is float


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


def test_wait_can_be_cancelled_and_releases_thread_lock(tmp_path):
    repo = _repo(tmp_path)
    stop = threading.Event()
    started = threading.Event()
    result = []

    def contender():
        started.set()
        try:
            with RepoWriteLock(
                repo,
                timeout_s=5.0,
                poll_interval_s=0.01,
                cancel_event=stop,
            ):
                pass
        except cancellation.BackupCancelled:
            result.append("cancelled")

    with RepoWriteLock(repo):
        thread = threading.Thread(target=contender)
        thread.start()
        assert started.wait(1.0)
        stop.set()
        thread.join(1.0)

    assert not thread.is_alive()
    assert result == ["cancelled"]
    with RepoWriteLock(repo, timeout_s=0.1):
        pass


def test_carrier_close_failure_still_releases_thread_lock(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    real_open = repo_lock._open_private_carrier

    class CloseFailureCarrier:
        def __init__(self, carrier):
            self.carrier = carrier

        def __getattr__(self, name):
            return getattr(self.carrier, name)

        def close(self):
            raise OSError("injected carrier close failure")

    monkeypatch.setattr(
        repo_lock,
        "_open_private_carrier",
        lambda path: CloseFailureCarrier(real_open(path)),
    )
    lock = RepoWriteLock(repo)
    lock.acquire()
    with pytest.raises(cancellation.BackupLockCleanupError) as error:
        lock.release()
    assert isinstance(error.value.__cause__, OSError)
    assert str(error.value.__cause__) == "injected carrier close failure"

    monkeypatch.setattr(repo_lock, "_open_private_carrier", real_open)
    del error
    del lock
    gc.collect()
    with RepoWriteLock(repo, timeout_s=0.1):
        pass


def test_release_allows_reacquire_and_lock_file_persists(tmp_path):
    repo = _repo(tmp_path)
    lock_path = repo / ".git" / "clipvault-backup.lock"

    with RepoWriteLock(repo, timeout_s=0.1):
        assert lock_path.exists()
    with RepoWriteLock(repo, timeout_s=0.1):
        assert lock_path.exists()

    assert lock_path.exists()


def test_lock_allows_direct_git_subdirectory_creation_after_construction(tmp_path):
    repo = _repo(tmp_path)
    lock = RepoWriteLock(repo, timeout_s=0.1)

    (repo / ".git" / "objects").mkdir()

    with lock:
        pass


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
