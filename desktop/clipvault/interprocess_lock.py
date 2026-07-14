"""Small cross-platform primitive for persistent, crash-released locks.

The carrier and directory are deliberately never deleted.  Their contents do
not represent ownership; the operating system lock does.  A persistent inode
avoids the classic stale-file deletion race where two processes lock different
replacement files.
"""

from __future__ import annotations

import errno
import math
import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path

if os.name == "nt":  # pragma: no cover - the opposite branch runs on CI hosts
    import msvcrt
else:  # pragma: no cover - the opposite branch runs on CI hosts
    import fcntl


_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class PersistentLockTimeout(TimeoutError):
    """The lock remained owned after the bounded wait."""


class PersistentLockCleanupError(BaseException):
    """One or more owned lock layers could not be released cleanly."""


def _inode_signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink)


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    # Directory link counts legitimately change when Git creates an object or
    # refs subdirectory. Device+inode proves identity without rejecting that
    # compatible activity.
    return (info.st_dev, info.st_ino)


def _open_private_carrier(path: Path):
    """Open/create one regular single-link carrier without following links."""

    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    while True:
        try:
            before = path.lstat()
        except FileNotFoundError:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            before = os.fstat(descriptor)
        else:
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ValueError("interprocess lock carrier is unsafe")
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
                raise ValueError("interprocess lock carrier changed")
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


def _validate_duration(value, *, name: str, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        qualifier = "positive" if positive else "non-negative"
        raise TypeError(f"{name} must be a {qualifier} number")
    converted = float(value)
    if not math.isfinite(converted) or (converted <= 0 if positive else converted < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return converted


class PersistentDirectoryLock:
    """Lock one persistent directory/carrier pair with a bounded wait.

    Windows locks the carrier's first byte. POSIX locks the directory inode,
    preserving compatibility with ClipVault's published backup lock behavior.
    Callers that need independent locks must therefore use independent lock
    directories.
    """

    def __init__(
        self,
        directory,
        carrier_name: str,
        *,
        timeout_s: float = 5.0,
        poll_interval_s: float = 0.05,
        checkpoint: Callable[[], None] | None = None,
        wait: Callable[[float], None] | None = None,
        carrier_opener: Callable = _open_private_carrier,
    ) -> None:
        if (
            not isinstance(carrier_name, str)
            or not carrier_name
            or carrier_name in {".", ".."}
            or "/" in carrier_name
            or "\\" in carrier_name
            or Path(carrier_name).name != carrier_name
        ):
            raise ValueError("interprocess lock carrier name is invalid")

        requested = Path(os.path.abspath(Path(directory)))
        try:
            before = requested.lstat()
            resolved = requested.resolve(strict=True)
        except OSError:
            raise ValueError("interprocess lock directory is unavailable") from None
        if (
            not stat.S_ISDIR(before.st_mode)
            or os.path.normcase(str(resolved)) != os.path.normcase(str(requested))
        ):
            raise ValueError("interprocess lock directory is unsafe")

        self.directory = requested
        self.lock_path = requested / carrier_name
        self.timeout_s = _validate_duration(
            timeout_s, name="timeout_s", positive=False
        )
        self.poll_interval_s = _validate_duration(
            poll_interval_s, name="poll_interval_s", positive=True
        )
        self._directory_identity = _directory_identity(before)
        self._checkpoint = checkpoint or (lambda: None)
        self._wait = wait or time.sleep
        self._carrier_opener = carrier_opener
        self._thread_lock = _thread_lock_for(self.lock_path)
        self._thread_acquired = False
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
                fcntl.flock(self._dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        return True

    def acquire(self) -> "PersistentDirectoryLock":
        if self._locked or self._file is not None or self._thread_acquired:
            raise RuntimeError("interprocess lock is already acquired")

        deadline = time.monotonic() + self.timeout_s
        while True:
            self._checkpoint()
            remaining = _remaining(deadline)
            if self._thread_lock.acquire(
                timeout=min(self.poll_interval_s, remaining)
            ):
                self._thread_acquired = True
                break
            if remaining <= 0:
                raise PersistentLockTimeout("interprocess lock timed out")

        try:
            self._checkpoint()
            current_directory = self.directory.lstat()
            if (
                not stat.S_ISDIR(current_directory.st_mode)
                or _directory_identity(current_directory)
                != self._directory_identity
            ):
                raise ValueError("interprocess lock directory changed")

            self._file = self._carrier_opener(self.lock_path)
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
                self._dir_fd = os.open(self.directory, directory_flags)

            while not self._try_os_lock():
                self._checkpoint()
                remaining = _remaining(deadline)
                if remaining <= 0:
                    raise PersistentLockTimeout("interprocess lock timed out")
                self._wait(min(self.poll_interval_s, remaining))

            self._checkpoint()
            carrier = os.fstat(self._file.fileno())
            current_carrier = self.lock_path.lstat()
            if (
                not stat.S_ISREG(carrier.st_mode)
                or carrier.st_nlink != 1
                or _inode_signature(carrier) != _inode_signature(current_carrier)
            ):
                raise ValueError("interprocess lock carrier changed")

            current_directory = self.directory.lstat()
            if (
                not stat.S_ISDIR(current_directory.st_mode)
                or _directory_identity(current_directory)
                != self._directory_identity
            ):
                raise ValueError("interprocess lock directory changed")
            if self._dir_fd is not None:
                opened_directory = os.fstat(self._dir_fd)
                if (
                    not stat.S_ISDIR(opened_directory.st_mode)
                    or _directory_identity(opened_directory)
                    != self._directory_identity
                ):
                    raise ValueError("interprocess lock directory changed")

            self._locked = True
            return self
        except BaseException:
            self._cleanup(unlock=False)
            raise

    def _cleanup(self, *, unlock: bool) -> None:
        carrier = self._file
        directory_fd = self._dir_fd
        was_locked = self._locked
        thread_acquired = self._thread_acquired
        self._file = None
        self._dir_fd = None
        self._locked = False
        self._thread_acquired = False
        first_error: BaseException | None = None

        def attempt(action) -> None:
            nonlocal first_error
            try:
                action()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if unlock and was_locked and carrier is not None:
            if os.name == "nt":
                def unlock_windows() -> None:
                    carrier.seek(0)
                    msvcrt.locking(carrier.fileno(), msvcrt.LK_UNLCK, 1)

                attempt(unlock_windows)
            elif directory_fd is not None:
                attempt(lambda: fcntl.flock(directory_fd, fcntl.LOCK_UN))
        if directory_fd is not None:
            attempt(lambda: os.close(directory_fd))
        if carrier is not None:
            attempt(carrier.close)
        if thread_acquired:
            attempt(self._thread_lock.release)
        if first_error is not None:
            raise PersistentLockCleanupError(
                "interprocess lock cleanup failed"
            ) from first_error

    def release(self) -> None:
        if not self._locked:
            return
        self._cleanup(unlock=True)

    def __enter__(self) -> "PersistentDirectoryLock":
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
