"""Cross-process migration ownership and crash-recovery tests."""

import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from clipvault.store import db
from clipvault.interprocess_lock import PersistentLockCleanupError
from clipvault.store.migration_lock import (
    DatabaseMigrationLock,
    DatabaseMigrationLockCleanupError,
    DatabaseMigrationLockTimeout,
    DatabaseMigrationLockUnavailable,
)


def _migrations(tmp_path: Path) -> Path:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_initial.sql").write_text(
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "CREATE TABLE migration_probe (value TEXT NOT NULL);"
        "INSERT INTO migration_probe(value) VALUES ('once');",
        encoding="utf-8",
    )
    return directory


def _lock_for(conn, *, timeout_s: float = 1.0) -> DatabaseMigrationLock:
    lock = DatabaseMigrationLock.for_connection(
        conn,
        timeout_s=timeout_s,
        poll_interval_s=0.01,
    )
    assert lock is not None
    return lock


def test_memory_and_already_current_database_do_not_create_lock_artifacts(tmp_path):
    migrations = _migrations(tmp_path)
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    memory = sqlite3.connect(":memory:")
    assert db.migrate(memory, migrations, expected_latest=1) == 1
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before

    current_path = tmp_path / "already-current.sqlite3"
    current = sqlite3.connect(current_path)
    current.executescript(
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (1);"
        "CREATE TABLE migration_probe (value TEXT NOT NULL);"
        "INSERT INTO migration_probe VALUES ('existing');"
    )
    current.commit()
    assert db.migrate(current, migrations, expected_latest=1) == 1
    assert not list(tmp_path.glob(".clipvault-migration-*.lock"))


def test_invalid_manifest_fails_before_lock_directory_is_created(tmp_path):
    database = tmp_path / "manifest.sqlite3"
    conn = db.connect(database)
    empty_manifest = tmp_path / "empty"
    empty_manifest.mkdir()

    with pytest.raises(db.MigrationManifestError):
        db.migrate(conn, empty_manifest, expected_latest=1)

    assert not list(tmp_path.glob(".clipvault-migration-*.lock"))
    assert db.schema_version(conn) == 0


def test_invalid_timeout_has_no_lock_or_schema_side_effect(tmp_path):
    migrations = _migrations(tmp_path)
    database = tmp_path / "invalid-timeout.sqlite3"
    conn = db.connect(database)

    with pytest.raises(ValueError, match="lock_timeout_s|timeout_s"):
        db.migrate(
            conn,
            migrations,
            expected_latest=1,
            lock_timeout_s=-1,
        )

    assert not list(tmp_path.glob(".clipvault-migration-*.lock"))
    assert db.schema_version(conn) == 0


def test_invalid_timeout_is_rejected_for_memory_and_current_schema(tmp_path):
    migrations = _migrations(tmp_path)
    memory = sqlite3.connect(":memory:")

    with pytest.raises(ValueError, match="lock_timeout_s"):
        db.migrate(
            memory,
            migrations,
            expected_latest=1,
            lock_timeout_s=-1,
        )
    assert db.schema_version(memory) == 0

    assert db.migrate(memory, migrations, expected_latest=1) == 1
    with pytest.raises(ValueError, match="lock_timeout_s"):
        db.migrate(
            memory,
            migrations,
            expected_latest=1,
            lock_timeout_s=-1,
        )
    assert db.schema_version(memory) == 1


def test_raw_file_connection_cannot_run_an_actual_migration(tmp_path):
    migrations = _migrations(tmp_path)
    database = tmp_path / "raw-connection.sqlite3"
    conn = sqlite3.connect(database)

    with pytest.raises(db.MigrationLockError, match="unavailable"):
        db.migrate(conn, migrations, expected_latest=1)

    assert db.schema_version(conn) == 0
    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='migration_probe'"
    ).fetchone() is None


def test_timeout_is_bounded_and_does_not_modify_schema(tmp_path):
    migrations = _migrations(tmp_path)
    database = tmp_path / "timeout.sqlite3"
    owner = db.connect(database)
    contender = db.connect(database)

    started = time.monotonic()
    with _lock_for(owner):
        with pytest.raises(db.MigrationLockTimeout):
            db.migrate(
                contender,
                migrations,
                expected_latest=1,
                lock_timeout_s=0.1,
            )
    elapsed = time.monotonic() - started

    assert 0.08 <= elapsed < 1.0
    assert db.schema_version(contender) == 0
    assert contender.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='migration_probe'"
    ).fetchone() is None


