"""Windows per-user Named Pipe publisher for Runtime Snapshot V1.

This module is imported on every platform but only constructs Win32 objects on
Windows.  The pipe is a one-request local channel: client identity is verified
before a byte of ClipVault content is published, and one absolute deadline
covers hello, request, and response I/O.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Protocol

from clipvault.runtime.snapshot import MAX_RESPONSE_BYTES, RuntimeSnapshotPublisher
from clipvault.runtime.snapshot_protocol import (
    SnapshotProtocolError,
    decode_client_hello,
    decode_snapshot_request,
    encode_host_hello,
    encode_snapshot_response,
)

log = logging.getLogger("clipvault.runtime.snapshot")

SNAPSHOT_PIPE_PREFIX = r"\\.\pipe\ClipVaultRuntimeSnapshotV1-"
SNAPSHOT_DEADLINE_MS = 250
_FRAME_PREFIX_BYTES = 4
_PIPE_BUFFER_BYTES = MAX_RESPONSE_BYTES + _FRAME_PREFIX_BYTES


class SnapshotPipeUnavailable(RuntimeError):
    pass


class SnapshotPipeKernel(Protocol):
    def pipe_name(self) -> str: ...
    def create_server(self, expected_host_path: Path, require_signature: bool): ...
    def connect(self, handle, stop_event: threading.Event) -> bool: ...
    def verify_client(self, handle, expected_host_path: Path, require_signature: bool) -> bool: ...
    def read_frame(self, handle, deadline: float) -> bytes: ...
    def write_frame(self, handle, payload: bytes, deadline: float) -> None: ...
    def flush_response(self, handle) -> None: ...
    def close_server(self, handle) -> None: ...


class WindowsRuntimeSnapshotServer:
    """Serve bounded snapshots until Runtime shutdown is requested."""

    def __init__(
        self,
        publisher: RuntimeSnapshotPublisher,
        expected_host_path: str | os.PathLike[str],
        *,
        require_signature: bool = True,
        kernel: SnapshotPipeKernel | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        expected = Path(expected_host_path)
        if not expected.is_absolute():
            raise ValueError("expected IME Host path must be absolute")
        self._publisher = publisher
        self._expected_host_path = expected.resolve(strict=False)
        self._require_signature = bool(require_signature)
        self._kernel = kernel or CtypesWindowsSnapshotPipeKernel()
        self._monotonic = monotonic

    @property
    def pipe_name(self) -> str:
        return self._kernel.pipe_name()

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            handle = self._kernel.create_server(
                self._expected_host_path,
                self._require_signature,
            )
            try:
                if not self._kernel.connect(handle, stop_event):
                    continue
                if not self._kernel.verify_client(
                    handle,
                    self._expected_host_path,
                    self._require_signature,
                ):
                    continue
                self._serve_connected(handle)
                # A completed overlapped WriteFile only means that the bytes
                # reached the server-side pipe buffer.  Flush before
                # disconnecting so the client cannot observe a valid length
                # prefix followed by ERROR_PIPE_NOT_CONNECTED.
                self._kernel.flush_response(handle)
            except (OSError, SnapshotProtocolError, ValueError) as exc:
                # Never log a path, UUID, candidate, pipe payload, or exception
                # message. The class is sufficient for local diagnostics.
                log.warning("snapshot client rejected err=%s", exc.__class__.__name__)
            finally:
                self._kernel.close_server(handle)

    def _serve_connected(self, handle) -> None:
        deadline = self._monotonic() + SNAPSHOT_DEADLINE_MS / 1000
        hello = decode_client_hello(self._kernel.read_frame(handle, deadline))
        # Parsing the UUID proves protocol conformance; it is deliberately not
        # treated as authentication (the process token/path/signature are).
        del hello
        self._kernel.write_frame(
            handle,
            encode_host_hello(self._publisher.publisher_epoch),
            deadline,
        )
        request = decode_snapshot_request(self._kernel.read_frame(handle, deadline))
        snapshot = self._publisher.publish(
            request_id=request.request_id,
            limit=request.limit,
        )
        self._kernel.write_frame(handle, encode_snapshot_response(snapshot), deadline)


if os.name == "nt":
    _ULONG_PTR = ctypes.c_size_t
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PIPE_ACCESS_DUPLEX = 0x00000003
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
    _PIPE_UNLIMITED_INSTANCES = 255
    _ERROR_IO_PENDING = 997
    _ERROR_PIPE_CONNECTED = 535
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _SDDL_REVISION_1 = 1
    _WTD_UI_NONE = 2
    _WTD_REVOKE_NONE = 0
    _WTD_CHOICE_FILE = 1
    _WTD_STATEACTION_IGNORE = 0
    _WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00001000

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER_STRUCT(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", _ULONG_PTR),
            ("InternalHigh", _ULONG_PTR),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class _WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.c_void_p),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    _WINTRUST_ACTION_GENERIC_VERIFY_V2 = _GUID(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )


class CtypesWindowsSnapshotPipeKernel:
    """Zero-dependency Win32 Named Pipe implementation."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise SnapshotPipeUnavailable("Windows Named Pipe requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        self._configure_functions()
        self._current_user_sid = self._process_user_sid(self._kernel32.GetCurrentProcess())

    def _configure_functions(self) -> None:
        k = self._kernel32
        a = self._advapi32
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetCurrentProcessId.restype = wintypes.DWORD
        k.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        k.ProcessIdToSessionId.restype = wintypes.BOOL
        k.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_SECURITY_ATTRIBUTES),
        ]
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
        k.ConnectNamedPipe.restype = wintypes.BOOL
        k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        k.DisconnectNamedPipe.restype = wintypes.BOOL
        k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k.FlushFileBuffers.restype = wintypes.BOOL
        k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateEventW.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
        k.CancelIoEx.restype = wintypes.BOOL
        k.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        k.GetOverlappedResult.restype = wintypes.BOOL
        k.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_OVERLAPPED),
        ]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = list(k.ReadFile.argtypes)
        k.WriteFile.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.GetNamedPipeClientProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        k.GetNamedPipeClientProcessId.restype = wintypes.BOOL
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree.restype = ctypes.c_void_p
        a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        a.OpenProcessToken.restype = wintypes.BOOL
        a.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        a.GetTokenInformation.restype = wintypes.BOOL
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        a.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        a.ConvertSidToStringSidW.restype = wintypes.BOOL
        self._wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(_GUID), ctypes.c_void_p]
        self._wintrust.WinVerifyTrust.restype = ctypes.c_long

    def pipe_name(self) -> str:
        session = wintypes.DWORD()
        if not self._kernel32.ProcessIdToSessionId(
            self._kernel32.GetCurrentProcessId(), ctypes.byref(session)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return f"{SNAPSHOT_PIPE_PREFIX}{session.value}"

    def create_server(self, expected_host_path: Path, require_signature: bool):
        del expected_host_path, require_signature
        descriptor = ctypes.c_void_p()
        descriptor_size = wintypes.DWORD()
        sddl = f"D:P(A;;GA;;;SY)(A;;GA;;;{self._current_user_sid})"
        if not self._advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(descriptor_size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        security = _SECURITY_ATTRIBUTES(
            ctypes.sizeof(_SECURITY_ATTRIBUTES), descriptor, False
        )
        try:
            handle = self._kernel32.CreateNamedPipeW(
                self.pipe_name(),
                _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED,
                _PIPE_REJECT_REMOTE_CLIENTS,
                _PIPE_UNLIMITED_INSTANCES,
                _PIPE_BUFFER_BYTES,
                _PIPE_BUFFER_BYTES,
                0,
                ctypes.byref(security),
            )
        finally:
            self._kernel32.LocalFree(descriptor)
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def connect(self, handle, stop_event: threading.Event) -> bool:
        event = self._new_event()
        overlapped = _OVERLAPPED(hEvent=event)
        try:
            connected = self._kernel32.ConnectNamedPipe(handle, ctypes.byref(overlapped))
            if connected:
                return True
            error = ctypes.get_last_error()
            if error == _ERROR_PIPE_CONNECTED:
                return True
            if error != _ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            while not stop_event.is_set():
                wait = self._kernel32.WaitForSingleObject(event, 25)
                if wait == _WAIT_OBJECT_0:
                    transferred = wintypes.DWORD()
                    return bool(
                        self._kernel32.GetOverlappedResult(
                            handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
                        )
                    )
                if wait != _WAIT_TIMEOUT:
                    raise ctypes.WinError(ctypes.get_last_error())
            self._cancel(handle, overlapped)
            return False
        finally:
            self._kernel32.CloseHandle(event)

    def verify_client(self, handle, expected_host_path: Path, require_signature: bool) -> bool:
        client_pid = wintypes.ULONG()
        if not self._kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(client_pid)):
            return False
        process = self._kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, client_pid.value
        )
        if not process:
            return False
        try:
            if self._process_user_sid(process) != self._current_user_sid:
                return False
            image = self._process_image(process)
        finally:
            self._kernel32.CloseHandle(process)
        if os.path.normcase(os.path.realpath(image)) != os.path.normcase(
            os.path.realpath(expected_host_path)
        ):
            return False
        return not require_signature or self._has_trusted_signature(image)

    def read_frame(self, handle, deadline: float) -> bytes:
        prefix = self._read_exact(handle, _FRAME_PREFIX_BYTES, deadline)
        length = int.from_bytes(prefix, "big")
        if not 1 <= length <= MAX_RESPONSE_BYTES:
            raise SnapshotProtocolError("frame bounds")
        return self._read_exact(handle, length, deadline)

    def write_frame(self, handle, payload: bytes, deadline: float) -> None:
        if not 1 <= len(payload) <= MAX_RESPONSE_BYTES:
            raise SnapshotProtocolError("frame bounds")
        self._write_exact(handle, len(payload).to_bytes(4, "big") + payload, deadline)

    def flush_response(self, handle) -> None:
        if not self._kernel32.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close_server(self, handle) -> None:
        if handle not in (None, _INVALID_HANDLE_VALUE):
            self._kernel32.DisconnectNamedPipe(handle)
            self._kernel32.CloseHandle(handle)

    def _read_exact(self, handle, size: int, deadline: float) -> bytes:
        output = bytearray(size)
        view = (ctypes.c_ubyte * size).from_buffer(output)
        self._transfer_exact(handle, view, size, deadline, writing=False)
        return bytes(output)

    def _write_exact(self, handle, value: bytes, deadline: float) -> None:
        source = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        self._transfer_exact(handle, source, len(value), deadline, writing=True)

    def _transfer_exact(self, handle, buffer, size: int, deadline: float, *, writing: bool) -> None:
        offset = 0
        operation = self._kernel32.WriteFile if writing else self._kernel32.ReadFile
        while offset < size:
            remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                raise TimeoutError("snapshot pipe deadline")
            event = self._new_event()
            overlapped = _OVERLAPPED(hEvent=event)
            transferred = wintypes.DWORD()
            try:
                pointer = ctypes.cast(
                    ctypes.byref(buffer, offset), ctypes.c_void_p
                )
                completed = operation(
                    handle,
                    pointer,
                    size - offset,
                    ctypes.byref(transferred),
                    ctypes.byref(overlapped),
                )
                if not completed:
                    error = ctypes.get_last_error()
                    if error != _ERROR_IO_PENDING:
                        raise ctypes.WinError(error)
                    wait = self._kernel32.WaitForSingleObject(event, remaining_ms)
                    if wait != _WAIT_OBJECT_0:
                        self._cancel(handle, overlapped)
                        if wait == _WAIT_TIMEOUT:
                            raise TimeoutError("snapshot pipe deadline")
                        raise ctypes.WinError(ctypes.get_last_error())
                    if not self._kernel32.GetOverlappedResult(
                        handle,
                        ctypes.byref(overlapped),
                        ctypes.byref(transferred),
                        False,
                    ):
                        raise ctypes.WinError(ctypes.get_last_error())
                if transferred.value == 0:
                    raise EOFError("snapshot pipe closed")
                offset += transferred.value
            finally:
                self._kernel32.CloseHandle(event)

    def _cancel(self, handle, overlapped) -> None:
        self._kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
        transferred = wintypes.DWORD()
        self._kernel32.GetOverlappedResult(
            handle,
            ctypes.byref(overlapped),
            ctypes.byref(transferred),
            True,
        )

    def _new_event(self):
        event = self._kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        return event

    def _process_user_sid(self, process) -> str:
        token = wintypes.HANDLE()
        if not self._advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            needed = wintypes.DWORD()
            self._advapi32.GetTokenInformation(
                token, _TOKEN_USER, None, 0, ctypes.byref(needed)
            )
            if needed.value == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(needed.value)
            if not self._advapi32.GetTokenInformation(
                token,
                _TOKEN_USER,
                buffer,
                needed,
                ctypes.byref(needed),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER_STRUCT)).contents.User.Sid
            text = wintypes.LPWSTR()
            if not self._advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return str(text.value)
            finally:
                self._kernel32.LocalFree(text)
        finally:
            self._kernel32.CloseHandle(token)

    def _process_image(self, process) -> str:
        size = wintypes.DWORD(32_768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not self._kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value

    def _has_trusted_signature(self, image: str) -> bool:
        file_info = _WINTRUST_FILE_INFO(
            ctypes.sizeof(_WINTRUST_FILE_INFO), image, None, None
        )
        trust = _WINTRUST_DATA(
            cbStruct=ctypes.sizeof(_WINTRUST_DATA),
            dwUIChoice=_WTD_UI_NONE,
            fdwRevocationChecks=_WTD_REVOKE_NONE,
            dwUnionChoice=_WTD_CHOICE_FILE,
            pFile=ctypes.addressof(file_info),
            dwStateAction=_WTD_STATEACTION_IGNORE,
            dwProvFlags=_WTD_CACHE_ONLY_URL_RETRIEVAL,
        )
        return self._wintrust.WinVerifyTrust(
            None,
            ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
            ctypes.byref(trust),
        ) == 0
