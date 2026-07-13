"""Cross-process single-writer lock for one ClipVault backup repository."""

from __future__ import annotations

import errno
import math
import os
import stat
import threading
import time
from pathlib import Path

if os.name == "nt":  # pragma: no cover - the opposite branch runs on CI hosts
    import msvcrt
else:  # pragma: no cover - the opposite branch runs on CI hosts
    import fcntl


_LOCK_FILENAME = "clipvault-backup.lock"
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class RepoLockTimeout(TimeoutError):
    """The repository is still owned by another writer after the deadline."""


def _inode_signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink)


def _open_private_carrier(path: Path):
    """Open/create one regular single-link carrier without following a link."""

    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    while True:
        try:
            before = path.lstat()
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    path,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                continue
            before = os.fstat(descriptor)
        else:
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("backup repository lock carrier is unsafe")
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError:
                continue

        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _inode_signature(opened) != _inode_signature(before)
            ):
                raise ValueError("backup repository lock carrier changed")
            return os.fdopen(descriptor, "r+b", buffering=0)
        except BaseException:
            os.close(descriptor)
            raise


def _thread_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path))
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[key] = lock
        return lock


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


class RepoWriteLock:
    """Bounded, crash-released writer lock stored inside the repository Git dir.

    The lock file is deliberately persistent. Its contents do not represent
    ownership and it is never deleted as a stale-lock heuristic; ownership is
    held by the operating system and is automatically released when the file
    descriptor or process exits.
    """

    def __init__(
        self,
        repo_path,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
    ):
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be a non-negative number")
        if not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("timeout_s must be non-negative")
        if (
            isinstance(poll_interval_s, bool)
            or not isinstance(poll_interval_s, (int, float))
        ):
            raise TypeError("poll_interval_s must be a positive number")
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")

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
        self.timeout_s = float(timeout_s)
        self.poll_interval_s = float(poll_interval_s)
        self._thread_lock = _thread_lock_for(self.lock_path)
        self._file = None
        self._dir_fd = None
        self._locked = False

    def _try_os_lock(self) -> bool:
        assert self._file is not None
        try:
            if os.name == "nt":
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                assert self._dir_fd is not None
                fcntl.flock(
                    self._dir_fd,
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        return True

    def acquire(self) -> "RepoWriteLock":
        if self._locked or self._file is not None:
            raise RuntimeError("repository lock is already acquired")

        deadline = time.monotonic() + self.timeout_s
        remaining = _remaining(deadline)
        if not self._thread_lock.acquire(timeout=remaining):
            raise RepoLockTimeout("backup repository writer lock timed out")

        try:
            self._file = _open_private_carrier(self.lock_path)
            # msvcrt locks a byte range. Keep one stable byte in the persistent
            # carrier file; its value has no ownership or stale-state meaning.
            if os.name == "nt":
                self._file.seek(0, os.SEEK_END)
                if self._file.tell() == 0:
                    self._file.write(b"\0")
                    self._file.flush()
                    os.fsync(self._file.fileno())
            else:
                directory_flags = (
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                )
                self._dir_fd = os.open(self.git_dir, directory_flags)

            while not self._try_os_lock():
                remaining = _remaining(deadline)
                if remaining <= 0:
                    raise RepoLockTimeout(
                        "backup repository writer lock timed out"
                    )
                time.sleep(min(self.poll_interval_s, remaining))
            carrier = os.fstat(self._file.fileno())
            current_carrier = self.lock_path.lstat()
            if (
                not stat.S_ISREG(carrier.st_mode)
                or carrier.st_nlink != 1
                or _inode_signature(carrier) != _inode_signature(current_carrier)
            ):
                raise ValueError("backup repository lock carrier changed")
            if self._dir_fd is not None:
                directory = os.fstat(self._dir_fd)
                current_directory = self.git_dir.lstat()
                if (
                    not stat.S_ISDIR(directory.st_mode)
                    or _inode_signature(directory)
                    != _inode_signature(current_directory)
                ):
                    raise ValueError("backup repository Git directory changed")
            self._locked = True
            return self
        except BaseException:
            if self._dir_fd is not None:
                directory_fd = self._dir_fd
                self._dir_fd = None
                os.close(directory_fd)
            if self._file is not None:
                self._file.close()
                self._file = None
            self._thread_lock.release()
            raise

    def release(self) -> None:
        if not self._locked or self._file is None:
            return
        try:
            if os.name == "nt":
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                assert self._dir_fd is not None
                fcntl.flock(self._dir_fd, fcntl.LOCK_UN)
        finally:
            self._locked = False
            if self._dir_fd is not None:
                os.close(self._dir_fd)
                self._dir_fd = None
            self._file.close()
            self._file = None
            self._thread_lock.release()

    def __enter__(self) -> "RepoWriteLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
