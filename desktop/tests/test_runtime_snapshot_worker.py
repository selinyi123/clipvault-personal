from __future__ import annotations

import threading

from clipvault.runtime.snapshot_worker import RuntimeSnapshotWorker


class _Connection:
    def __init__(self):
        self.creator = threading.get_ident()
        self.closed = False

    def close(self):
        self.closed = True


def test_connection_publisher_and_server_are_created_in_worker_thread():
    stop = threading.Event()
    observed = {}

    def connect(path):
        observed["path"] = path
        observed["connection"] = _Connection()
        return observed["connection"]

    def publisher_factory(conn, *, weights):
        assert conn.creator == threading.get_ident()
        observed["weights"] = weights
        return object()

    class Server:
        def run(self, stop_event):
            observed["server_thread"] = threading.get_ident()
            stop_event.set()

    worker = RuntimeSnapshotWorker(
        "runtime.sqlite3",
        connect=connect,
        server_factory=lambda _publisher: Server(),
        weights="bounded-weights",
        publisher_factory=publisher_factory,
    )
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    thread.join(1)

    assert not thread.is_alive()
    assert observed["path"] == "runtime.sqlite3"
    assert observed["weights"] == "bounded-weights"
    assert observed["server_thread"] == observed["connection"].creator
    assert observed["connection"].closed


def test_transient_failure_is_degraded_and_retried_without_global_stop():
    stop = threading.Event()
    errors = []
    connections = []
    attempts = 0

    def connect(_path):
        conn = _Connection()
        connections.append(conn)
        return conn

    def server_factory(_publisher):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("private local path must not reach health state")

        class Server:
            def run(self, stop_event):
                stop_event.set()

        return Server()

    worker = RuntimeSnapshotWorker(
        "runtime.sqlite3",
        connect=connect,
        server_factory=server_factory,
        publisher_factory=lambda _conn, *, weights: object(),
        on_error=errors.append,
        retry_delay_s=0.01,
    )
    worker.run(stop)

    assert attempts == 2
    assert errors == ["OSError", None]
    assert all(conn.closed for conn in connections)


def test_unexpected_server_return_retries_as_degraded_failure():
    stop = threading.Event()
    errors = []
    calls = 0

    class Server:
        def run(self, _stop_event):
            nonlocal calls
            calls += 1
            if calls == 2:
                stop.set()

    worker = RuntimeSnapshotWorker(
        "runtime.sqlite3",
        connect=lambda _path: _Connection(),
        server_factory=lambda _publisher: Server(),
        publisher_factory=lambda _conn, *, weights: object(),
        on_error=errors.append,
        retry_delay_s=0.01,
    )
    worker.run(stop)

    assert calls == 2
    assert "RuntimeSnapshotUnexpectedExit" in errors
