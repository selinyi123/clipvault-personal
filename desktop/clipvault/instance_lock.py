"""Single-instance guard via Windows named mutex."""

import ctypes
from ctypes import wintypes

_ERROR_ALREADY_EXISTS = 183
INSTANCE_MUTEX_NAME = "Local\\ClipVaultPersonal"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class AlreadyRunningError(Exception):
    pass


class InstanceLock:
    def __init__(self, name: str = INSTANCE_MUTEX_NAME):
        self.name = name
        self._handle = None

    def acquire(self) -> None:
        handle = _kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(f"CreateMutexW failed: {ctypes.get_last_error()}")
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(handle)
            raise AlreadyRunningError(self.name)
        self._handle = handle

    def release(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
