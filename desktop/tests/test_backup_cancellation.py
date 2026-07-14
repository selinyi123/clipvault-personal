"""Shutdown cancellation for backup Git work and runtime ownership."""

from __future__ import annotations

import errno
import logging
import math
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from clipvault.backup import cancellation, git_repo, process_tree
from clipvault.backup.github_backup import BackupWorker
from clipvault.backup.process_tree import ProcessTreeController
from clipvault.backup.repo_lock import RepoWriteLock
from clipvault.config import Config
from clipvault.pipeline import ingest as pipeline
from clipvault.runtime.app import ClipVaultRuntime, RuntimeAdapters
from clipvault.store.backup_queue_repo import BackupQueueRepo
from clipvault.store.clips_repo import ClipsRepo


class _BlockingProcess:
    def __init__(self, on_wait=None):
        self.pid = 4242
        self.returncode = None
        self.inputs = []
        self.on_wait = on_wait

    def poll(self):
        return self.returncode

    def communicate(self, input=None, timeout=None):
        self.inputs.append(input)
        if self.returncode is not None:
            return b"", b""
        if self.on_wait is not None:
            self.on_wait()
        raise subprocess.TimeoutExpired(["git"], timeout)

    def kill(self):
        self.returncode = -9


class _CompletingProcess(_BlockingProcess):
    def __init__(self, *, returncode=0, stdout=b"", stderr=b""):
        super().__init__()
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def communicate(self, input=None, timeout=None):
        self.inputs.append(input)
        return self.stdout, self.stderr


class _FakeProcessTree:
    popen_kwargs = {}

    def __init__(self):
        self.attached = False
        self.terminated = False
        self.closed = False

    def attach(self, _process):
        self.attached = True

    def terminate(self, process):
        self.terminated = True
        process.returncode = -9

    def close(self):
        self.closed = True


def _configured_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "backup"
    git_repo.init(repo)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Backup Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "backup@example.com"],
        check=True,
        capture_output=True,
    )
    return repo


def test_pre_cancelled_git_command_never_spawns(monkeypatch):
    stop = threading.Event()
    stop.set()

    def forbidden_spawn(*_args, **_kwargs):
        raise AssertionError("cancelled backup must not spawn Git")

    monkeypatch.setattr(subprocess, "Popen", forbidden_spawn)

    with pytest.raises(cancellation.BackupCancelled):
        with cancellation.cancellation_scope(stop):
            git_repo._run("private-repo", ["status"])


