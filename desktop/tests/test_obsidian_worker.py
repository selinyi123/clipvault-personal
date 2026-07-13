"""Dedicated Obsidian worker lifecycle and thread-boundary regressions."""

import sqlite3
import threading
import time

from clipvault.config import Config
from clipvault.obsidian import writer
from clipvault.pipeline import ingest as pipeline
from clipvault.runtime.obsidian_worker import ObsidianWorker
from clipvault.service import ClipVaultService
from clipvault.store import db
from clipvault.store.clips_repo import ClipsRepo


def _cfg(tmp_path) -> Config:
    return Config(
        device_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        device_name="worker-test",
        db_path=str(tmp_path / "worker.sqlite3"),
        max_clip_bytes=1_048_576,
        poll_ms=500,
        vault_path=str(tmp_path / "vault"),
    )


def test_worker_is_the_only_thread_that_performs_vault_io(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    setup_conn = db.connect(cfg.db_path)
    db.migrate(setup_conn)
    setup_conn.close()

    called = threading.Event()
    writer_threads: list[str] = []
    original = writer.write_clip

    def observed_write(*args, **kwargs):
        writer_threads.append(threading.current_thread().name)
        result = original(*args, **kwargs)
        called.set()
        return result

    monkeypatch.setattr(writer, "write_clip", observed_write)
    stop = threading.Event()
    worker = ObsidianWorker(cfg, interval_s=60)
    thread = threading.Thread(
        target=worker.run,
        args=(stop,),
        name="test-obsidian-worker",
        daemon=True,
    )
    thread.start()

    capture_conn = db.connect(cfg.db_path)
    try:
        service = ClipVaultService(
            capture_conn,
            cfg,
            obsidian_notify=worker.notify,
        )
        outcome = service.handle_clipboard_text("write off the foreground thread")

        assert outcome.status == "new"
        assert called.wait(5), "worker did not process the durable queue wake"
        deadline = time.monotonic() + 5
        persisted = ClipsRepo(capture_conn).get(outcome.clip.id)
        while persisted.obsidian_path is None and time.monotonic() < deadline:
            time.sleep(0.01)
            persisted = ClipsRepo(capture_conn).get(outcome.clip.id)
        assert writer_threads == ["test-obsidian-worker"]
        assert persisted.obsidian_path is not None
    finally:
        capture_conn.close()
        stop.set()
        worker.notify()
        thread.join(5)
    assert not thread.is_alive()


def test_worker_stop_wake_closes_owned_connection(tmp_path):
    cfg = _cfg(tmp_path)
    opened = threading.Event()
    closed = threading.Event()

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.set()
            super().close()

    def connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, factory=TrackingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        opened.set()
        return conn

    stop = threading.Event()
    worker = ObsidianWorker(cfg, interval_s=60, connect_fn=connect)
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    assert opened.wait(2)
    stop.set()
    worker.notify()
    thread.join(5)

    assert not thread.is_alive()
    assert closed.wait(1)


def test_worker_reopens_connection_after_sqlite_error(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    stop = threading.Event()
    recovered = threading.Event()
    connections = []

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            super().close()

    def connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, factory=TrackingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        connections.append(conn)
        return conn

    attempts = 0

    def retry_sweep(self, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("reopen")
        recovered.set()
        stop.set()
        return 0

    monkeypatch.setattr(
        "clipvault.runtime.obsidian_worker.ObsidianCommands.retry_sweep",
        retry_sweep,
    )
    worker = ObsidianWorker(cfg, interval_s=0.01, connect_fn=connect)
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    try:
        assert recovered.wait(5), "worker did not rebuild commands after sqlite error"
    finally:
        stop.set()
        worker.notify()
        thread.join(5)

    assert not thread.is_alive()
    assert len(connections) >= 2
    assert all(conn.closed for conn in connections)


def test_single_wake_drains_more_than_one_bounded_batch(tmp_path):
    cfg = _cfg(tmp_path)
    setup_conn = db.connect(cfg.db_path)
    db.migrate(setup_conn)
    for index in range(51):
        pipeline.ingest(
            setup_conn,
            f"worker backlog {index}",
            source_device="worker-test",
        )

    stop = threading.Event()
    worker = ObsidianWorker(cfg, interval_s=60, limit=50)
    thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        pending = 51
        while pending and time.monotonic() < deadline:
            pending = setup_conn.execute(
                "SELECT COUNT(*) FROM obsidian_queue"
            ).fetchone()[0]
            if pending:
                time.sleep(0.02)
        assert pending == 0
        assert setup_conn.execute(
            "SELECT COUNT(*) FROM clips WHERE obsidian_path IS NOT NULL"
        ).fetchone()[0] == 51
    finally:
        setup_conn.close()
        stop.set()
        worker.notify()
        thread.join(5)
    assert not thread.is_alive()
