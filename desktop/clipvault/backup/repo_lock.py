"""Cross-process single-writer lock for one ClipVault backup repository."""

from __future__ import annotations

import stat
import time
from pathlib import Path

from clipvault.backup import cancellation
from clipvault.interprocess_lock import (
    PersistentDirectoryLock,
    PersistentLockCleanupError,
    PersistentLockTimeout,
    _open_private_carrier,
    _validate_duration,
)


_LOCK_FILENAME = "clipvault-backup.lock"


class RepoLockTimeout(TimeoutError):
    """The repository is still owned by another writer after the deadline."""


class RepoWriteLock:
    """Backward-compatible backup lock stored inside the repository Git dir.

    The shared primitive deliberately preserves the published implementation:
    Windows locks the persistent carrier byte and POSIX locks the ``.git``
    directory inode.  This matters while old and new ClipVault processes may
    overlap during an upgrade.
    """

    def __init__(
        self,
        repo_path,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
        cancel_event: cancellation.CancellationEvent | None = None,
    ):
        validated_timeout = _validate_duration(
            timeout_s, name="timeout_s", positive=False
        )
        validated_poll_interval = _validate_duration(
            poll_interval_s, name="poll_interval_s", positive=True
        )
        root = Path(repo_path).resolve(strict=True)
        git_dir = root / ".git"
        try:
            git_info = git_dir.lstat()
        except FileNotFoundError:
            raise ValueError("backup repository Git directory is unavailable") from None
        if (
            not stat.S_ISDIR(git_info.st_mode)
            or git_dir.resolve(strict=True) != git_dir
        ):
            raise ValueError("backup repository Git directory is unavailable")

        self.git_dir = git_dir
        self.lock_path = git_dir / _LOCK_FILENAME
        self.timeout_s = validated_timeout
        self.poll_interval_s = validated_poll_interval
        self.cancel_event = cancel_event
        self._acquired = False

        def checkpoint() -> None:
            cancellation.checkpoint(self.cancel_event)

        def wait(wait_s: float) -> None:
            if self.cancel_event is None:
                time.sleep(wait_s)
            elif self.cancel_event.wait(wait_s):
                raise cancellation.BackupCancelled("backup shutdown requested")

        self._lock = PersistentDirectoryLock(
            self.git_dir,
            _LOCK_FILENAME,
            timeout_s=validated_timeout,
            poll_interval_s=validated_poll_interval,
            checkpoint=checkpoint,
            wait=wait,
            # Keep this private seam so existing failure-injection tests and
            # downstream monkeypatches continue to exercise carrier cleanup.
            carrier_opener=_open_private_carrier,
        )

    @staticmethod
    def _raise_cleanup(exc: PersistentLockCleanupError) -> None:
        cause = exc.__cause__ if exc.__cause__ is not None else exc
        raise cancellation.BackupLockCleanupError(
            "backup repository lock cleanup failed"
        ) from cause

    def acquire(self) -> "RepoWriteLock":
        if self._acquired:
            raise RuntimeError("repository lock is already acquired")
        try:
            self._lock.acquire()
        except PersistentLockTimeout:
            raise RepoLockTimeout("backup repository writer lock timed out") from None
        except PersistentLockCleanupError as exc:
            self._raise_cleanup(exc)
        self._acquired = True
        return self

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self._lock.release()
        except PersistentLockCleanupError as exc:
            self._raise_cleanup(exc)
        finally:
            self._acquired = False

    def __enter__(self) -> "RepoWriteLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
