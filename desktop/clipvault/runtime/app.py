"""Desktop process runtime composition and worker lifecycle.

``main.py`` owns CLI, single-instance behavior, signals, and tray presentation.
This module owns background threads and enforces one SQLite connection per
worker. Existing packages remain the concrete adapters; no public package move
is required to make their dependency direction explicit.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from clipvault.api import server as api_server
from clipvault.backup import cancellation as backup_cancellation
from clipvault.backup.github_backup import BackupWorker
from clipvault.config import Config
from clipvault.runtime.obsidian_worker import ObsidianWorker
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.outbox_repo import OutboxRepo
from clipvault.store.peers_repo import PeersRepo
from clipvault.watcher.win_clipboard import PollingWatcher

log = logging.getLogger("clipvault.runtime")

_MAINTENANCE_INTERVAL_S = 60.0
_DEFAULT_JOIN_TIMEOUT_S = 5.0
_DEFAULT_START_TIMEOUT_S = 5.0
_RUNTIME_API_SERVE = api_server.serve


class RuntimeStopRequested(RuntimeError):
    """Normal external stop requested while startup was still in progress."""


@dataclass(frozen=True)
class RuntimeAdapters:
    """Injectable concrete adapters used by the runtime composition root."""

    connect: Callable = db.connect
    migrate: Callable = db.migrate
    api_serve: Callable = _RUNTIME_API_SERVE
    watcher_factory: Callable = PollingWatcher
    obsidian_worker_factory: Callable = ObsidianWorker
    backup_worker_factory: Callable = BackupWorker
    thread_factory: Callable = threading.Thread
    monotonic: Callable[[], float] = time.monotonic


class ClipVaultRuntime:
    """One-shot, idempotently stoppable desktop background runtime."""

    def __init__(
        self,
        config: Config,
        *,
        adapters: RuntimeAdapters | None = None,
        maintenance_interval_s: float = _MAINTENANCE_INTERVAL_S,
        start_timeout_s: float = _DEFAULT_START_TIMEOUT_S,
    ) -> None:
        self.config = config
        self.adapters = adapters or RuntimeAdapters()
        self.maintenance_interval_s = max(0.01, float(maintenance_interval_s))
        self.start_timeout_s = max(0.1, float(start_timeout_s))
        self.stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._terminal_errors: dict[str, str] = {}
        self._degraded_errors: dict[str, str] = {}
        self._api_preflight_complete = threading.Event()
        self._api_ready = threading.Event()
        self._started = False
        self._obsidian_worker = None
        self._backup_worker = None
        self._backup_worker_lock = threading.Lock()

    def _record_terminal_error(self, name: str, exc: BaseException) -> None:
        error_class = exc.__class__.__name__
        with self._health_lock:
            self._terminal_errors[name] = error_class
        # Never include exception messages: DB or adapter errors can contain a
        # private local path.
        log.error("runtime worker stopped name=%s err=%s", name, error_class)

    def _record_unexpected_exit(self, name: str) -> None:
        with self._health_lock:
            self._terminal_errors[name] = "UnexpectedWorkerExit"
        log.error("runtime worker stopped name=%s err=UnexpectedWorkerExit", name)

    def _record_degraded_error(self, name: str, error_class: str | None) -> None:
        with self._health_lock:
            if error_class is None:
                self._degraded_errors.pop(name, None)
            else:
                self._degraded_errors[name] = error_class

    def _guarded_target(self, name: str, target: Callable, *args, **kwargs) -> Callable[[], None]:
        def run() -> None:
            try:
                target(*args, **kwargs)
            except BaseException as exc:
                self._record_terminal_error(name, exc)
                self.request_stop()
            else:
                if not self.stop_event.is_set():
                    self._record_unexpected_exit(name)
                    self.request_stop()

        return run

    def _new_thread(
        self,
        name: str,
        target: Callable,
        *args,
        daemon: bool = True,
        **kwargs,
    ):
        return self.adapters.thread_factory(
            target=self._guarded_target(name, target, *args, **kwargs),
            daemon=daemon,
            name=name,
        )

    def _migrate_once(self) -> None:
        conn = self.adapters.connect(self.config.db_path)
        try:
            self.adapters.migrate(conn)
        finally:
            conn.close()

    def _handle_clipboard_text(self, text: str, source_app: str | None):
        conn = self.adapters.connect(self.config.db_path)
        try:
            return ClipVaultService(
                conn,
                self.config,
                obsidian_notify=self._obsidian_worker.notify,
            ).handle_clipboard_text(text, source_app)
        finally:
            conn.close()

    def _maintenance_loop(self) -> None:
        conn = self.adapters.connect(self.config.db_path)
        try:
            peers = PeersRepo(conn)
            outbox = OutboxRepo(conn)
            while not self.stop_event.wait(self.maintenance_interval_s):
                try:
                    min_acked = peers.min_my_acked()
                    if min_acked:
                        pruned = outbox.prune_acked(min_acked)
                        if pruned:
                            log.info("outbox pruned %d acked events", pruned)
                except Exception as exc:
                    log.error(
                        "maintenance sweep failed err=%s",
                        exc.__class__.__name__,
                    )
        finally:
            conn.close()

    def _backup_loop(self) -> None:
        conn = self.adapters.connect(self.config.db_path)
        worker = None
        try:
            worker = self.adapters.backup_worker_factory(
                conn,
                self.config.backup_repo_path,
            )
            with self._backup_worker_lock:
                self._backup_worker = worker
            if self.stop_event.is_set():
                request_stop = getattr(worker, "request_stop", None)
                if callable(request_stop):
                    request_stop()
            interval_s = max(60, self.config.backup_interval_minutes * 60)
            while not self.stop_event.wait(interval_s):
                try:
                    stats = worker.run_once(monotonic=self.adapters.monotonic())
                    if stats["written"] or stats["dropped"]:
                        log.info(
                            "backup: wrote=%d dropped=%d pushed=%s",
                            stats["written"],
                            stats["dropped"],
                            stats["pushed"],
                        )
                except backup_cancellation.BackupCancelled:
                    if not self.stop_event.is_set():
                        raise
                    break
                except Exception as exc:
                    log.error("backup worker failed err=%s", exc.__class__.__name__)
        finally:
            with self._backup_worker_lock:
                if self._backup_worker is worker:
                    self._backup_worker = None
            conn.close()

    def _build_threads(self) -> list[threading.Thread]:
        self._obsidian_worker = self.adapters.obsidian_worker_factory(
            self.config,
            interval_s=self.maintenance_interval_s,
        )
        watcher = self.adapters.watcher_factory(
            self._handle_clipboard_text,
            interval_ms=self.config.poll_ms,
            on_error=lambda error_class, _failures: self._record_degraded_error(
                "watcher", error_class
            ),
        )
        api_serve = self.adapters.api_serve
        if api_serve is _RUNTIME_API_SERVE:
            api_serve = partial(
                api_serve,
                on_preflight_complete=self._api_preflight_complete.set,
            )
        else:
            # Injected adapters retain their historical signature and own any
            # private preparation. Their readiness wait starts immediately.
            self._api_preflight_complete.set()
        threads = [
            # API readiness is the startup gate. It binds before any watcher can
            # observe or persist clipboard state.
            self._new_thread(
                "api",
                api_serve,
                self.config,
                self.stop_event,
                obsidian_notify=self._obsidian_worker.notify,
                on_ready=self._api_ready.set,
            ),
            self._new_thread(
                "obsidian-worker",
                self._obsidian_worker.run,
                self.stop_event,
            ),
            self._new_thread("maintenance", self._maintenance_loop),
        ]
        if self.config.backup_enabled and self.config.backup_repo_path:
            # A persistent Git ref/index critical section must outlive main's
            # first bounded join rather than be killed by interpreter teardown.
            threads.append(
                self._new_thread(
                    "backup-worker",
                    self._backup_loop,
                    daemon=False,
                )
            )
        # Watcher is deliberately last: partial startup cannot consume a
        # clipboard sequence before every required runtime thread exists.
        threads.append(self._new_thread("watcher", watcher.run, self.stop_event))
        return threads

    def _wait_until_ready(self) -> None:
        deadline = self.adapters.monotonic() + self.start_timeout_s
        while not self._api_ready.is_set():
            if self.stop_event.wait(0.01):
                with self._health_lock:
                    failed = sorted(self._terminal_errors)
                if failed:
                    raise RuntimeError(
                        f"runtime worker failed during startup: {','.join(failed)}"
                    )
                raise RuntimeStopRequested("runtime stopped during startup")
            if self.adapters.monotonic() >= deadline:
                raise RuntimeError("runtime API readiness timeout")
        with self._health_lock:
            failed = sorted(self._terminal_errors)
        if failed or self.stop_event.is_set():
            if failed:
                raise RuntimeError(
                    f"runtime worker failed during startup: {','.join(failed)}"
                )
            raise RuntimeStopRequested("runtime stopped during startup")

    def _wait_until_api_preflight(self) -> None:
        """Wait without a bind timeout while the API connection repairs FTS."""

        while not self._api_preflight_complete.is_set():
            if self.stop_event.wait(0.01):
                with self._health_lock:
                    failed = sorted(self._terminal_errors)
                if failed:
                    raise RuntimeError(
                        f"runtime worker failed during startup: {','.join(failed)}"
                    )
                raise RuntimeStopRequested("runtime stopped during startup")
        self._raise_if_terminal()

    def _raise_if_terminal(self) -> None:
        with self._health_lock:
            failed = sorted(self._terminal_errors)
        if failed or self.stop_event.is_set():
            if failed:
                raise RuntimeError(
                    f"runtime worker failed during startup: {','.join(failed)}"
                )
            raise RuntimeStopRequested("runtime stopped during startup")

    def start(self) -> None:
        """Start all workers once; clean up every partial start on failure."""

        with self._lifecycle_lock:
            if self._started:
                if self.stop_event.is_set():
                    raise RuntimeError("runtime cannot restart after stop")
                return
            if self.stop_event.is_set():
                raise RuntimeStopRequested("runtime cannot start after stop was requested")
            self._migrate_once()
            if self.stop_event.is_set():
                raise RuntimeStopRequested("runtime stopped during migration")
            candidates = self._build_threads()
            self._started = True
            try:
                api_thread, *remaining = candidates
                api_thread.start()
                self._threads.append(api_thread)
                self._wait_until_api_preflight()
                self._wait_until_ready()
                for thread in remaining:
                    thread.start()
                    self._threads.append(thread)
                    self._raise_if_terminal()
            except BaseException:
                self.request_stop()
                alive = self._join_started(_DEFAULT_JOIN_TIMEOUT_S)
                if "backup-worker" in alive:
                    # Preserve the original startup failure, but do not return
                    # control while a non-daemon persistent Git writer survives.
                    self.drain_backup_before_exit()
                raise

        if self.config.backup_enabled and self.config.backup_repo_path:
            log.info(
                "backup worker enabled interval=%dmin",
                self.config.backup_interval_minutes,
            )

    def request_stop(self) -> None:
        """Idempotently request shutdown and wake the sleeping Vault worker."""

        self.stop_event.set()
        with self._backup_worker_lock:
            backup_worker = self._backup_worker
        if backup_worker is not None:
            try:
                request_stop = getattr(backup_worker, "request_stop", None)
                if callable(request_stop):
                    request_stop()
            except Exception as exc:
                log.error(
                    "runtime backup stop failed err=%s",
                    exc.__class__.__name__,
                )
        worker = self._obsidian_worker
        if worker is not None:
            try:
                worker.notify()
            except Exception as exc:
                log.error("runtime stop wake failed err=%s", exc.__class__.__name__)

    def wait(self, timeout: float | None = None) -> bool:
        return self.stop_event.wait(timeout)

    def _join_started(self, timeout: float) -> list[str]:
        deadline = self.adapters.monotonic() + max(0.0, float(timeout))
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - self.adapters.monotonic()))
        return [thread.name for thread in self._threads if thread.is_alive()]

    def join(self, timeout: float = _DEFAULT_JOIN_TIMEOUT_S) -> list[str]:
        """Join already-started workers within one shared deadline."""

        return self._join_started(timeout)

    def close(self, timeout: float = _DEFAULT_JOIN_TIMEOUT_S) -> list[str]:
        """Idempotently request stop and join every thread already started."""

        self.request_stop()
        return self.join(timeout)

    def drain_backup_before_exit(
        self,
        timeout: float | None = None,
    ) -> list[str]:
        """Wait out a persistent Git critical section before Python exits.

        A ref or real-index writer is intentionally not hard-cancelled because
        doing so can strand a Git lock. Its normal command ceiling is 60 seconds
        and process cleanup is independently bounded, so main waits to completion
        instead of allowing interpreter teardown to kill or orphan the writer.
        Tests/embedded callers may supply a bounded timeout. Other unhealthy
        workers do not consume this dedicated drain.
        """

        self.request_stop()
        deadline = (
            None
            if timeout is None
            else self.adapters.monotonic() + max(0.0, float(timeout))
        )
        for thread in self._threads:
            if thread.name == "backup-worker" and thread.is_alive():
                if deadline is None:
                    thread.join()
                else:
                    thread.join(
                        timeout=max(0.0, deadline - self.adapters.monotonic())
                    )
        return [thread.name for thread in self._threads if thread.is_alive()]

    def health(self) -> dict[str, dict[str, str | bool | None]]:
        """Return content-free worker lifecycle state for diagnostics/tests."""

        with self._health_lock:
            errors = dict(self._terminal_errors)
            degraded = dict(self._degraded_errors)
        return {
            thread.name: {
                "alive": thread.is_alive(),
                "error_class": errors.get(thread.name) or degraded.get(thread.name),
            }
            for thread in self._threads
        }

    def terminal_errors(self) -> dict[str, str]:
        with self._health_lock:
            return dict(self._terminal_errors)
