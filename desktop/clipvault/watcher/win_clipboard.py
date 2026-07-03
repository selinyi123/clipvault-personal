"""Windows clipboard access via ctypes (zero dependencies).

GetClipboardSequenceNumber is polled cheaply; the clipboard is only opened
when the sequence changes. All win32 calls are injectable so the polling
logic is unit-testable without a real clipboard.
"""

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

_CF_UNICODETEXT = 13
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_EXCLUDE_MONITOR_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
_CAN_INCLUDE_HISTORY_FORMAT = "CanIncludeInClipboardHistory"
_CAN_UPLOAD_CLOUD_FORMAT = "CanUploadToCloudClipboard"
_CLIPBOARD_VIEWER_IGNORE_FORMAT = "Clipboard Viewer Ignore"

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_registered_formats: dict[str, int] = {}


def get_clipboard_seq() -> int:
    return _user32.GetClipboardSequenceNumber()


def _registered_clipboard_format(name: str) -> int:
    fmt = _registered_formats.get(name)
    if fmt is None:
        fmt = int(_user32.RegisterClipboardFormatW(name) or 0)
        _registered_formats[name] = fmt
    return fmt


def _format_available(name: str) -> bool:
    fmt = _registered_clipboard_format(name)
    return bool(fmt and _user32.IsClipboardFormatAvailable(fmt))


def _read_clipboard_dword(name: str) -> int | None:
    fmt = _registered_clipboard_format(name)
    if not fmt:
        return None
    handle = _user32.GetClipboardData(fmt)
    if not handle or _kernel32.GlobalSize(handle) < ctypes.sizeof(wintypes.DWORD):
        return None
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD)).contents.value
    finally:
        _kernel32.GlobalUnlock(handle)


def clipboard_exclusion_reason_from_formats(
    has_format: Callable[[str], bool],
    read_dword: Callable[[str], int | None],
) -> str | None:
    """Return why a producer-marked clipboard item should not be captured.

    Windows clipboard producers can opt out of history/monitoring with
    registered formats. ClipVault has no per-clip "local only, never sync"
    metadata, so cloud-sync opt-out is treated as a capture opt-out too.
    """
    for presence_format in (_EXCLUDE_MONITOR_FORMAT, _CLIPBOARD_VIEWER_IGNORE_FORMAT):
        if has_format(presence_format):
            return presence_format

    if has_format(_CAN_INCLUDE_HISTORY_FORMAT):
        value = read_dword(_CAN_INCLUDE_HISTORY_FORMAT)
        if value is None:
            return f"{_CAN_INCLUDE_HISTORY_FORMAT}=unreadable"
        if value == 0:
            return f"{_CAN_INCLUDE_HISTORY_FORMAT}=0"

    if has_format(_CAN_UPLOAD_CLOUD_FORMAT):
        value = read_dword(_CAN_UPLOAD_CLOUD_FORMAT)
        if value is None:
            return f"{_CAN_UPLOAD_CLOUD_FORMAT}=unreadable"
        if value == 0:
            return f"{_CAN_UPLOAD_CLOUD_FORMAT}=0"

    return None


def _clipboard_exclusion_reason_open() -> str | None:
    return clipboard_exclusion_reason_from_formats(_format_available, _read_clipboard_dword)


def get_clipboard_text(retries: int = 3, retry_delay: float = 0.05) -> str | None:
    """Read CF_UNICODETEXT; None if unavailable, excluded, or busy."""
    for _ in range(retries):
        if not _user32.OpenClipboard(None):
            time.sleep(retry_delay)
            continue
        try:
            if _clipboard_exclusion_reason_open() is not None:
                return None
            handle = _user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()
    return None


def get_foreground_app() -> str | None:
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    process = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        if _kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1]
        return None
    finally:
        _kernel32.CloseHandle(process)


class PollingWatcher:
    """Captures clipboard changes. The first tick only establishes a baseline
    so pre-existing clipboard content is not re-ingested on every restart."""

    def __init__(
        self,
        on_text: Callable[[str, str | None], object],
        *,
        get_seq: Callable[[], int] = get_clipboard_seq,
        get_text: Callable[[], str | None] = get_clipboard_text,
        get_app: Callable[[], str | None] = get_foreground_app,
        interval_ms: int = 500,
    ):
        self._on_text = on_text
        self._get_seq = get_seq
        self._get_text = get_text
        self._get_app = get_app
        self.interval_ms = interval_ms
        self._last_seq: int | None = None

    def tick(self) -> bool:
        """Returns True when a capture was dispatched."""
        seq = self._get_seq()
        if self._last_seq is None:
            self._last_seq = seq
            return False
        if seq == self._last_seq:
            return False
        self._last_seq = seq
        text = self._get_text()
        if not text:
            return False
        self._on_text(text, self._get_app())
        return True

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval_ms / 1000):
            self.tick()
