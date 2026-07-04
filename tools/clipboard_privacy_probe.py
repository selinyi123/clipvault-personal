"""Manual QA helper for Windows registered clipboard privacy formats.

This script intentionally replaces the current Windows clipboard contents with
a non-sensitive probe string plus one optional registered privacy format. It is
for Owner/manual QA only; running it does not by itself satisfy the Issue #36
Windows clipboard privacy gate.

Examples:

    python tools/clipboard_privacy_probe.py normal
    python tools/clipboard_privacy_probe.py exclude-monitor
    python tools/clipboard_privacy_probe.py viewer-ignore
    python tools/clipboard_privacy_probe.py history-off
    python tools/clipboard_privacy_probe.py cloud-off
"""

from __future__ import annotations

import argparse
import ctypes
import platform
import struct
import sys
from ctypes import wintypes

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

EXCLUDE_MONITOR_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"
CAN_INCLUDE_HISTORY_FORMAT = "CanIncludeInClipboardHistory"
CAN_UPLOAD_CLOUD_FORMAT = "CanUploadToCloudClipboard"
CLIPBOARD_VIEWER_IGNORE_FORMAT = "Clipboard Viewer Ignore"

PROBES = {
    "normal": None,
    "exclude-monitor": (EXCLUDE_MONITOR_FORMAT, "presence"),
    "viewer-ignore": (CLIPBOARD_VIEWER_IGNORE_FORMAT, "presence"),
    "history-off": (CAN_INCLUDE_HISTORY_FORMAT, "dword-zero"),
    "cloud-off": (CAN_UPLOAD_CLOUD_FORMAT, "dword-zero"),
}


def _require_windows() -> None:
    if platform.system() != "Windows":
        raise SystemExit("clipboard privacy probe is Windows-only")


def _win_error(message: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), message)


def _global_alloc_bytes(kernel32: ctypes.WinDLL, data: bytes) -> int:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise _win_error("GlobalAlloc failed")

    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        raise _win_error("GlobalLock failed")

    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    return int(handle)


def _set_clipboard_data(user32: ctypes.WinDLL, kernel32: ctypes.WinDLL, fmt: int, data: bytes) -> None:
    handle = _global_alloc_bytes(kernel32, data)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        raise _win_error(f"SetClipboardData failed for format {fmt}")
    # Ownership of the movable memory handle transfers to the system after a
    # successful SetClipboardData call. Do not GlobalFree(handle) here.


def _open_console_clipboard(user32: ctypes.WinDLL, kernel32: ctypes.WinDLL) -> None:
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        raise SystemExit("run this probe from a Windows console so clipboard ownership is explicit")
    if not user32.OpenClipboard(hwnd):
        raise _win_error("OpenClipboard failed")


def write_probe_clipboard(case: str, text: str) -> None:
    _require_windows()
    if case not in PROBES:
        raise ValueError(f"unknown probe case: {case}")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    probe = PROBES[case]
    registered_format = None
    if probe is not None:
        format_name, _payload_kind = probe
        registered_format = user32.RegisterClipboardFormatW(format_name)
        if not registered_format:
            raise _win_error(f"RegisterClipboardFormatW failed for {format_name}")

    _open_console_clipboard(user32, kernel32)
    try:
        if not user32.EmptyClipboard():
            raise _win_error("EmptyClipboard failed")

        _set_clipboard_data(user32, kernel32, CF_UNICODETEXT, text.encode("utf-16le") + b"\x00\x00")

        if probe is None:
            return

        _format_name, payload_kind = probe
        if payload_kind == "presence":
            payload = struct.pack("<I", 1)
        elif payload_kind == "dword-zero":
            payload = struct.pack("<I", 0)
        else:
            raise AssertionError(f"unhandled payload kind: {payload_kind}")
        _set_clipboard_data(user32, kernel32, int(registered_format), payload)
    finally:
        user32.CloseClipboard()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the Windows clipboard with a ClipVault manual-QA probe. "
            "This overwrites the current clipboard content."
        )
    )
    parser.add_argument("case", choices=sorted(PROBES), help="privacy format probe to write")
    parser.add_argument(
        "--text",
        default=None,
        help="optional probe text; defaults to a case-specific non-sensitive marker",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    text = args.text or f"CLIPVAULT_PRIVACY_PROBE_{args.case.upper().replace('-', '_')}"
    write_probe_clipboard(args.case, text)
    expected = "should be captured" if args.case == "normal" else "should be ignored by ClipVault"
    print(f"Wrote {args.case!r} probe to the Windows clipboard; expected result: {expected}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
