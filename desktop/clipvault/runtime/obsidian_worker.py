"""Dedicated Obsidian filesystem worker.

Foreground capture and sync paths only commit durable SQLite intents and wake
this worker.  The worker owns both its SQLite connection and all Vault IO so a
slow or unavailable filesystem cannot block the clipboard watcher or API
serving thread.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable

from clipvault.config import Config
from clipvault.service import ClipVaultService
from clipvault.store import db

log = logging.getLogger("clipvault.runtime.obsidian")


class _WorkSignal:
    """Generation-based wake signal that cannot lose an in-flight notify."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        # A new worker performs one recovery sweep immediately on startup.
        self._generation = 1

    def notify(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def wait(
        self,
        seen_generation: int,
        stop: threading.Event,
        timeout_s: float,
    ) -> int:
        with self._condition:
            self._condition.wait_for(
                lambda: self._generation != seen_generation or stop.is_set(),
                timeout=max(0.01, float(timeout_s)),
            )
            return self._generation


class ObsidianWorker:
    """Wakeable worker that owns the Obsidian retry connection and file IO."""

    def __init__(
        self,
        config: Config,
        *,
        interval_s: float = 60.0,
        limit: int = 50,
        soft_runtime_budget_ms: int = 500,
        connect_fn: Callable[[str], sqlite3.Connection] = db.connect,
    ) -> None:
        self.config = config
        self.interval_s = max(0.01, float(interval_s))
        self.limit = max(1, min(int(limit), 500))
        # This budget decides whether another item may start.  It cannot cancel
        # a filesystem call already in progress, hence the explicit "soft" name.
        self.soft_runtime_budget_ms = max(1, int(soft_runtime_budget_ms))
        self._connect = connect_fn
        self._signal = _WorkSignal()

    def notify(self) -> None:
        """Wake the worker after a durable queue intent has committed."""

        self._signal.notify()

    def run(self, stop: threading.Event) -> None:
        """Run until ``stop`` is set; never share the connection across threads."""

        conn: sqlite3.Connection | None = None
        service: ClipVaultService | None = None
        seen_generation = 0
        try:
            while not stop.is_set():
                seen_generation = self._signal.wait(
                    seen_generation,
                    stop,
                    self.interval_s if service is not None else min(self.interval_s, 5.0),
                )
                if stop.is_set():
                    break
                if service is None:
                    try:
                        conn = self._connect(self.config.db_path)
                        db.migrate(conn)
                        service = ClipVaultService(conn, self.config)
                    except Exception as exc:
                        if conn is not None:
                            conn.close()
                            conn = None
                        log.error(
                            "obsidian worker database unavailable err=%s",
                            exc.__class__.__name__,
                        )
                        continue
                try:
                    while not stop.is_set():
                        repaired = service.retry_obsidian_sweep(
                            limit=self.limit,
                            max_runtime_ms=self.soft_runtime_budget_ms,
                        )
                        if repaired:
                            log.info("obsidian worker repaired %d clips", repaired)
                        # One wake may represent more than one committed item.
                        # Drain consecutive bounded batches so coalesced signals
                        # do not leave limit+1 work waiting for the 60s fallback.
                        if not service.has_ready_obsidian_work():
                            break
                except sqlite3.Error as exc:
                    # Reopen the thread-owned connection after database errors;
                    # a transient connection failure must not kill the worker.
                    log.error(
                        "obsidian worker database failed err=%s",
                        exc.__class__.__name__,
                    )
                    if conn is not None:
                        conn.close()
                    conn = None
                    service = None
                except Exception as exc:
                    # Never include exception messages: they may contain a Vault
                    # path. The durable queue makes the next wake/interval safe.
                    log.error(
                        "obsidian worker sweep failed err=%s",
                        exc.__class__.__name__,
                    )
        finally:
            if conn is not None:
                conn.close()