def test_running_git_cancels_reaps_and_preserves_noninteractive_env(monkeypatch):
    stop = threading.Event()
    process = _BlockingProcess(on_wait=stop.set)
    controller = _FakeProcessTree()
    captured = {}

    def spawn(_cmd, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    with cancellation.cancellation_scope(stop):
        with pytest.raises(cancellation.BackupCancelled):
            git_repo._run(
                "private-repo",
                ["hash-object", "--stdin"],
                env={
                    "GIT_TERMINAL_PROMPT": "1",
                    "GCM_INTERACTIVE": "Always",
                    "GIT_NO_REPLACE_OBJECTS": "0",
                    "GIT_ASKPASS": "configured-git-helper",
                    "SSH_ASKPASS": "configured-ssh-helper",
                    "SSH_ASKPASS_REQUIRE": "force",
                },
                input_bytes=b"public test line\n",
            )

    assert process.returncode == -9
    assert controller.attached and controller.terminated and controller.closed
    assert process.inputs.count(b"public test line\n") == 1
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GCM_INTERACTIVE"] == "Never"
    assert captured["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert captured["env"]["GIT_ASKPASS"] == "configured-git-helper"
    assert captured["env"]["SSH_ASKPASS"] == "configured-ssh-helper"
    assert captured["env"]["SSH_ASKPASS_REQUIRE"] == "force"


def test_git_timeout_keeps_established_124_result(monkeypatch):
    process = _BlockingProcess()
    controller = _FakeProcessTree()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    result = git_repo._run("private-repo", ["status"], timeout=0)

    assert result.returncode == 124
    assert result.stdout == ""
    assert "private-repo" not in result.stderr
    assert controller.terminated and controller.closed


@pytest.mark.parametrize("timeout", [True, -1, math.nan, math.inf, "60"])
def test_invalid_git_timeout_fails_before_spawn(monkeypatch, timeout):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("invalid timeout spawned Git"),
    )

    with pytest.raises(ValueError, match="invalid Git command timeout"):
        git_repo._run("private-repo", ["status"], timeout=timeout)


def test_git_runner_preserves_nonzero_result_and_safe_decoding(monkeypatch):
    process = _CompletingProcess(
        returncode=7,
        stdout=b"out\x80",
        stderr=b"err\xff",
    )
    controller = _FakeProcessTree()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    result = git_repo._run("private-repo", ["status"])

    assert result.returncode == 7
    assert result.stdout.encode("utf-8", errors="surrogateescape") == b"out\x80"
    assert result.stderr == "err�"
    assert controller.attached and not controller.terminated and controller.closed


def test_started_ref_update_finishes_before_shutdown_checkpoint(monkeypatch):
    stop = threading.Event()

    class RefUpdateProcess(_BlockingProcess):
        def communicate(self, input=None, timeout=None):
            self.inputs.append(input)
            if len(self.inputs) == 1:
                stop.set()
                raise subprocess.TimeoutExpired(["git"], timeout)
            self.returncode = 0
            return b"", b""

    process = RefUpdateProcess()
    controller = _FakeProcessTree()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    with cancellation.cancellation_scope(stop):
        result = git_repo._run(
            "private-repo",
            ["update-ref", "refs/heads/main", "a" * 40, "b" * 40],
        )

    assert result.returncode == 0
    assert not controller.terminated
    assert controller.closed


def test_temporary_index_update_remains_interruptible(monkeypatch):
    stop = threading.Event()
    process = _BlockingProcess(on_wait=stop.set)
    controller = _FakeProcessTree()
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    with cancellation.cancellation_scope(stop):
        with pytest.raises(cancellation.BackupCancelled):
            git_repo._run(
                "private-repo",
                ["update-index", "--add", "--cacheinfo", "entry"],
                env={"GIT_INDEX_FILE": "disposable-index"},
            )

    assert controller.terminated and controller.closed
    assert process.poll() is not None


def test_ref_update_timeout_during_shutdown_is_cancellation(monkeypatch):
    stop = threading.Event()
    process = _BlockingProcess()
    controller = _FakeProcessTree()

    def spawn(*_args, **_kwargs):
        stop.set()
        return process

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(
        git_repo,
        "ProcessTreeController",
        lambda **_kwargs: controller,
    )

    with cancellation.cancellation_scope(stop):
        with pytest.raises(cancellation.BackupCancelled):
            git_repo._run(
                "private-repo",
                ["update-ref", "refs/heads/main", "a" * 40, "b" * 40],
                timeout=0.01,
            )

    assert controller.terminated and controller.closed
    assert process.poll() is not None


def test_unreapable_git_is_a_terminal_control_failure():
    class UnreapableProcess(_BlockingProcess):
        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(["git"], timeout)

        def kill(self):
            pass

    process = UnreapableProcess()

    with pytest.raises(cancellation.BackupProcessTerminationError):
        git_repo._reap_process(process)


def test_process_controller_uses_owned_platform_boundary():
    controller = ProcessTreeController(grace_s=0.25)
    try:
        if os.name == "nt":
            flags = controller.popen_kwargs["creationflags"]
            assert flags & subprocess.CREATE_NEW_PROCESS_GROUP
            assert flags & subprocess.CREATE_NO_WINDOW
            assert flags & 0x00000004  # CREATE_SUSPENDED: assign Job before resume
        else:
            assert controller.popen_kwargs == {"start_new_session": True}
    finally:
        controller.close()


def test_posix_group_control_failure_is_terminal(monkeypatch):
    def denied(_process_group, _signal):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(process_tree.os, "killpg", denied, raising=False)

    with pytest.raises(
        cancellation.BackupProcessTerminationError,
        match="process group control failed",
    ):
        process_tree._signal_posix_group(4242, signal.SIGTERM)


def test_posix_termination_clears_owned_group_before_close(monkeypatch):
    calls = []
    sigkill = getattr(signal, "SIGKILL", 9)

    def signal_group(process_group, selected_signal):
        calls.append((process_group, selected_signal))
        return True

    monkeypatch.setattr(process_tree, "_signal_posix_group", signal_group)
    monkeypatch.setattr(process_tree.signal, "SIGKILL", sigkill, raising=False)
    controller = object.__new__(ProcessTreeController)
    controller.grace_s = 0.0
    controller._windows_job = None
    controller._process_group = 4242

    controller.terminate(_BlockingProcess())
    controller.close()

    assert calls == [
        (4242, signal.SIGTERM),
        (4242, sigkill),
    ]
    assert controller._process_group is None


def test_process_tree_termination_stops_descendant(tmp_path):
    started = tmp_path / "descendant-started"
    leaked = tmp_path / "descendant-leaked"
    descendant = (
        "import time; from pathlib import Path; "
        f"Path({str(started)!r}).write_text('started'); "
        "time.sleep(0.75); "
        f"Path({str(leaked)!r}).write_text('leaked')"
    )
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
        "time.sleep(30)"
    )
    controller = ProcessTreeController(grace_s=0.25)
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        **controller.popen_kwargs,
    }
    process = subprocess.Popen([sys.executable, "-c", parent], **kwargs)
    try:
        controller.attach(process)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists(), "descendant did not start"

        controller.terminate(process)
        git_repo._reap_process(process)
        time.sleep(1.0)

        assert process.poll() is not None
        assert not leaked.exists()
    finally:
        controller.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_process_tree_termination_owns_descendant_after_leader_exit(tmp_path):
    started = tmp_path / "orphan-started"
    leaked = tmp_path / "orphan-leaked"
    descendant = (
        "import time; from pathlib import Path; "
        f"Path({str(started)!r}).write_text('started'); "
        "time.sleep(0.75); "
        f"Path({str(leaked)!r}).write_text('leaked')"
    )
    parent = (
        "import subprocess, sys; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}])"
    )
    controller = ProcessTreeController(grace_s=0.25)
    process = subprocess.Popen(
        [sys.executable, "-c", parent],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **controller.popen_kwargs,
    )
    try:
        controller.attach(process)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists(), "descendant did not start"
        assert process.wait(timeout=2) == 0

        controller.terminate(process)
        git_repo._reap_process(process)
        time.sleep(1.0)

        assert not leaked.exists()
    finally:
        controller.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_process_tree_close_stops_lingering_owned_helper_after_success(tmp_path):
    started = tmp_path / "helper-started"
    leaked = tmp_path / "helper-leaked"
    descendant = (
        "import time; from pathlib import Path; "
        f"Path({str(started)!r}).write_text('started'); "
        "time.sleep(0.75); "
        f"Path({str(leaked)!r}).write_text('leaked')"
    )
    parent = (
        "import subprocess, sys; "
        "subprocess.Popen("
        f"[sys.executable, '-c', {descendant!r}], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True)"
    )
    controller = ProcessTreeController(grace_s=0.25)
    process = subprocess.Popen(
        [sys.executable, "-c", parent],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **controller.popen_kwargs,
    )
    try:
        controller.attach(process)
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started.exists(), "owned helper did not start"
        assert process.communicate(timeout=2)[0] == b""

        controller.close()
        time.sleep(1.0)

        assert not leaked.exists()
    finally:
        controller.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_cancel_after_local_commit_remains_recoverable(
    conn,
    tmp_path,
    monkeypatch,
):
    repo = _configured_repo(tmp_path)
    outcome = pipeline.ingest(
        conn,
        "durable backup cancellation recovery",
        source_device="desktop-test",
        now_fn=lambda: "2026-07-13T00:00:00Z",
    )
    worker = BackupWorker(conn, str(repo), push_enabled=False)
    real_add_commit = git_repo.add_commit

    def commit_then_stop(*args, **kwargs):
        committed = real_add_commit(*args, **kwargs)
        worker.request_stop()
        return committed

    monkeypatch.setattr(git_repo, "add_commit", commit_then_stop)
    with pytest.raises(cancellation.BackupCancelled):
        worker.run_once()

    assert git_repo.head_commit(repo) is not None
    assert BackupQueueRepo(conn).state_of(outcome.clip.id) == "pending"
    assert ClipsRepo(conn).get(outcome.clip.id).backed_up_at is None

    monkeypatch.setattr(git_repo, "add_commit", real_add_commit)
    recovered = BackupWorker(conn, str(repo), push_enabled=False).run_once()

    assert recovered["written"] == 1
    assert BackupQueueRepo(conn).state_of(outcome.clip.id) == "done"
    relpath = repo / "clips" / "2026" / "07" / "2026-07-13.jsonl"
    assert len(relpath.read_text(encoding="utf-8").splitlines()) == 1


