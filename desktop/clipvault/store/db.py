"""Connection management and sequential migrations (DB-1)."""

import re
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from clipvault.store.migration_lock import (
    DatabaseMigrationLock,
    DatabaseMigrationLockCleanupError,
    DatabaseMigrationLockTimeout,
    DatabaseMigrationLockUnavailable,
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LATEST_SCHEMA_VERSION = 9
_MIGRATION_NAME = re.compile(
    r"^(?P<number>[0-9]{4})_[A-Za-z0-9][A-Za-z0-9_]*\.sql$"
)
_QUICK_CHECK_SQL = "PRAGMA main.quick_check(1)"
_FOREIGN_KEY_CHECK_SQL = "PRAGMA main.foreign_key_check"


class DatabaseStartupError(RuntimeError):
    """A database cannot be opened safely by this application version."""


class MigrationManifestError(DatabaseStartupError):
    """The packaged migration sequence is incomplete or ambiguous."""


class SchemaCompatibilityError(DatabaseStartupError):
    """Stored schema metadata is malformed or newer than this application."""


class DatabaseIntegrityError(DatabaseStartupError):
    """A pre-migration integrity check could not prove the database safe."""


class MigrationTransactionError(DatabaseStartupError):
    """Migration was requested while caller-owned work was uncommitted."""


class MigrationConnectionError(DatabaseStartupError):
    """Migration was requested on a connection with shadow schemas."""


class MigrationLockError(DatabaseStartupError):
    """Migration ownership could not be established or released safely."""


class MigrationLockTimeout(MigrationLockError):
    """Another process retained migration ownership past the deadline."""


class MigrationLockCleanupError(MigrationLockError):
    """Migration lock cleanup could not be proven complete."""


class _ClipVaultConnection(sqlite3.Connection):
    """SQLite connection retaining the immutable identity used to open main.

    SQLite may canonicalize ``PRAGMA database_list`` on some VFSes. Keeping
    both the original access path and its opening identity lets the migration
    lock detect a symlink or symlinked parent retargeted any time after open.
    """

    _clipvault_main_access_path: Path | None
    _clipvault_main_opened_canonical_path: Path | None
    _clipvault_main_opened_identity: tuple[int, int, int] | None


@dataclass(frozen=True)
class _Migration:
    number: int
    sql: str


def connect(db_path: str | Path) -> sqlite3.Connection:
    p = str(db_path)
    access_path = None if p in {"", ":memory:"} else Path(p).absolute()
    if access_path is not None:
        access_path.parent.mkdir(parents=True, exist_ok=True)
    opened_before = None
    if access_path is not None:
        try:
            opened_before = DatabaseMigrationLock.snapshot_access_path(
                access_path,
                allow_missing=True,
            )
        except DatabaseMigrationLockUnavailable:
            raise DatabaseStartupError(
                "database opening identity is unavailable"
            ) from None
    connect_target = p if access_path is None else str(access_path)
    conn = None
    try:
        conn = sqlite3.connect(connect_target, factory=_ClipVaultConnection)
        if isinstance(conn, _ClipVaultConnection):
            conn._clipvault_main_access_path = access_path
            conn._clipvault_main_opened_canonical_path = None
            conn._clipvault_main_opened_identity = None
            if access_path is not None:
                try:
                    opened_after = DatabaseMigrationLock.snapshot_access_path(
                        access_path,
                    )
                except DatabaseMigrationLockUnavailable:
                    raise DatabaseStartupError(
                        "database opening identity is unavailable"
                    ) from None
                assert opened_after is not None
                if (
                    opened_before is not None
                    and (
                        opened_after.canonical_path
                        != opened_before.canonical_path
                        or opened_after.identity != opened_before.identity
                    )
                ):
                    raise DatabaseStartupError(
                        "database opening identity changed"
                    )
                conn._clipvault_main_opened_canonical_path = (
                    opened_after.canonical_path
                )
                conn._clipvault_main_opened_identity = opened_after.identity
        conn.row_factory = sqlite3.Row
        # Install the wait policy before the first PRAGMA that may need a write
        # lock. Concurrent startup must not fail immediately while another
        # process is establishing WAL mode.
        busy_timeout = conn.execute("PRAGMA busy_timeout=5000").fetchone()
        if busy_timeout is None or busy_timeout[0] != 5000:
            raise DatabaseStartupError("database busy timeout is unavailable")

        journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        expected_mode = "memory" if p == ":memory:" else "wal"
        if (
            journal_mode is None
            or not isinstance(journal_mode[0], str)
            or journal_mode[0].lower() != expected_mode
        ):
            raise DatabaseStartupError("database WAL mode is unavailable")

        conn.execute("PRAGMA foreign_keys=ON")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
        if foreign_keys is None or foreign_keys[0] != 1:
            raise DatabaseStartupError("database foreign keys are unavailable")
    except BaseException:
        if conn is not None:
            try:
                conn.close()
            except BaseException:
                pass
        raise
    assert conn is not None
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    objects = conn.execute(
        "SELECT type FROM main.sqlite_schema WHERE name = 'schema_meta'"
    ).fetchall()
    if not objects:
        # Version zero is represented only by a genuinely empty database.
        # Treating an arbitrary unversioned schema as fresh would let migration
        # DDL collide with, or overwrite assumptions about, existing data.
        existing = conn.execute(
            "SELECT 1 FROM main.sqlite_schema LIMIT 1"
        ).fetchone()
        if existing is not None:
            raise SchemaCompatibilityError("database schema metadata is missing")
        return 0
    if len(objects) != 1 or objects[0][0] != "table":
        raise SchemaCompatibilityError("database schema metadata is invalid")

    try:
        columns = conn.execute(
            "PRAGMA main.table_info('schema_meta')"
        ).fetchall()
        if (
            len(columns) != 1
            or columns[0][1] != "version"
            or str(columns[0][2]).upper() != "INTEGER"
            or columns[0][3] != 1
            or columns[0][4] is not None
            or columns[0][5] != 0
        ):
            raise SchemaCompatibilityError(
                "database schema metadata is invalid"
            )
        rows = conn.execute(
            "SELECT version, typeof(version) FROM main.schema_meta"
        ).fetchall()
    except SchemaCompatibilityError:
        raise
    except sqlite3.DatabaseError as exc:
        raise SchemaCompatibilityError(
            "database schema metadata is invalid"
        ) from exc
    if len(rows) != 1:
        raise SchemaCompatibilityError("database schema metadata is invalid")
    version, storage_type = rows[0]
    if storage_type != "integer" or type(version) is not int or version < 1:
        raise SchemaCompatibilityError("database schema metadata is invalid")
    return version


def _migration_manifest(
    migrations_dir: Path,
    expected_latest: int,
) -> tuple[_Migration, ...]:
    if type(expected_latest) is not int or expected_latest < 1:
        raise MigrationManifestError("expected schema version is invalid")
    try:
        scripts = sorted(
            path
            for path in migrations_dir.iterdir()
            if path.suffix.lower() == ".sql"
        )
    except OSError as exc:
        raise MigrationManifestError("migration manifest is unavailable") from exc
    if not scripts:
        raise MigrationManifestError("migration manifest is empty")

    migrations = []
    for script in scripts:
        if script.is_symlink() or not script.is_file():
            raise MigrationManifestError("migration file is not regular")
        match = _MIGRATION_NAME.fullmatch(script.name)
        if match is None:
            raise MigrationManifestError("migration filename is invalid")
        try:
            sql = script.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise MigrationManifestError(
                "migration file is unreadable"
            ) from exc
        migrations.append(_Migration(int(match.group("number")), sql))

    numbers = [migration.number for migration in migrations]
    if numbers != list(range(1, len(migrations) + 1)):
        raise MigrationManifestError("migration sequence is not contiguous")
    if numbers[-1] != expected_latest:
        raise MigrationManifestError("migration sequence has the wrong tail")
    return tuple(migrations)


def _assert_clean_migration_connection(conn: sqlite3.Connection) -> None:
    """Reject schemas that can shadow unqualified historical migration SQL."""

    try:
        databases = conn.execute("PRAGMA database_list").fetchall()
        if any(row[1] not in {"main", "temp"} for row in databases):
            raise MigrationConnectionError(
                "database migration connection has attached schemas"
            )
        temp_object = conn.execute(
            "SELECT 1 FROM temp.sqlite_schema LIMIT 1"
        ).fetchone()
        if temp_object is not None:
            raise MigrationConnectionError(
                "database migration connection has temporary objects"
            )
    except MigrationConnectionError:
        raise
    except sqlite3.DatabaseError as exc:
        raise MigrationConnectionError(
            "database migration connection is invalid"
        ) from exc


def _safe_in_transaction(conn: sqlite3.Connection) -> bool | None:
    """Read transaction state without retaining a raw connection exception."""

    try:
        return bool(conn.in_transaction)
    except Exception:
        return None


def _close_failed_integrity_connection(conn: sqlite3.Connection) -> None:
    """Best-effort close without replacing the fixed integrity error."""

    try:
        conn.close()
    except Exception:
        pass


def _restore_after_integrity_failure(conn: sqlite3.Connection) -> None:
    """Remove a probe-owned transaction or close the unusable connection."""

    transaction_state = _safe_in_transaction(conn)
    if transaction_state is False:
        return
    if transaction_state is None:
        _close_failed_integrity_connection(conn)
        return
    try:
        conn.rollback()
    except Exception:
        # Do not retain the raw rollback failure. Closing is the only safe way
        # to release SQLite state when rollback itself cannot be trusted.
        _close_failed_integrity_connection(conn)
        return
    transaction_state = _safe_in_transaction(conn)
    if transaction_state is not False:
        # A non-conforming connection that remains in a transaction after a
        # successful rollback, or can no longer report its state, must not
        # escape as reusable migration state.
        _close_failed_integrity_connection(conn)


def _close_integrity_cursor(cursor: sqlite3.Cursor) -> bool:
    """Close a probe cursor without retaining a raw close exception."""

    try:
        cursor.close()
    except Exception:
        return False
    return True


def _integrity_pragma_passes(conn: sqlite3.Connection, sql: str) -> bool:
    """Return only a safe verdict; raw SQLite rows never escape this frame."""

    cursor = None
    first_row = None
    second_row = None
    passed = False
    try:
        cursor = conn.execute(sql)
        first_row = cursor.fetchone()
        if sql == _QUICK_CHECK_SQL:
            second_row = cursor.fetchone()
            passed = (
                first_row is not None
                and len(first_row) == 1
                and first_row[0] == "ok"
                and second_row is None
            )
        elif sql == _FOREIGN_KEY_CHECK_SQL:
            passed = first_row is None
    except Exception:
        # Convert execute/fetch failures into a content-free verdict. Raise only
        # after this helper has returned so no raw exception enters traceback.
        passed = False
    finally:
        if cursor is not None and not _close_integrity_cursor(cursor):
            passed = False
        # Drop diagnostics before the normal return even for debuggers/profilers
        # that retain completed Python frames temporarily.
        cursor = None
        first_row = None
        second_row = None
    return passed


def _assert_pre_migration_integrity(conn: sqlite3.Connection) -> None:
    """Reject corrupt or referentially inconsistent databases before DDL.

    Keep every diagnostic content-free: SQLite integrity rows can contain table,
    index, row, or file details. The caller only needs a stable startup error
    class and message; repair remains an explicit Owner action.
    """

    quick_ok = _integrity_pragma_passes(conn, _QUICK_CHECK_SQL)
    foreign_keys_ok = quick_ok and _integrity_pragma_passes(
        conn,
        _FOREIGN_KEY_CHECK_SQL,
    )
    if not quick_ok or not foreign_keys_ok:
        _restore_after_integrity_failure(conn)
        raise DatabaseIntegrityError("database integrity check failed")


def migrate(
    conn: sqlite3.Connection,
    migrations_dir: Path = MIGRATIONS_DIR,
    *,
    expected_latest: int = LATEST_SCHEMA_VERSION,
    lock_timeout_s: float = 30.0,
) -> int:
    if conn.in_transaction:
        # sqlite3.executescript() implicitly commits a pending transaction.
        # Refuse before manifest/schema work so migration can never make a
        # caller's unrelated business writes durable.
        raise MigrationTransactionError(
            "database migration requires an idle connection"
        )
    _assert_clean_migration_connection(conn)
    manifest = _migration_manifest(migrations_dir, expected_latest)
    validated_lock_timeout = DatabaseMigrationLock.validate_timeout(
        lock_timeout_s
    )
    target = None
    target_error = None
    try:
        # Capture the no-side-effect path/identity snapshot before the first
        # schema read.  If the database is already current, preserve the old
        # behavior and do not require or create a migration lock artifact.
        target = DatabaseMigrationLock.target_for_connection(conn)
    except DatabaseMigrationLockUnavailable as exc:
        target_error = exc
    current = schema_version(conn)
    latest = manifest[-1].number
    if current > latest:
        raise SchemaCompatibilityError(
            "database schema is newer than this application"
        )
    if current == latest:
        return current

    try:
        if target_error is not None:
            raise target_error
        if target is not None and not target.opening_identity_verified:
            # A raw file-backed sqlite3.Connection has no trustworthy record
            # of which physical file was opened.  It remains compatible for
            # already-current schemas, but an actual migration must fail
            # closed.  Production file connections use db.connect().
            raise DatabaseMigrationLockUnavailable(
                "database migration opening identity is unavailable"
            )
        lock = (
            None
            if target is None
            else DatabaseMigrationLock.for_target(
                target,
                timeout_s=validated_lock_timeout,
            )
        )
        guard = lock if lock is not None else nullcontext()
        with guard:
            # A competing process may have completed one or all migrations
            # while this process waited. Re-read only after ownership is held.
            current = schema_version(conn)
            if current > latest:
                raise SchemaCompatibilityError(
                    "database schema is newer than this application"
                )
            if current == latest:
                # A process that waited for another migration owner keeps the
                # same no-check fast path as an initially current connection.
                return current
            _assert_pre_migration_integrity(conn)
            for migration in manifest:
                number = migration.number
                if number <= current:
                    continue
                try:
                    conn.executescript(
                        "BEGIN;\n"
                        f"{migration.sql}\n"
                        "DELETE FROM main.schema_meta;\n"
                        f"INSERT INTO main.schema_meta(version) VALUES ({number});\n"
                        "COMMIT;"
                    )
                except BaseException:
                    try:
                        conn.rollback()
                    except BaseException:
                        pass
                    raise
                current = number
            return current
    except DatabaseMigrationLockTimeout:
        raise MigrationLockTimeout("database migration lock timed out") from None
    except DatabaseMigrationLockUnavailable:
        raise MigrationLockError("database migration lock is unavailable") from None
    except DatabaseMigrationLockCleanupError:
        raise MigrationLockCleanupError(
            "database migration lock cleanup failed"
        ) from None
