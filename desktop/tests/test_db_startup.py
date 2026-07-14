"""Fail-closed SQLite startup and migration compatibility gates."""

import sqlite3

import pytest

from clipvault.store import db


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _TrackedConnection:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        busy_timeout: int = 5000,
        journal_mode: str = "memory",
        foreign_keys: int = 1,
        close_error: bool = False,
    ):
        self.fail_at = fail_at
        self.busy_timeout = busy_timeout
        self.journal_mode = journal_mode
        self.foreign_keys = foreign_keys
        self.close_error = close_error
        self.calls: list[str] = []
        self.closed = False
        self.row_factory = None

    def execute(self, sql: str):
        self.calls.append(sql)
        if self.fail_at == len(self.calls):
            raise sqlite3.OperationalError("injected pragma failure")
        rows = {
            "PRAGMA busy_timeout=5000": (self.busy_timeout,),
            "PRAGMA journal_mode=WAL": (self.journal_mode,),
            "PRAGMA foreign_keys=ON": None,
            "PRAGMA foreign_keys": (self.foreign_keys,),
        }
        return _Cursor(rows[sql])

    def close(self) -> None:
        self.closed = True
        if self.close_error:
            raise OSError("injected close failure")


def _write_migration(path, name: str) -> None:
    (path / name).write_text(
        "CREATE TABLE must_not_run (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )


def test_connect_installs_busy_timeout_before_wal(monkeypatch):
    tracked = _TrackedConnection()
    monkeypatch.setattr(db.sqlite3, "connect", lambda _path, **_kwargs: tracked)

    assert db.connect(":memory:") is tracked
    assert tracked.calls == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA journal_mode=WAL",
        "PRAGMA foreign_keys=ON",
        "PRAGMA foreign_keys",
    ]
    assert tracked.row_factory is sqlite3.Row
    assert tracked.closed is False


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
def test_connect_closes_connection_when_a_pragma_fails(monkeypatch, fail_at):
    tracked = _TrackedConnection(fail_at=fail_at)
    monkeypatch.setattr(db.sqlite3, "connect", lambda _path, **_kwargs: tracked)

    with pytest.raises(sqlite3.OperationalError, match="pragma failure"):
        db.connect(":memory:")

    assert tracked.closed is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"busy_timeout": 0},
        {"journal_mode": "delete"},
        {"foreign_keys": 0},
    ],
)
def test_connect_rejects_unapplied_pragma_settings(monkeypatch, overrides):
    tracked = _TrackedConnection(**overrides)
    monkeypatch.setattr(db.sqlite3, "connect", lambda _path, **_kwargs: tracked)

    with pytest.raises(db.DatabaseStartupError):
        db.connect(":memory:")

    assert tracked.closed is True


def test_connect_cleanup_error_does_not_hide_setup_error(monkeypatch):
    tracked = _TrackedConnection(fail_at=1, close_error=True)
    monkeypatch.setattr(db.sqlite3, "connect", lambda _path, **_kwargs: tracked)

    with pytest.raises(sqlite3.OperationalError, match="pragma failure"):
        db.connect(":memory:")

    assert tracked.closed is True


@pytest.mark.parametrize(
    "filenames",
    [
        ("0001_first.sql", "0001_duplicate.sql"),
        ("0001_first.sql", "0003_gap.sql"),
        ("0002_wrong_start.sql",),
        ("0001_first.sql", "invalid.sql"),
        ("0001_truncated_tail.sql",),
        ("0001_first.sql", "0002_upper.SQL"),
    ],
)
def test_invalid_migration_manifest_fails_before_any_sql(tmp_path, filenames):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for filename in filenames:
        _write_migration(migrations, filename)
    conn = sqlite3.connect(":memory:")

    with pytest.raises(db.MigrationManifestError):
        db.migrate(conn, migrations)

    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='must_not_run'"
    ).fetchone() is None
    assert db.schema_version(conn) == 0


