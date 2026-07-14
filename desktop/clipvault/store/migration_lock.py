"""Per-database lock used to serialize schema migration across processes."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from clipvault.interprocess_lock import (
    PersistentDirectoryLock,
    PersistentLockCleanupError,
    PersistentLockTimeout,
    _validate_duration,
)


_LOCK_DIRECTORY_PREFIX = ".clipvault-migration-"
_LOCK_DIRECTORY_SUFFIX = ".lock"
_LOCK_CARRIER = "owner.lock"

_DatabaseIdentity: TypeAlias = tuple[int, int, int]


@dataclass(frozen=True)
class _DatabaseTarget:
    access_path: Path
    canonical_path: Path
    identity: _DatabaseIdentity


class DatabaseMigrationLockTimeout(TimeoutError):
    """Another process retained the migration lock past the deadline."""


class DatabaseMigrationLockUnavailable(RuntimeError):
    """The lock path or carrier cannot be trusted or opened safely."""


class DatabaseMigrationLockCleanupError(BaseException):
    """Migration lock ownership could not be proven released."""


def _database_identity(info: os.stat_result) -> _DatabaseIdentity:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _validated_database_identity(path: Path) -> _DatabaseIdentity:
    try:
        info = path.lstat()
    except OSError:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is unavailable"
        ) from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is unsafe"
        )
    return _database_identity(info)


def _canonical_main_target(
    conn: sqlite3.Connection,
) -> _DatabaseTarget | None:
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.DatabaseError:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is unavailable"
        ) from None

    main_rows = [row for row in rows if len(row) >= 3 and row[1] == "main"]
    if len(main_rows) != 1 or not isinstance(main_rows[0][2], str):
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is invalid"
        )
    filename = main_rows[0][2]
    if filename == "":
        # SQLite in-memory and anonymous temporary databases cannot be shared
        # by a second process, so a filesystem lock would add only side effects.
        return None
    if "\0" in filename:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is invalid"
        )

    reported_access_path = Path(os.path.abspath(filename))
    configured_access_path = getattr(conn, "_clipvault_main_access_path", None)
    if isinstance(configured_access_path, Path):
        access_path = configured_access_path
    else:
        access_path = Path(os.path.abspath(filename))

    try:
        resolved = access_path.resolve(strict=True)
        reported_resolved = reported_access_path.resolve(strict=True)
    except OSError:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is unavailable"
        ) from None
    identity = _validated_database_identity(resolved)
    if _validated_database_identity(reported_resolved) != identity:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock target is invalid"
        )
    return _DatabaseTarget(
        access_path=access_path,
        canonical_path=resolved,
        identity=identity,
    )


def _lock_directory(
    database_path: Path,
    database_identity: _DatabaseIdentity,
) -> Path:
    # A physical identity key converges ordinary path aliases (including a
    # mapped/UNC alias of the same containing directory) without exposing the
    # database path in the persistent sidecar name.
    canonical_key = ":".join(str(part) for part in database_identity).encode(
        "ascii"
    )
    digest = hashlib.sha256(canonical_key).hexdigest()[:32]
    directory = database_path.parent / (
        f"{_LOCK_DIRECTORY_PREFIX}{digest}{_LOCK_DIRECTORY_SUFFIX}"
    )
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock directory is unavailable"
        ) from None

    try:
        info = directory.lstat()
        resolved = directory.resolve(strict=True)
    except OSError:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock directory is unavailable"
        ) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or os.path.normcase(str(resolved)) != os.path.normcase(str(directory))
    ):
        raise DatabaseMigrationLockUnavailable(
            "database migration lock directory is unsafe"
        )
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
        raise DatabaseMigrationLockUnavailable(
            "database migration lock directory is not private"
        )
    return directory


class DatabaseMigrationLock:
    """Persistent, path-private lock for one canonical SQLite database."""

    def __init__(
        self,
        database_path: Path,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.05,
        _access_path: Path | None = None,
        _expected_identity: _DatabaseIdentity | None = None,
    ) -> None:
        validated_timeout = _validate_duration(
            timeout_s, name="timeout_s", positive=False
        )
        validated_poll_interval = _validate_duration(
            poll_interval_s, name="poll_interval_s", positive=True
        )
        database_path = Path(database_path)
        if _expected_identity is None:
            access_path = (
                Path(os.path.abspath(database_path))
                if _access_path is None
                else _access_path
            )
            try:
                database_path = access_path.resolve(strict=True)
            except OSError:
                raise DatabaseMigrationLockUnavailable(
                    "database migration lock target is unavailable"
                ) from None
            expected_identity = _validated_database_identity(database_path)
        else:
            access_path = database_path if _access_path is None else _access_path
            expected_identity = _expected_identity
        directory = _lock_directory(database_path, expected_identity)
        self.access_path = access_path
        self.database_path = database_path
        self._expected_identity = expected_identity
        self.lock_dir = directory
        self.carrier_path = directory / _LOCK_CARRIER
        try:
            self._lock = PersistentDirectoryLock(
                directory,
                _LOCK_CARRIER,
                timeout_s=validated_timeout,
                poll_interval_s=validated_poll_interval,
            )
        except (OSError, ValueError, TypeError):
            raise DatabaseMigrationLockUnavailable(
                "database migration lock is unavailable"
            ) from None

    @classmethod
    def target_for_connection(
        cls,
        conn: sqlite3.Connection,
    ) -> _DatabaseTarget | None:
        return _canonical_main_target(conn)

    @classmethod
    def for_target(
        cls,
        target: _DatabaseTarget,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.05,
    ) -> "DatabaseMigrationLock":
        return cls(
            target.canonical_path,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            _access_path=target.access_path,
            _expected_identity=target.identity,
        )

    @classmethod
    def for_connection(
        cls,
        conn: sqlite3.Connection,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.05,
    ) -> "DatabaseMigrationLock | None":
        target = cls.target_for_connection(conn)
        if target is None:
            return None
        return cls.for_target(
            target,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def _validate_target(self) -> None:
        try:
            resolved = self.access_path.resolve(strict=True)
        except OSError:
            raise DatabaseMigrationLockUnavailable(
                "database migration lock target is unavailable"
            ) from None
        if (
            resolved != self.database_path
            or _validated_database_identity(resolved)
            != self._expected_identity
        ):
            raise DatabaseMigrationLockUnavailable(
                "database migration lock target changed"
            )

    def _release_after_failed_validation(self) -> None:
        try:
            self._lock.release()
        except PersistentLockCleanupError as exc:
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            raise DatabaseMigrationLockCleanupError(
                "database migration lock cleanup failed"
            ) from cause

    def acquire(self) -> "DatabaseMigrationLock":
        try:
            self._lock.acquire()
        except PersistentLockTimeout:
            raise DatabaseMigrationLockTimeout(
                "database migration lock timed out"
            ) from None
        except PersistentLockCleanupError as exc:
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            raise DatabaseMigrationLockCleanupError(
                "database migration lock cleanup failed"
            ) from cause
        except (OSError, ValueError):
            raise DatabaseMigrationLockUnavailable(
                "database migration lock is unavailable"
            ) from None
        try:
            # The path can be atomically renamed or replaced while this
            # process waits for the sidecar.  Revalidate only after ownership
            # is held, before db.migrate() re-reads or mutates the schema.
            self._validate_target()
        except BaseException:
            self._release_after_failed_validation()
            raise
        return self

    def release(self) -> None:
        try:
            self._lock.release()
        except PersistentLockCleanupError as exc:
            cause = exc.__cause__ if exc.__cause__ is not None else exc
            raise DatabaseMigrationLockCleanupError(
                "database migration lock cleanup failed"
            ) from cause

    def __enter__(self) -> "DatabaseMigrationLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