def test_push_cancellation_does_not_backoff_or_log_failure(
    conn,
    tmp_path,
    monkeypatch,
    caplog,
):
    repo = _configured_repo(tmp_path)
    worker = BackupWorker(conn, str(repo), push_enabled=True)
    monkeypatch.setattr(
        worker,
        "_authorize_with_safe_recovery",
        lambda **_kwargs: object(),
    )

    def cancel_push(*_args, **_kwargs):
        worker.request_stop()
        raise cancellation.BackupCancelled("backup shutdown requested")

    monkeypatch.setattr(git_repo, "push", cancel_push)
    with caplog.at_level(logging.ERROR, logger="clipvault.backup"):
        with pytest.raises(cancellation.BackupCancelled):
            worker._try_push(monotonic=100.0)

    assert worker._backoff_s == 60
    assert worker._monotonic_blocked_until == 0.0
    assert "push failed" not in caplog.text
    with RepoWriteLock(repo, timeout_s=0.1):
        pass


class _FirstIntervalEvent:
    """Let a direct _backup_loop test enter one scheduled run immediately."""

    def __init__(self):
        self._event = threading.Event()
        self._first_interval = True

    def is_set(self):
        return self._event.is_set()

    def set(self):
        self._event.set()

    def wait(self, timeout=None):
        if self._first_interval and timeout is not None and timeout >= 60:
            self._first_interval = False
            return False
        return self._event.wait(timeout)