def test_empty_migration_manifest_is_rejected(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()

    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(db.MigrationManifestError, match="empty"):
            db.migrate(conn, migrations)


def test_sql_named_directory_is_not_accepted_as_a_migration(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_directory.sql").mkdir()

    with sqlite3.connect(":memory:") as conn:
        with pytest.raises(db.MigrationManifestError, match="not regular"):
            db.migrate(conn, migrations, expected_latest=1)


def test_unreadable_later_migration_fails_before_earlier_sql_runs(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    _write_migration(migrations, "0001_first.sql")
    (migrations / "0002_invalid_utf8.sql").write_bytes(b"\xff\xfe")
    conn = sqlite3.connect(":memory:")

    with pytest.raises(db.MigrationManifestError, match="unreadable"):
        db.migrate(conn, migrations, expected_latest=2)

    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='must_not_run'"
    ).fetchone() is None


def test_unversioned_nonempty_database_is_not_treated_as_fresh():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
    conn.execute("INSERT INTO existing_data(value) VALUES ('keep')")
    conn.commit()

    with pytest.raises(db.SchemaCompatibilityError, match="metadata is missing"):
        db.migrate(conn)

    assert conn.execute("SELECT value FROM existing_data").fetchone()[0] == "keep"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='clips'"
    ).fetchone() is None


def test_internal_schema_residue_is_not_treated_as_a_fresh_database():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE transient (id INTEGER PRIMARY KEY AUTOINCREMENT);"
        "DROP TABLE transient;"
    )
    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='sqlite_sequence'"
    ).fetchone() is not None

    with pytest.raises(db.SchemaCompatibilityError, match="metadata is missing"):
        db.migrate(conn)


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (1);"
        "INSERT INTO schema_meta VALUES (2);",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (1.5);",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (-1);",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (0);",
        "CREATE TABLE schema_meta (version);"
        "INSERT INTO schema_meta VALUES (NULL);",
        "CREATE TABLE schema_meta (version);"
        "INSERT INTO schema_meta VALUES ('not-an-integer');",
        "CREATE TABLE schema_meta (version);"
        "INSERT INTO schema_meta VALUES (X'01');",
        "CREATE TABLE schema_meta (version INTEGER);"
        "INSERT INTO schema_meta VALUES (1);",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL, extra TEXT);"
        "INSERT INTO schema_meta VALUES (1, NULL);",
        "CREATE TABLE schema_meta (version TEXT NOT NULL);"
        "INSERT INTO schema_meta VALUES ('1');",
        "CREATE TABLE schema_meta (version INTEGER NOT NULL DEFAULT 1);"
        "INSERT INTO schema_meta(version) VALUES (1);",
        "CREATE TABLE schema_meta (other INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (1);",
        "CREATE VIEW schema_meta AS SELECT 1 AS version;",
    ],
)
def test_malformed_schema_metadata_is_rejected_without_writes(schema_sql):
    conn = sqlite3.connect(":memory:")
    conn.executescript(schema_sql)
    conn.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    conn.execute("INSERT INTO sentinel(value) VALUES ('keep')")
    conn.commit()

    with pytest.raises(db.SchemaCompatibilityError):
        db.migrate(conn)

    assert conn.execute("SELECT value FROM sentinel").fetchone()[0] == "keep"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='clips'"
    ).fetchone() is None


def test_future_schema_is_rejected_without_modifying_the_database():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO schema_meta VALUES (10);"
        "CREATE TABLE future_data (value TEXT NOT NULL);"
        "INSERT INTO future_data(value) VALUES ('keep');"
    )

    with pytest.raises(db.SchemaCompatibilityError, match="newer"):
        db.migrate(conn)

    assert db.schema_version(conn) == 10
    assert conn.execute("SELECT value FROM future_data").fetchone()[0] == "keep"
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='clips'"
    ).fetchone() is None


def test_temp_schema_objects_are_rejected_before_migration(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_initial.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations / "0002_next.sql").write_text(
        "CREATE TABLE migrated_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE main.schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO main.schema_meta VALUES (1);"
        "CREATE TEMP TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO temp.schema_meta VALUES (99);"
    )

    assert db.schema_version(conn) == 1
    with pytest.raises(db.MigrationConnectionError, match="temporary"):
        db.migrate(conn, migrations, expected_latest=2)

    assert conn.execute("SELECT version FROM main.schema_meta").fetchone()[0] == 1
    assert conn.execute("SELECT version FROM temp.schema_meta").fetchone()[0] == 99
    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='migrated_table'"
    ).fetchone() is None


def test_attached_schema_is_rejected_before_migration(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_initial.sql").write_text(
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);",
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS shadow")

    with pytest.raises(db.MigrationConnectionError, match="attached"):
        db.migrate(conn, migrations, expected_latest=1)

    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='schema_meta'"
    ).fetchone() is None


def test_temp_schema_metadata_does_not_make_empty_main_look_versioned():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TEMP TABLE schema_meta (version INTEGER NOT NULL);"
        "INSERT INTO temp.schema_meta VALUES (99);"
    )

    assert db.schema_version(conn) == 0


def test_schema_query_error_is_not_downgraded_to_version_zero():
    class BrokenConnection:
        def execute(self, _sql):
            raise sqlite3.DatabaseError("injected schema read failure")

    with pytest.raises(sqlite3.DatabaseError, match="schema read failure"):
        db.schema_version(BrokenConnection())


def test_migrate_rejects_caller_transaction_without_committing_it(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_initial.sql").write_text(
        "CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        "CREATE TABLE business_data (value TEXT NOT NULL);",
        encoding="utf-8",
    )
    conn = sqlite3.connect(":memory:")
    assert db.migrate(conn, migrations, expected_latest=1) == 1
    (migrations / "0002_next.sql").write_text(
        "CREATE TABLE must_not_run (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    conn.execute("INSERT INTO business_data(value) VALUES ('pending')")
    assert conn.in_transaction is True

    with pytest.raises(db.MigrationTransactionError):
        db.migrate(conn, migrations, expected_latest=2)

    assert conn.in_transaction is True
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM business_data").fetchone()[0] == 0
    assert db.schema_version(conn) == 1
    assert conn.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE name='must_not_run'"
    ).fetchone() is None
