"""Fail-closed integrity checks immediately before migration DDL."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from clipvault.store import db
from clipvault.store.migration_lock import DatabaseMigrationLock


_QUICK_CHECK = "PRAGMA main.quick_check(1)"
_FOREIGN_KEY_CHECK = "PRAGMA main.foreign_key_check"
_PRIVATE_MARKER = r"D:\Private\Vault\old.sqlite3"


class _RowsCursor:
    def __init__(
        self,
        *rows,
        fetch_error=None,
        close_action=None,
        close_error=None,
    ):
        self._rows = iter(rows)
        self._fetch_error = fetch_error
        self._close_action = close_action
        self._close_error = close_error
        self.close_called = False

    def fetchone(self):
        if self._fetch_error is not None:
            raise self._fetch_error
        return next(self._rows, None)

    def close(self):
        self.close_called = True
        if self._close_action is not None:
            self._close_action()
        if self._close_error is not None:
            raise self._close_error


def _migration_manifests(tmp_path: Path) -> tuple[Path, Path]:
    v1 = tmp_path / "migrations-v1"
    v2 = tmp_path / "migrations-v2"
    v1.mkdir()
    v2.mkdir()
    first = (
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "CREATE TABLE parents (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE children ("
        "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, "
        "FOREIGN KEY(parent_id) REFERENCES parents(id)"
        ");"
    )
    second = (
        "CREATE TABLE migration_probe (value TEXT NOT NULL);"
        "INSERT INTO migration_probe(value) VALUES ('migrated');"
    )
    for directory in (v1, v2):
        (directory / "0001_initial.sql").write_text(first, encoding="utf-8")
    (v2 / "0002_probe.sql").write_text(second, encoding="utf-8")
    return v1, v2


@pytest.fixture
def old_database(tmp_path):
    v1, v2 = _migration_manifests(tmp_path)
    database = tmp_path / "old.sqlite3"
    conn = db.connect(database)
    assert db.migrate(conn, v1, expected_latest=1) == 1
    conn.execute("INSERT INTO parents(id, value) VALUES (1, 'kept')")
    conn.commit()
    try:
        yield conn, v2
    finally:
        conn.close()


def _assert_old_database_unchanged(conn) -> None:
    assert conn.in_transaction is False
    assert db.schema_version(conn) == 1
    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema "
        "WHERE type='table' AND name='migration_probe'"
    ).fetchone() is None
    assert conn.execute("SELECT value FROM parents WHERE id=1").fetchone()[0] == "kept"


def _assert_safe_integrity_error(error: db.DatabaseIntegrityError, *private) -> None:
    assert str(error) == "database integrity check failed"
    assert error.__cause__ is None
    assert error.__context__ is None
    rendered = repr(error) + str(error)
    assert all(value not in rendered for value in private)


def _migration_error(conn, migrations, *, lock_timeout_s=30.0):
    try:
        db.migrate(
            conn,
            migrations,
            expected_latest=2,
            lock_timeout_s=lock_timeout_s,
        )
    except db.DatabaseIntegrityError as error:
        return error
    raise AssertionError("migration unexpectedly passed integrity checks")


def _assert_traceback_locals_are_content_free(error, marker) -> None:
    traceback = error.__traceback__
    saw_database_frame = False
    while traceback is not None:
        frame = traceback.tb_frame
        if Path(frame.f_code.co_filename).as_posix().endswith(
            "clipvault/store/db.py"
        ):
            saw_database_frame = True
        for name, value in frame.f_locals.items():
            assert marker not in repr(value)
            assert not isinstance(value, sqlite3.Cursor)
            assert name not in {
                "cursor",
                "quick_cursor",
                "quick_row",
                "raw_row",
                "first_row",
                "second_row",
            }
        traceback = traceback.tb_next
    assert saw_database_frame


def test_healthy_old_database_checks_inside_lock_then_upgrades(
    old_database,
    monkeypatch,
):
    conn, migrations = old_database
    events = []
    connection_type = type(conn)
    real_execute = connection_type.execute
    real_schema_version = db.schema_version
    real_acquire = DatabaseMigrationLock.acquire

    def observed_execute(connection, sql, *args, **kwargs):
        if sql == _QUICK_CHECK:
            events.append("quick-check")
        elif sql == _FOREIGN_KEY_CHECK:
            events.append("foreign-key-check")
        return real_execute(connection, sql, *args, **kwargs)

    def observed_schema_version(connection):
        events.append("schema-version")
        return real_schema_version(connection)

    def observed_acquire(lock):
        result = real_acquire(lock)
        events.append("lock-acquired")
        return result

    monkeypatch.setattr(connection_type, "execute", observed_execute)
    monkeypatch.setattr(db, "schema_version", observed_schema_version)
    monkeypatch.setattr(DatabaseMigrationLock, "acquire", observed_acquire)

    assert db.migrate(conn, migrations, expected_latest=2) == 2

    assert events[:5] == [
        "schema-version",
        "lock-acquired",
        "schema-version",
        "quick-check",
        "foreign-key-check",
    ]
    assert conn.execute("SELECT value FROM migration_probe").fetchone()[0] == "migrated"
    assert conn.in_transaction is False


@pytest.mark.parametrize(
    "rows",
    [
        (),
        ((None,),),
        ((b"ok",),),
        ((f"private index detail {_PRIVATE_MARKER}",),),
        (("ok",), (f"unexpected extra row {_PRIVATE_MARKER}",)),
    ],
    ids=["empty", "none", "bytes", "non-ok", "extra-row"],
)
def test_abnormal_quick_check_fails_closes_cursor_and_scrubs_traceback(
    old_database,
    monkeypatch,
    rows,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    quick_cursor = _RowsCursor(*rows)

    def corrupted_quick_check(connection, sql, *args, **kwargs):
        if sql == _QUICK_CHECK:
            return quick_cursor
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", corrupted_quick_check)

    error = _migration_error(conn, migrations)

    assert quick_cursor.close_called is True
    _assert_safe_integrity_error(
        error,
        _PRIVATE_MARKER,
        "private index detail",
        "unexpected extra row",
    )
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    _assert_old_database_unchanged(conn)


def test_quick_check_cursor_close_failure_is_fail_closed_and_content_free(
    old_database,
    monkeypatch,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    quick_cursor = _RowsCursor(
        ("ok",),
        close_error=sqlite3.DatabaseError(f"raw close detail {_PRIVATE_MARKER}"),
    )

    def close_failing_quick_check(connection, sql, *args, **kwargs):
        if sql == _QUICK_CHECK:
            return quick_cursor
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", close_failing_quick_check)

    error = _migration_error(conn, migrations)

    assert quick_cursor.close_called is True
    _assert_safe_integrity_error(error, _PRIVATE_MARKER, "raw close detail")
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    _assert_old_database_unchanged(conn)


def test_cursor_close_invalidates_connection_without_escaping_raw_error(
    old_database,
    monkeypatch,
    tmp_path,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    served = False
    quick_cursor = _RowsCursor(
        ("ok",),
        close_action=conn.close,
        close_error=sqlite3.ProgrammingError(
            f"raw invalidated connection detail {_PRIVATE_MARKER}"
        ),
    )

    def invalidating_quick_check(connection, sql, *args, **kwargs):
        nonlocal served
        if sql == _QUICK_CHECK and not served:
            served = True
            return quick_cursor
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", invalidating_quick_check)

    error = _migration_error(conn, migrations)

    assert quick_cursor.close_called is True
    _assert_safe_integrity_error(
        error,
        _PRIVATE_MARKER,
        "raw invalidated connection detail",
    )
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    replacement = db.connect(tmp_path / "old.sqlite3")
    try:
        _assert_old_database_unchanged(replacement)
        assert db.migrate(replacement, migrations, expected_latest=2) == 2
    finally:
        replacement.close()


def test_foreign_key_orphan_fails_without_migration_side_effects(old_database):
    conn, migrations = old_database
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO children(id, parent_id) VALUES (7, 999)")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    error = _migration_error(conn, migrations)

    _assert_safe_integrity_error(error, "children", "999")
    _assert_old_database_unchanged(conn)
    assert conn.execute("SELECT parent_id FROM children WHERE id=7").fetchone()[0] == 999


@pytest.mark.parametrize("failure_point", ["execute", "fetch"])
def test_foreign_key_check_error_closes_cursor_and_scrubs_traceback(
    old_database,
    monkeypatch,
    failure_point,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    foreign_cursor = _RowsCursor(
        fetch_error=sqlite3.DatabaseError(
            f"raw foreign-key detail {_PRIVATE_MARKER}"
        )
    )

    def failing_foreign_key_check(connection, sql, *args, **kwargs):
        if sql == _FOREIGN_KEY_CHECK:
            if failure_point == "execute":
                raise sqlite3.DatabaseError(
                    f"raw foreign-key detail {_PRIVATE_MARKER}"
                )
            return foreign_cursor
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", failing_foreign_key_check)

    error = _migration_error(conn, migrations)

    assert foreign_cursor.close_called is (failure_point == "fetch")
    _assert_safe_integrity_error(
        error,
        _PRIVATE_MARKER,
        "raw foreign-key detail",
    )
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    _assert_old_database_unchanged(conn)


def test_sqlite_check_error_restores_transaction_and_releases_lock(
    old_database,
    monkeypatch,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    fail_once = True

    def failing_check(connection, sql, *args, **kwargs):
        nonlocal fail_once
        if sql == _QUICK_CHECK and fail_once:
            fail_once = False
            real_execute(connection, "BEGIN")
            raise sqlite3.DatabaseError(
                f"raw sqlite detail {_PRIVATE_MARKER}"
            )
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", failing_check)

    error = _migration_error(conn, migrations, lock_timeout_s=0.1)

    _assert_safe_integrity_error(error, _PRIVATE_MARKER, "raw sqlite detail")
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    _assert_old_database_unchanged(conn)

    # The same connection can immediately reacquire the migration lock and
    # complete the upgrade, proving both transaction and lock cleanup.
    assert db.migrate(conn, migrations, expected_latest=2, lock_timeout_s=0.1) == 2
    assert conn.in_transaction is False


def test_rollback_failure_closes_connection_and_still_releases_lock(
    old_database,
    monkeypatch,
    tmp_path,
):
    conn, migrations = old_database
    connection_type = type(conn)
    real_execute = connection_type.execute
    fail_once = True

    def failing_check(connection, sql, *args, **kwargs):
        nonlocal fail_once
        if sql == _QUICK_CHECK and fail_once:
            fail_once = False
            real_execute(connection, "BEGIN")
            raise sqlite3.DatabaseError(
                f"raw check detail {_PRIVATE_MARKER}"
            )
        return real_execute(connection, sql, *args, **kwargs)

    def failing_rollback(_connection):
        raise sqlite3.DatabaseError(
            f"raw rollback detail {_PRIVATE_MARKER}"
        )

    monkeypatch.setattr(connection_type, "execute", failing_check)
    monkeypatch.setattr(connection_type, "rollback", failing_rollback)

    error = _migration_error(conn, migrations, lock_timeout_s=0.1)

    _assert_safe_integrity_error(
        error,
        _PRIVATE_MARKER,
        "raw check detail",
        "raw rollback detail",
    )
    _assert_traceback_locals_are_content_free(error, _PRIVATE_MARKER)
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    # Closing the poisoned connection releases its SQLite transaction; leaving
    # the migration context releases the process lock. A fresh connection can
    # therefore verify the untouched old schema and immediately upgrade.
    replacement = db.connect(tmp_path / "old.sqlite3")
    try:
        _assert_old_database_unchanged(replacement)
        assert (
            db.migrate(
                replacement,
                migrations,
                expected_latest=2,
                lock_timeout_s=0.1,
            )
            == 2
        )
    finally:
        replacement.close()


def test_current_schema_keeps_no_integrity_check_fast_path(
    old_database,
    monkeypatch,
):
    conn, migrations = old_database
    assert db.migrate(conn, migrations, expected_latest=2) == 2
    connection_type = type(conn)
    real_execute = connection_type.execute

    def reject_integrity_probe(connection, sql, *args, **kwargs):
        if sql in {_QUICK_CHECK, _FOREIGN_KEY_CHECK}:
            raise AssertionError("current schema ran a migration-only integrity check")
        return real_execute(connection, sql, *args, **kwargs)

    monkeypatch.setattr(connection_type, "execute", reject_integrity_probe)

    assert db.migrate(conn, migrations, expected_latest=2) == 2


def test_waiter_rereads_latest_schema_without_integrity_or_duplicate_ddl(
    tmp_path,
    monkeypatch,
):
    migrations, _unused = _migration_manifests(tmp_path)
    database = tmp_path / "concurrent.sqlite3"
    owner_connection = db.connect(database)
    owner = DatabaseMigrationLock.for_connection(owner_connection, timeout_s=1.0)
    assert owner is not None
    waiting = threading.Event()
    results = []
    failures = []
    real_acquire = DatabaseMigrationLock.acquire

    def observed_acquire(lock):
        if threading.current_thread().name == "integrity-contender":
            waiting.set()
        return real_acquire(lock)

    def integrity_must_not_run(_connection):
        raise AssertionError("waiter checked integrity after owner reached latest")

    monkeypatch.setattr(DatabaseMigrationLock, "acquire", observed_acquire)
    monkeypatch.setattr(db, "_assert_pre_migration_integrity", integrity_must_not_run)

    def migrate_as_contender():
        contender = db.connect(database)
        try:
            results.append(
                db.migrate(
                    contender,
                    migrations,
                    expected_latest=1,
                    lock_timeout_s=5.0,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            contender.close()

    try:
        with owner:
            thread = threading.Thread(
                target=migrate_as_contender,
                name="integrity-contender",
            )
            thread.start()
            assert waiting.wait(1.0)
            owner_connection.executescript(
                "BEGIN;"
                "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
                "CREATE TABLE parents (id INTEGER PRIMARY KEY, value TEXT NOT NULL);"
                "CREATE TABLE children ("
                "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, "
                "FOREIGN KEY(parent_id) REFERENCES parents(id)"
                ");"
                "INSERT INTO schema_meta(version) VALUES (1);"
                "COMMIT;"
            )

        thread.join(5.0)
        assert not thread.is_alive()
        assert failures == []
        assert results == [1]
        assert db.schema_version(owner_connection) == 1
    finally:
        owner_connection.close()
