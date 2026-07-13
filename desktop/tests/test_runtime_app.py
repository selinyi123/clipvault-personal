"""Desktop runtime lifecycle, failure isolation, and connection ownership."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from clipvault.config import Config
from clipvault.runtime import app as runtime_app
from clipvault.runtime.app import ClipVaultRuntime, RuntimeAdapters, RuntimeStopRequested
from clipvault.store import db


def _cfg(tmp_path, *, backup_enabled: bool = False) -> Config:
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="runtime-test",
        db_path=str(tmp_path / "runtime.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
        backup_enabled=backup_enabled,
        backup_repo_path=str(tmp_path / "backup") if backup_enabled else "",
    )


class _FakeObsidianWorker:
    def __init__(self, _config, *, interval_s):
        self.interval_s = interval_s
        self.notify_count = 0
        self.started = threading.Event()

    def notify(self):
        self.notify_count += 1

    def run(self, stop):
        self.started.set()
        stop.wait()


class _FakeWatcher:
    def __init__(self, on_text, *, interval_ms, on_error=None):
        self.on_text = on_text
        self.interval_ms = interval_ms
        self.on_error = on_error
        self.started = threading.Event()

    def run(self, stop):
        self.started.set()
        stop.wait()


def _adapters(*, api_serve=None, thread_factory=threading.Thread):
    def default_api(_config, stop, *, obsidian_notify=None, on_ready=None):
        assert obsidian_notify is not None
        assert on_ready is not None
        on_ready()
        stop.wait()

    return RuntimeAdapters(
        connect=db.connect,
        migrate=db.migrate,
        api_serve=api_serve or default_api,
        watcher_factory=_FakeWatcher,
        obsidian_worker_factory=_FakeObsidianWorker,
        thread_factory=thread_factory,
    )


def test_runtime_start_stop_join_are_idempotent_and_content_free(tmp_path):
    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(),
        maintenance_interval_s=60,
    )

    runtime.start()
    first_names = set(runtime.health())
    runtime.start()

    assert first_names == {"obsidian-worker", "maintenance", "api", "watcher"}
    assert set(runtime.health()) == first_names
    runtime.request_stop()
    runtime.request_stop()
    assert runtime.join(2) == []
    assert runtime.join(0) == []
    assert all(not row["alive"] for row in runtime.health().values())
    assert all(row["error_class"] is None for row in runtime.health().values())


def test_runtime_terminal_worker_error_requests_coordinated_shutdown(tmp_path, caplog):
    marker = r"D:\Private\Vault"
    allow_crash = threading.Event()

    def crash_api(_config, _stop, *, obsidian_notify=None, on_ready=None):
        on_ready()
        allow_crash.wait()
        raise RuntimeError(marker)

    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(api_serve=crash_api),
        maintenance_interval_s=60,
    )
    with caplog.at_level("ERROR", logger="clipvault.runtime"):
        runtime.start()
        allow_crash.set()
        deadline = time.monotonic() + 2
        while runtime.health().get("api", {}).get("error_class") is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)

    health = runtime.health()
    assert health["api"] == {"alive": False, "error_class": "RuntimeError"}
    assert runtime.stop_event.is_set()
    assert marker not in caplog.text
    assert runtime.close(2) == []
    assert all(not row["alive"] for row in runtime.health().values())


def test_runtime_start_fails_cleanly_when_api_never_binds(tmp_path):
    def fail_bind(_config, _stop, *, obsidian_notify=None, on_ready=None):
        raise OSError("private bind detail")

    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(api_serve=fail_bind),
        maintenance_interval_s=60,
        start_timeout_s=1,
    )

    with pytest.raises(RuntimeError, match="api"):
        runtime.start()

    assert runtime.stop_event.is_set()
    assert runtime.terminal_errors() == {"api": "OSError"}
    assert runtime.join(0) == []


def test_runtime_unexpected_worker_return_is_terminal(tmp_path):
    allow_return = threading.Event()

    def returning_api(_config, _stop, *, obsidian_notify=None, on_ready=None):
        on_ready()
        allow_return.wait()

    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(api_serve=returning_api),
        maintenance_interval_s=60,
    )
    runtime.start()
    allow_return.set()
    deadline = time.monotonic() + 2
    while runtime.terminal_errors().get("api") is None:
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert runtime.terminal_errors() == {"api": "UnexpectedWorkerExit"}
    assert runtime.stop_event.is_set()
    assert runtime.close(2) == []


def test_runtime_partial_start_failure_stops_and_joins_started_threads(tmp_path):
    created = []

    class FailingThread:
        name = "maintenance"

        def __init__(self):
            created.append(self)

        def start(self):
            raise RuntimeError("simulated start failure")

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    def thread_factory(*, target, daemon, name):
        if name == "maintenance":
            return FailingThread()
        thread = threading.Thread(target=target, daemon=daemon, name=name)
        created.append(thread)
        return thread

    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(thread_factory=thread_factory),
    )

    with pytest.raises(RuntimeError, match="start failure"):
        runtime.start()

    assert runtime.stop_event.is_set()
    assert [thread.name for thread in runtime._threads] == ["api", "obsidian-worker"]
    assert runtime.join(0) == []
    assert runtime._obsidian_worker.notify_count >= 1


def test_runtime_closes_every_connection_it_opens(tmp_path):
    opened = 0
    closed = 0
    lock = threading.Lock()

    class TrackedConnection:
        def __init__(self, inner: sqlite3.Connection):
            nonlocal opened
            self.inner = inner
            with lock:
                opened += 1

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            nonlocal closed
            self.inner.close()
            with lock:
                closed += 1

    def connect(path):
        return TrackedConnection(db.connect(path))

    base = _adapters()
    adapters = RuntimeAdapters(
        connect=connect,
        migrate=base.migrate,
        api_serve=base.api_serve,
        watcher_factory=base.watcher_factory,
        obsidian_worker_factory=base.obsidian_worker_factory,
        backup_worker_factory=base.backup_worker_factory,
        thread_factory=base.thread_factory,
        monotonic=base.monotonic,
    )
    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=adapters,
        maintenance_interval_s=60,
    )
    runtime.start()
    deadline = time.monotonic() + 2
    while opened < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    runtime.request_stop()
    assert runtime.join(2) == []
    assert opened >= 2  # migration + maintenance; fake adapters own no DB
    assert closed == opened


def test_runtime_refuses_first_start_after_preemptive_stop(tmp_path):
    runtime = ClipVaultRuntime(_cfg(tmp_path), adapters=_adapters())
    runtime.request_stop()

    with pytest.raises(RuntimeStopRequested, match="after stop"):
        runtime.start()


def test_runtime_refuses_restart_after_completed_stop(tmp_path):
    runtime = ClipVaultRuntime(_cfg(tmp_path), adapters=_adapters())
    runtime.start()
    assert runtime.close(2) == []

    with pytest.raises(RuntimeError, match="restart"):
        runtime.start()


def test_runtime_api_readiness_timeout_closes_started_thread(tmp_path):
    def never_ready(_config, stop, *, obsidian_notify=None, on_ready=None):
        stop.wait()

    runtime = ClipVaultRuntime(
        _cfg(tmp_path),
        adapters=_adapters(api_serve=never_ready),
        start_timeout_s=0.1,
    )

    with pytest.raises(RuntimeError, match="readiness timeout"):
        runtime.start()

    assert runtime.stop_event.is_set()
    assert runtime.join(0) == []


def test_runtime_join_uses_one_shared_deadline(tmp_path):
    clock = [0.0]
    timeouts = []

    class WaitingThread:
        def __init__(self, name):
            self.name = name

        def join(self, timeout=None):
            timeouts.append(timeout)
            clock[0] += 0.6

        def is_alive(self):
            return True

    base = _adapters()
    adapters = RuntimeAdapters(
        connect=base.connect,
        migrate=base.migrate,
        api_serve=base.api_serve,
        watcher_factory=base.watcher_factory,
        obsidian_worker_factory=base.obsidian_worker_factory,
        backup_worker_factory=base.backup_worker_factory,
        thread_factory=base.thread_factory,
        monotonic=lambda: clock[0],
    )
    runtime = ClipVaultRuntime(_cfg(tmp_path), adapters=adapters)
    runtime._threads = [WaitingThread("one"), WaitingThread("two"), WaitingThread("three")]

    assert runtime.join(1.0) == ["one", "two", "three"]
    assert timeouts[0] == pytest.approx(1.0)
    assert timeouts[1] == pytest.approx(0.4)
    assert timeouts[2] == 0.0


def test_watcher_callback_closes_short_connection_after_dispatch_failure(
    tmp_path,
    monkeypatch,
):
    closed = threading.Event()

    class TrackedConnection:
        def close(self):
            closed.set()

    class FailedService:
        def __init__(self, conn, config, *, obsidian_notify=None):
            assert isinstance(conn, TrackedConnection)
            assert obsidian_notify is not None

        def handle_clipboard_text(self, text, source_app):
            raise RuntimeError("private clipboard payload")

    base = _adapters()
    adapters = RuntimeAdapters(
        connect=lambda _path: TrackedConnection(),
        migrate=base.migrate,
        api_serve=base.api_serve,
        watcher_factory=base.watcher_factory,
        obsidian_worker_factory=base.obsidian_worker_factory,
        backup_worker_factory=base.backup_worker_factory,
        thread_factory=base.thread_factory,
        monotonic=base.monotonic,
    )
    runtime = ClipVaultRuntime(_cfg(tmp_path), adapters=adapters)
    runtime._obsidian_worker = _FakeObsidianWorker(runtime.config, interval_s=60)
    monkeypatch.setattr(runtime_app, "ClipVaultService", FailedService)

    with pytest.raises(RuntimeError, match="clipboard payload"):
        runtime._handle_clipboard_text("secret", "test.exe")

    assert closed.is_set()


def test_runtime_health_tracks_and_clears_recoverable_watcher_error(tmp_path):
    class AliveWatcherThread:
        name = "watcher"

        def is_alive(self):
            return True

    runtime = ClipVaultRuntime(_cfg(tmp_path), adapters=_adapters())
    runtime._threads = [AliveWatcherThread()]

    runtime._record_degraded_error("watcher", "OperationalError")
    assert runtime.health()["watcher"] == {
        "alive": True,
        "error_class": "OperationalError",
    }
    runtime._record_degraded_error("watcher", None)
    assert runtime.health()["watcher"] == {"alive": True, "error_class": None}