def test_two_processes_migrate_one_empty_database_once(tmp_path):
    migrations = _migrations(tmp_path)
    database = tmp_path / "concurrent.sqlite3"
    # Keep this test about migration ownership rather than racing the first
    # journal-mode initialization of a brand-new SQLite path.
    setup = db.connect(database)
    setup.close()
    start = tmp_path / "start"
    script = (
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "from clipvault.store import db\n"
        f"conn = db.connect({str(database)!r})\n"
        "Path(sys.argv[1]).write_text('ready', encoding='utf-8')\n"
        f"start = Path({str(start)!r})\n"
        "while not start.exists():\n"
        "    time.sleep(0.01)\n"
        f"version = db.migrate(conn, Path({str(migrations)!r}), expected_latest=1)\n"
        "conn.close()\n"
        "raise SystemExit(0 if version == 1 else 17)\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path / f"ready-{index}")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        deadline = time.monotonic() + 5.0
        while not all(
            (tmp_path / f"ready-{index}").exists() for index in range(2)
        ):
            if any(process.poll() is not None for process in processes):
                raise AssertionError("migration contender exited before the barrier")
            if time.monotonic() >= deadline:
                raise AssertionError("migration contenders did not reach the barrier")
            time.sleep(0.01)
        start.write_text("go", encoding="utf-8")
        completed = [process.communicate(timeout=15) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    for process, (_stdout, stderr) in zip(processes, completed, strict=True):
        assert process.returncode == 0, stderr

    verify = db.connect(database)
    try:
        assert db.schema_version(verify) == 1
        assert [
            row[0]
            for row in verify.execute(
                "SELECT value FROM migration_probe"
            ).fetchall()
        ] == ["once"]
    finally:
        verify.close()


def test_waiter_rereads_schema_after_owner_finishes_migration(
    tmp_path, monkeypatch
):
    migrations = _migrations(tmp_path)
    database = tmp_path / "reread.sqlite3"
    owner_connection = db.connect(database)
    owner = _lock_for(owner_connection)
    waiting = threading.Event()
    results = []
    failures = []
    real_acquire = DatabaseMigrationLock.acquire

    def observed_acquire(lock):
        if threading.current_thread().name == "migration-contender":
            # db.migrate() has already read schema version zero before it
            # reaches DatabaseMigrationLock.acquire().
            waiting.set()
        return real_acquire(lock)

    monkeypatch.setattr(DatabaseMigrationLock, "acquire", observed_acquire)

    def migrate_as_contender():
        contender_connection = db.connect(database)
        try:
            results.append(
                db.migrate(
                    contender_connection,
                    migrations,
                    expected_latest=1,
                    lock_timeout_s=5.0,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            contender_connection.close()

    try:
        with owner:
            thread = threading.Thread(
                target=migrate_as_contender,
                name="migration-contender",
            )
            thread.start()
            assert waiting.wait(1.0)
            owner_connection.executescript(
                "BEGIN;"
                "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
                "CREATE TABLE migration_probe (value TEXT NOT NULL);"
                "INSERT INTO migration_probe(value) VALUES ('once');"
                "INSERT INTO schema_meta(version) VALUES (1);"
                "COMMIT;"
            )

        thread.join(5.0)
        assert not thread.is_alive()
        assert failures == []
        assert results == [1]
        verify = db.connect(database)
        try:
            assert [
                row[0]
                for row in verify.execute(
                    "SELECT value FROM migration_probe"
                ).fetchall()
            ] == ["once"]
        finally:
            verify.close()
    finally:
        owner_connection.close()


def test_process_crash_releases_lock_but_carrier_persists(tmp_path):
    database = tmp_path / "crash.sqlite3"
    setup = db.connect(database)
    setup.close()
    ready = tmp_path / "ready"
    script = (
        "import time\n"
        "from pathlib import Path\n"
        "from clipvault.store import db\n"
        "from clipvault.store.migration_lock import DatabaseMigrationLock\n"
        f"conn = db.connect({str(database)!r})\n"
        "lock = DatabaseMigrationLock.for_connection(conn, timeout_s=1.0)\n"
        "assert lock is not None\n"
        "with lock:\n"
        f"    Path({str(ready)!r}).write_text('ready', encoding='utf-8')\n"
        "    time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not ready.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError("child did not acquire migration lock")
            time.sleep(0.02)
        assert ready.exists()
        process.kill()
        process.wait(timeout=5)

        conn = db.connect(database)
        lock = _lock_for(conn, timeout_s=1.0)
        with lock:
            assert lock.carrier_path.exists()
        assert lock.carrier_path.exists()
        conn.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_canonical_aliases_share_lock_and_other_database_does_not(
    tmp_path, monkeypatch
):
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    first = db.connect(first_path)
    second = db.connect(second_path)
    monkeypatch.chdir(tmp_path)
    alias = db.connect(Path("first.sqlite3"))
    first_lock = _lock_for(first)
    alias_lock = _lock_for(alias, timeout_s=0.1)
    second_lock = _lock_for(second)

    assert first_lock.lock_dir == alias_lock.lock_dir
    assert first_lock.lock_dir != second_lock.lock_dir
    assert first_path.name not in first_lock.lock_dir.name
    with first_lock:
        with second_lock:
            pass
        with pytest.raises(DatabaseMigrationLockTimeout):
            alias_lock.acquire()


def test_waiter_rejects_database_replaced_before_lock_acquisition(tmp_path):
    database = tmp_path / "replace.sqlite3"
    original = db.connect(database)
    owner = _lock_for(original)
    contender = _lock_for(original, timeout_s=5.0)
    original.close()
    started = threading.Event()
    failures = []

    def wait_for_lock():
        started.set()
        try:
            contender.acquire()
        except BaseException as exc:
            failures.append(exc)
        else:  # pragma: no cover - a replacement must never be accepted
            contender.release()

    archived = tmp_path / "replace-original.sqlite3"
    with owner:
        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        assert started.wait(1.0)
        os.replace(database, archived)
        sqlite3.connect(database).close()

    thread.join(2.0)
    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], DatabaseMigrationLockUnavailable)

    replacement = db.connect(database)
    try:
        with _lock_for(replacement, timeout_s=0.1):
            pass
    finally:
        replacement.close()


def test_waiter_rejects_access_symlink_retargeted_to_another_database(tmp_path):
    first_path = tmp_path / "symlink-first.sqlite3"
    second_path = tmp_path / "symlink-second.sqlite3"
    for path in (first_path, second_path):
        setup = db.connect(path)
        setup.close()

    alias = tmp_path / "database-alias.sqlite3"
    try:
        alias.symlink_to(first_path)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")

    original = db.connect(alias)
    owner = _lock_for(original)
    contender = _lock_for(original, timeout_s=5.0)
    direct = db.connect(first_path)
    try:
        assert _lock_for(direct).lock_dir == owner.lock_dir
    finally:
        direct.close()
    original.close()
    started = threading.Event()
    failures = []

    def wait_for_lock():
        started.set()
        try:
            contender.acquire()
        except BaseException as exc:
            failures.append(exc)
        else:  # pragma: no cover - a retargeted alias must never be accepted
            contender.release()

    with owner:
        thread = threading.Thread(target=wait_for_lock)
        thread.start()
        assert started.wait(1.0)
        alias.unlink()
        alias.symlink_to(second_path)

    thread.join(2.0)
    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], DatabaseMigrationLockUnavailable)

    replacement = db.connect(alias)
    try:
        with _lock_for(replacement, timeout_s=0.1):
            pass
    finally:
        replacement.close()


def test_migrate_rejects_symlink_retargeted_after_connection_open(tmp_path):
    migrations = _migrations(tmp_path)
    first_path = tmp_path / "opened-first.sqlite3"
    second_path = tmp_path / "opened-second.sqlite3"
    for path in (first_path, second_path):
        setup = db.connect(path)
        setup.close()

    alias = tmp_path / "opened-alias.sqlite3"
    try:
        alias.symlink_to(first_path)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")

    opened = db.connect(alias)
    alias.unlink()
    alias.symlink_to(second_path)
    try:
        with pytest.raises(db.MigrationLockError, match="unavailable"):
            db.migrate(opened, migrations, expected_latest=1)
        assert db.schema_version(opened) == 0
    finally:
        opened.close()

    for path in (first_path, second_path):
        verify = db.connect(path)
        try:
            assert db.schema_version(verify) == 0
            assert verify.execute(
                "SELECT 1 FROM main.sqlite_schema "
                "WHERE name='migration_probe'"
            ).fetchone() is None
        finally:
            verify.close()


def test_target_validation_cleanup_failure_takes_precedence(tmp_path, monkeypatch):
    database = tmp_path / "cleanup-priority.sqlite3"
    conn = db.connect(database)
    lock = _lock_for(conn)

    class FailedCleanupLock:
        def acquire(self):
            return self

        def release(self):
            try:
                raise OSError("injected cleanup failure")
            except OSError as exc:
                raise PersistentLockCleanupError(
                    "interprocess lock cleanup failed"
                ) from exc

    def fail_validation():
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target changed"
        )

    lock._lock = FailedCleanupLock()
    monkeypatch.setattr(lock, "_validate_target", fail_validation)

    with pytest.raises(DatabaseMigrationLockCleanupError) as error:
        lock.acquire()

    assert isinstance(error.value.__cause__, OSError)
    assert str(error.value.__cause__) == "injected cleanup failure"
    conn.close()


def test_hardlinked_carrier_fails_closed_without_touching_external_file(tmp_path):
    migrations = _migrations(tmp_path)
    database = tmp_path / "unsafe-carrier.sqlite3"
    conn = db.connect(database)
    lock = _lock_for(conn)
    outside = tmp_path / "outside-private.bin"
    outside.write_bytes(b"keep")
    try:
        os.link(outside, lock.carrier_path)
    except OSError as exc:  # pragma: no cover - filesystem capability guard
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")

    with pytest.raises(db.MigrationLockError):
        db.migrate(conn, migrations, expected_latest=1, lock_timeout_s=0.1)

    assert outside.read_bytes() == b"keep"
    assert outside.stat().st_nlink == 2
    assert db.schema_version(conn) == 0