def test_runtime_stop_cancels_inflight_backup_and_closes_connection(
    tmp_path,
    caplog,
):
    started = threading.Event()
    cancelled = threading.Event()
    closed = threading.Event()

    class BlockingWorker:
        def run_once(self, monotonic=0.0):
            started.set()
            cancelled.wait(5)
            raise cancellation.BackupCancelled("backup shutdown requested")

        def request_stop(self):
            cancelled.set()

    class TrackedConnection:
        def __init__(self):
            self.inner = sqlite3.connect(":memory:")

        def close(self):
            self.inner.close()
            closed.set()

    config = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="backup-runtime-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        backup_repo_path=str(tmp_path / "backup"),
        backup_enabled=True,
    )
    adapters = RuntimeAdapters(
        connect=lambda _path: TrackedConnection(),
        backup_worker_factory=lambda _conn, _path: BlockingWorker(),
    )
    runtime = ClipVaultRuntime(config, adapters=adapters)
    runtime.stop_event = _FirstIntervalEvent()
    thread = runtime._new_thread("backup-worker", runtime._backup_loop)
    runtime._threads.append(thread)

    with caplog.at_level(logging.ERROR):
        thread.start()
        assert started.wait(2)
        before = time.monotonic()
        runtime.request_stop()
        runtime.request_stop()
        assert runtime.close(2) == []

    assert not thread.is_alive()
    assert time.monotonic() - before < 1.0
    assert closed.is_set()
    assert runtime._backup_worker is None
    assert runtime.health()["backup-worker"] == {
        "alive": False,
        "error_class": None,
    }
    assert runtime.terminal_errors() == {}
    assert "backup worker failed" not in caplog.text
    assert "runtime worker stopped" not in caplog.text


