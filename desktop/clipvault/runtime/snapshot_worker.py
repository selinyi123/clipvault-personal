"""Failure-isolated lifecycle for the Desktop Runtime snapshot publisher."""

from __future__ import annotations

import threading
from collections.abc import Callable

from clipvault.runtime.snapshot import RuntimeSnapshotPublisher


class RuntimeSnapshotUnexpectedExit(RuntimeError):
    """The platform server returned without a coordinated Runtime stop."""


class RuntimeSnapshotWorker:
    """Own the snapshot SQLite connection and restart only this surface.

    The IME candidate surface is optional.  A pipe, signature, or local DB
    failure therefore degrades this worker without stopping clipboard capture,
    sync, the API, or basic Rime input.  Only error class names cross the health
    callback; private paths and candidate content never do.
    """

    def __init__(
        self,
        db_path: str,
        *,
        connect: Callable,
        server_factory: Callable,
        weights=None,
        publisher_factory: Callable = RuntimeSnapshotPublisher,
        on_error: Callable[[str | None], None] | None = None,
        retry_delay_s: float = 1.0,
    ) -> None:
        self._db_path = db_path
        self._connect = connect
        self._server_factory = server_factory
        self._weights = weights
        self._publisher_factory = publisher_factory
        self._on_error = on_error or (lambda _error_class: None)
        self._retry_delay_s = max(0.01, float(retry_delay_s))

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            conn = None
            try:
                # sqlite3 enforces creator-thread ownership by default.  Do
                # not construct this connection in ClipVaultRuntime.start().
                conn = self._connect(self._db_path)
                publisher = self._publisher_factory(
                    conn,
                    weights=self._weights,
                )
                server = self._server_factory(publisher)
                self._on_error(None)
                server.run(stop_event)
                if not stop_event.is_set():
                    raise RuntimeSnapshotUnexpectedExit()
            except Exception as exc:
                self._on_error(exc.__class__.__name__)
                if stop_event.is_set():
                    break
                stop_event.wait(self._retry_delay_s)
            finally:
                if conn is not None:
                    conn.close()