def test_runtime_stop_during_backup_factory_is_forwarded(tmp_path):
    factory_started = threading.Event()
    release_factory = threading.Event()
    worker_stopped = threading.Event()
    connection_closed = threading.Event()

    class Worker:
        def request_stop(self):
            worker_stopped.set()

        def run_once(self, monotonic=0.0):
            raise AssertionError("stopped runtime must not enter backup work")

    class Connection:
        def close(self):
            connection_closed.set()

    def factory(_conn, _path):
        factory_started.set()
        release_factory.wait(2)
        return Worker()

    config = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="backup-factory-race-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        backup_repo_path=str(tmp_path / "backup"),
        backup_enabled=True,
    )
    runtime = ClipVaultRuntime(
        config,
        adapters=RuntimeAdapters(
            connect=lambda _path: Connection(),
            backup_worker_factory=factory,
        ),
    )
    runtime.stop_event = _FirstIntervalEvent()
    thread = runtime._new_thread("backup-worker", runtime._backup_loop)
    runtime._threads.append(thread)
    thread.start()
    assert factory_started.wait(1)

    runtime.request_stop()
    release_factory.set()
    assert runtime.close(2) == []

    assert worker_stopped.is_set()
    assert connection_closed.is_set()
    assert runtime.terminal_errors() == {}


def test_runtime_exit_drain_waits_for_persistent_backup_section(tmp_path):
    started = threading.Event()
    allow_finish = threading.Event()
    stop_forwarded = threading.Event()
    connection_closed = threading.Event()

    class PersistentWriterWorker:
        def request_stop(self):
            stop_forwarded.set()

        def run_once(self, monotonic=0.0):
            started.set()
            allow_finish.wait(2)
            return {"written": 0, "dropped": 0, "pushed": False}

    class Connection:
        def close(self):
            connection_closed.set()

    config = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="backup-exit-drain-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        backup_repo_path=str(tmp_path / "backup"),
        backup_enabled=True,
    )
    runtime = ClipVaultRuntime(
        config,
        adapters=RuntimeAdapters(
            connect=lambda _path: Connection(),
            backup_worker_factory=lambda _conn, _path: PersistentWriterWorker(),
        ),
    )
    runtime.stop_event = _FirstIntervalEvent()
    thread = runtime._new_thread("backup-worker", runtime._backup_loop)
    runtime._threads.append(thread)
    thread.start()
    assert started.wait(1)

    assert runtime.close(0.01) == ["backup-worker"]
    assert stop_forwarded.is_set()
    assert not connection_closed.is_set()
    release = threading.Timer(0.05, allow_finish.set)
    release.start()
    try:
        assert runtime.drain_backup_before_exit(1.0) == []
    finally:
        release.cancel()
        allow_finish.set()
        thread.join(1)

    assert connection_closed.is_set()
    assert runtime.terminal_errors() == {}


@pytest.mark.parametrize(
    "error_type",
    [
        cancellation.BackupProcessTerminationError,
        cancellation.BackupLockCleanupError,
    ],
)
def test_backup_control_failure_is_terminal_runtime_health(tmp_path, error_type):
    connection_closed = threading.Event()

    class FailingWorker:
        def request_stop(self):
            pass

        def run_once(self, monotonic=0.0):
            raise error_type("backup control cleanup failed")

    class Connection:
        def close(self):
            connection_closed.set()

    config = Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="backup-terminal-health-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        backup_repo_path=str(tmp_path / "backup"),
        backup_enabled=True,
    )
    runtime = ClipVaultRuntime(
        config,
        adapters=RuntimeAdapters(
            connect=lambda _path: Connection(),
            backup_worker_factory=lambda _conn, _path: FailingWorker(),
        ),
    )
    runtime.stop_event = _FirstIntervalEvent()
    thread = runtime._new_thread("backup-worker", runtime._backup_loop)
    runtime._threads.append(thread)
    thread.start()
    thread.join(2)

    assert not thread.is_alive()
    assert connection_closed.is_set()
    assert runtime.stop_event.is_set()
    assert runtime.terminal_errors() == {
        "backup-worker": error_type.__name__
    }
    assert runtime.health()["backup-worker"] == {
        "alive": False,
        "error_class": error_type.__name__,
    }
