"""Bounded Windows Named Pipe adapter for opaque OTP broker offers.

This module mirrors the frozen ``CVOB`` v1 broker wire owned by the native
Windows OTP broker.  It never decrypts, logs, or persists an envelope.  The
pipe server owns local-only ACLs and ``PIPE_REJECT_REMOTE_CLIENTS``; this
client only performs one overlapped request/response exchange within the
caller's absolute deadline.
"""

from __future__ import annotations

import ctypes
import math
import os
import re
import struct
import threading
import time
import uuid
from ctypes import wintypes

from clipvault.otp.ingress import (
    OTP_BROKER_FORWARD_TIMEOUT_S,
    OTP_RELAY_ALGORITHM,
    OTP_RELAY_AUTHENTICATION_TAG_BYTES,
    OTP_RELAY_MAX_CIPHERTEXT_BYTES,
    OTP_RELAY_MIN_CIPHERTEXT_BYTES,
    OTP_RELAY_NONCE_BYTES,
    OTP_RELAY_PROTOCOL_VERSION,
    OtpOpaqueBrokerUnavailable,
    OtpOpaqueEnvelope,
)


_BROKER_MAGIC = b"CVOB"
_BROKER_PROTOCOL_VERSION = 1
_BROKER_OPERATION_OFFER = 1
_BROKER_OPERATION_RESPONSE = 128
_BROKER_STATUS_ACCEPTED = 1
_BROKER_STATUS_CONSUMED = 7
_BROKER_STATUS_MIN = 1
_BROKER_STATUS_MAX = 8
_BROKER_ALGORITHM_A256GCM = 1
_BROKER_MAX_FRAME_BYTES = 512
_BROKER_HEADER = struct.Struct(">4sBBBB")
_U32_BE = struct.Struct(">I")
_U64_BE = struct.Struct(">Q")
_TEST_NAMESPACE_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")


class _PipeTimeout(RuntimeError):
    pass


class _PipeUnavailable(RuntimeError):
    pass


class _PipeProtocol(RuntimeError):
    pass


def _wipe(buffer: bytearray | None) -> None:
    if buffer is not None:
        buffer[:] = b"\x00" * len(buffer)


def _canonical_uuid_bytes(value: str, *, device_identity: bool = False) -> bytes:
    if not isinstance(value, str):
        raise _PipeProtocol("non-canonical UUID")
    raw = value
    if device_identity:
        if not raw.startswith("device:"):
            raise _PipeProtocol("non-canonical device UUID")
        raw = raw[len("device:") :]
    try:
        parsed = uuid.UUID(raw)
    except (AttributeError, TypeError, ValueError):
        raise _PipeProtocol("non-canonical UUID") from None
    if parsed.version != 4 or str(parsed) != raw:
        raise _PipeProtocol("non-canonical UUID")
    return parsed.bytes


def _encode_offer(envelope: OtpOpaqueEnvelope) -> bytearray:
    if not isinstance(envelope, OtpOpaqueEnvelope):
        raise _PipeProtocol("invalid envelope")
    if (
        envelope.version != OTP_RELAY_PROTOCOL_VERSION
        or envelope.algorithm != OTP_RELAY_ALGORITHM
        or isinstance(envelope.sequence, bool)
        or not isinstance(envelope.sequence, int)
        or not 0 < envelope.sequence <= 0xFFFFFFFFFFFFFFFF
        or isinstance(envelope.issued_at_ms, bool)
        or not isinstance(envelope.issued_at_ms, int)
        or not 0 <= envelope.issued_at_ms <= 0xFFFFFFFFFFFFFFFF
        or isinstance(envelope.expires_at_ms, bool)
        or not isinstance(envelope.expires_at_ms, int)
        or not 0 <= envelope.expires_at_ms <= 0xFFFFFFFFFFFFFFFF
        or len(envelope.nonce) != OTP_RELAY_NONCE_BYTES
        or not OTP_RELAY_MIN_CIPHERTEXT_BYTES
        <= len(envelope.ciphertext)
        <= OTP_RELAY_MAX_CIPHERTEXT_BYTES
        or len(envelope.authentication_tag) != OTP_RELAY_AUTHENTICATION_TAG_BYTES
    ):
        raise _PipeProtocol("invalid envelope")

    frame = bytearray(
        _BROKER_HEADER.pack(
            _BROKER_MAGIC,
            _BROKER_PROTOCOL_VERSION,
            _BROKER_OPERATION_OFFER,
            0,
            0,
        )
    )
    try:
        frame.extend(
            (
                envelope.version,
                _BROKER_ALGORITHM_A256GCM,
            )
        )
        frame.extend(_canonical_uuid_bytes(envelope.session_epoch))
        frame.extend(_canonical_uuid_bytes(envelope.event_id))
        frame.extend(
            _canonical_uuid_bytes(
                envelope.sender_device_id,
                device_identity=True,
            )
        )
        frame.extend(
            _canonical_uuid_bytes(
                envelope.target_device_id,
                device_identity=True,
            )
        )
        frame.extend(_U64_BE.pack(envelope.sequence))
        frame.extend(_U64_BE.pack(envelope.issued_at_ms))
        frame.extend(_U64_BE.pack(envelope.expires_at_ms))
        frame.extend(envelope.nonce)
        frame.append(len(envelope.ciphertext))
        frame.extend(envelope.ciphertext)
        frame.extend(envelope.authentication_tag)
        if not 0 < len(frame) <= _BROKER_MAX_FRAME_BYTES:
            raise _PipeProtocol("invalid offer length")
        return frame
    except BaseException:
        _wipe(frame)
        raise


def _decode_response(frame: bytearray) -> int:
    if not isinstance(frame, bytearray) or len(frame) < 26:
        raise _PipeProtocol("invalid response")
    try:
        magic, version, operation, reserved_a, reserved_b = _BROKER_HEADER.unpack_from(
            frame
        )
    except struct.error:
        raise _PipeProtocol("invalid response") from None
    if (
        magic != _BROKER_MAGIC
        or version != _BROKER_PROTOCOL_VERSION
        or operation != _BROKER_OPERATION_RESPONSE
        or reserved_a != 0
        or reserved_b != 0
    ):
        raise _PipeProtocol("invalid response")

    status = frame[8]
    secret_length = frame[25]
    if (
        not _BROKER_STATUS_MIN <= status <= _BROKER_STATUS_MAX
        or secret_length > OTP_RELAY_MAX_CIPHERTEXT_BYTES
        or len(frame) != 26 + secret_length
        or (
            secret_length != 0
            and (
                secret_length < OTP_RELAY_MIN_CIPHERTEXT_BYTES
                or status != _BROKER_STATUS_CONSUMED
            )
        )
    ):
        raise _PipeProtocol("invalid response")
    return status


def _frame_length_prefix(size: int) -> bytearray:
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 < size <= _BROKER_MAX_FRAME_BYTES
    ):
        raise _PipeProtocol("invalid frame length")
    return bytearray(_U32_BE.pack(size))


def _parse_frame_length(prefix: bytearray) -> int:
    if not isinstance(prefix, bytearray) or len(prefix) != _U32_BE.size:
        raise _PipeProtocol("invalid frame prefix")
    size = _U32_BE.unpack(prefix)[0]
    if not 0 < size <= _BROKER_MAX_FRAME_BYTES:
        raise _PipeProtocol("invalid frame length")
    return size


def _validated_test_namespace(value: str | None) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or _TEST_NAMESPACE_RE.fullmatch(value) is None:
        raise ValueError("invalid OTP broker test namespace")
    return value


def _pipe_name(session_id: int, test_namespace: str = "") -> str:
    if (
        isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or not 0 <= session_id <= 0xFFFFFFFF
    ):
        raise _PipeUnavailable("invalid Windows session")
    suffix = (
        f"-{_validated_test_namespace(test_namespace)}"
        if test_namespace
        else ""
    )
    return rf"\\.\pipe\ClipVaultOtpBrokerV1-{session_id}{suffix}"


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class _Win32PipeKernel:
    """Small ctypes boundary; all transfers use FILE_FLAG_OVERLAPPED."""

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_PIPE_BUSY = 231
    _ERROR_IO_PENDING = 997
    _WAIT_OBJECT_0 = 0
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self, *, monotonic=time.monotonic) -> None:
        if os.name != "nt":
            raise _PipeUnavailable("Windows broker unavailable")
        self._monotonic = monotonic
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._CreateFileW = kernel32.CreateFileW
        self._CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._CreateFileW.restype = wintypes.HANDLE
        self._WaitNamedPipeW = kernel32.WaitNamedPipeW
        self._WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        self._WaitNamedPipeW.restype = wintypes.BOOL
        self._CreateEventW = kernel32.CreateEventW
        self._CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._CreateEventW.restype = wintypes.HANDLE
        self._ResetEvent = kernel32.ResetEvent
        self._ResetEvent.argtypes = [wintypes.HANDLE]
        self._ResetEvent.restype = wintypes.BOOL
        self._ReadFile = kernel32.ReadFile
        self._WriteFile = kernel32.WriteFile
        for operation in (self._ReadFile, self._WriteFile):
            operation.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(_Overlapped),
            ]
            operation.restype = wintypes.BOOL
        self._WaitForSingleObject = kernel32.WaitForSingleObject
        self._WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._WaitForSingleObject.restype = wintypes.DWORD
        self._GetOverlappedResult = kernel32.GetOverlappedResult
        self._GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_Overlapped),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self._GetOverlappedResult.restype = wintypes.BOOL
        self._CancelIoEx = kernel32.CancelIoEx
        self._CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self._CancelIoEx.restype = wintypes.BOOL
        self._CloseHandle = kernel32.CloseHandle
        self._CloseHandle.argtypes = [wintypes.HANDLE]
        self._CloseHandle.restype = wintypes.BOOL
        self._GetCurrentProcessId = kernel32.GetCurrentProcessId
        self._GetCurrentProcessId.argtypes = []
        self._GetCurrentProcessId.restype = wintypes.DWORD
        self._ProcessIdToSessionId = kernel32.ProcessIdToSessionId
        self._ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._ProcessIdToSessionId.restype = wintypes.BOOL

    def current_session_id(self) -> int:
        session_id = wintypes.DWORD()
        if not self._ProcessIdToSessionId(
            self._GetCurrentProcessId(),
            ctypes.byref(session_id),
        ):
            raise _PipeUnavailable("Windows session unavailable")
        return int(session_id.value)

    def _remaining_ms(self, deadline_monotonic: float) -> int:
        remaining = deadline_monotonic - self._monotonic()
        if remaining <= 0:
            return 0
        return min(int(remaining * 1000), 0xFFFFFFFF)

    def connect(
        self,
        pipe_name: str,
        deadline_monotonic: float,
        cancel_requested: threading.Event,
    ):
        while not cancel_requested.is_set():
            if self._remaining_ms(deadline_monotonic) == 0:
                raise _PipeTimeout("broker connect timeout")
            handle = self._CreateFileW(
                pipe_name,
                self._GENERIC_READ | self._GENERIC_WRITE,
                0,
                None,
                self._OPEN_EXISTING,
                self._FILE_ATTRIBUTE_NORMAL | self._FILE_FLAG_OVERLAPPED,
                None,
            )
            if handle != self._INVALID_HANDLE_VALUE:
                return handle
            error = ctypes.get_last_error()
            if error not in (self._ERROR_FILE_NOT_FOUND, self._ERROR_PIPE_BUSY):
                raise _PipeUnavailable("broker connect failed")
            remaining_ms = self._remaining_ms(deadline_monotonic)
            if remaining_ms == 0:
                raise _PipeTimeout("broker connect timeout")
            if error == self._ERROR_PIPE_BUSY:
                self._WaitNamedPipeW(pipe_name, min(remaining_ms, 25))
            else:
                cancel_requested.wait(min(remaining_ms, 5) / 1000.0)
        raise _PipeUnavailable("broker port closed")

    def _finish_overlapped(
        self,
        handle,
        overlapped: _Overlapped,
        deadline_monotonic: float,
        transferred: wintypes.DWORD,
    ) -> None:
        wait_ms = self._remaining_ms(deadline_monotonic)
        if wait_ms == 0:
            self._CancelIoEx(handle, ctypes.byref(overlapped))
            self._GetOverlappedResult(
                handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                True,
            )
            raise _PipeTimeout("broker I/O timeout")
        wait_result = self._WaitForSingleObject(overlapped.hEvent, wait_ms)
        if wait_result != self._WAIT_OBJECT_0:
            self._CancelIoEx(handle, ctypes.byref(overlapped))
            self._GetOverlappedResult(
                handle,
                ctypes.byref(overlapped),
                ctypes.byref(transferred),
                True,
            )
            raise _PipeTimeout("broker I/O timeout")
        if not self._GetOverlappedResult(
            handle,
            ctypes.byref(overlapped),
            ctypes.byref(transferred),
            False,
        ):
            raise _PipeUnavailable("broker I/O failed")

    def _transfer_exact(
        self,
        handle,
        data: bytearray,
        deadline_monotonic: float,
        *,
        write: bool,
    ) -> None:
        if not isinstance(data, bytearray) or not data:
            raise _PipeProtocol("invalid transfer buffer")
        event = self._CreateEventW(None, True, False, None)
        if not event:
            raise _PipeUnavailable("broker event unavailable")
        try:
            offset = 0
            operation = self._WriteFile if write else self._ReadFile
            while offset < len(data):
                if self._remaining_ms(deadline_monotonic) == 0:
                    raise _PipeTimeout("broker I/O timeout")
                if not self._ResetEvent(event):
                    raise _PipeUnavailable("broker event reset failed")
                overlapped = _Overlapped()
                overlapped.hEvent = event
                transferred = wintypes.DWORD()
                view = (ctypes.c_ubyte * (len(data) - offset)).from_buffer(
                    data,
                    offset,
                )
                completed = operation(
                    handle,
                    view,
                    len(data) - offset,
                    ctypes.byref(transferred),
                    ctypes.byref(overlapped),
                )
                if not completed:
                    if ctypes.get_last_error() != self._ERROR_IO_PENDING:
                        raise _PipeUnavailable("broker I/O failed")
                    self._finish_overlapped(
                        handle,
                        overlapped,
                        deadline_monotonic,
                        transferred,
                    )
                if transferred.value == 0:
                    raise _PipeUnavailable("broker pipe closed")
                offset += transferred.value
        finally:
            self._CloseHandle(event)

    def write_frame(
        self,
        handle,
        payload: bytearray,
        deadline_monotonic: float,
    ) -> None:
        prefix = _frame_length_prefix(len(payload))
        try:
            self._transfer_exact(handle, prefix, deadline_monotonic, write=True)
            self._transfer_exact(handle, payload, deadline_monotonic, write=True)
        finally:
            _wipe(prefix)

    def read_frame(self, handle, deadline_monotonic: float) -> bytearray:
        prefix = bytearray(_U32_BE.size)
        payload = None
        try:
            self._transfer_exact(handle, prefix, deadline_monotonic, write=False)
            payload = bytearray(_parse_frame_length(prefix))
            self._transfer_exact(handle, payload, deadline_monotonic, write=False)
            return payload
        except BaseException:
            _wipe(payload)
            raise
        finally:
            _wipe(prefix)

    def cancel_and_close(self, handle) -> None:
        if handle is None or handle == self._INVALID_HANDLE_VALUE:
            return
        self._CancelIoEx(handle, None)
        self._CloseHandle(handle)


class WindowsNamedPipeOtpOpaqueIngressPort:
    """Strictly-online ``OtpOpaqueIngressPort`` for the native broker."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        test_namespace: str | None = None,
        _kernel=None,
        _session_id: int | None = None,
        _monotonic=time.monotonic,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("OTP Windows broker enabled must be a boolean")
        self._enabled = enabled
        if test_namespace is None:
            environment_namespace = os.environ.get(
                "CLIPVAULT_OTP_TEST_NAMESPACE",
                "",
            )
            self._test_namespace = (
                environment_namespace
                if _TEST_NAMESPACE_RE.fullmatch(environment_namespace) is not None
                else ""
            )
        else:
            self._test_namespace = _validated_test_namespace(test_namespace)
        self._kernel = _kernel
        self._session_id = _session_id
        self._monotonic = _monotonic
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._closed = False
        self._in_forward = False
        self._active_handle = None

    def _get_kernel(self):
        if self._kernel is not None:
            return self._kernel
        if os.name != "nt":
            raise _PipeUnavailable("Windows broker unavailable")
        self._kernel = _Win32PipeKernel(monotonic=self._monotonic)
        return self._kernel

    def forward(
        self,
        envelope: OtpOpaqueEnvelope,
        *,
        deadline_monotonic: float,
    ) -> None:
        if not self._enabled:
            raise OtpOpaqueBrokerUnavailable()
        if (
            isinstance(deadline_monotonic, bool)
            or not isinstance(deadline_monotonic, (int, float))
            or not math.isfinite(float(deadline_monotonic))
        ):
            raise OtpOpaqueBrokerUnavailable("otp_broker_timeout")

        now = self._monotonic()
        deadline = min(
            float(deadline_monotonic),
            now + OTP_BROKER_FORWARD_TIMEOUT_S,
        )
        if deadline <= now:
            raise OtpOpaqueBrokerUnavailable("otp_broker_timeout")

        with self._lock:
            if self._closed or self._in_forward:
                raise OtpOpaqueBrokerUnavailable()
            self._in_forward = True

        request = None
        response = None
        handle = None
        close_handle = False
        try:
            request = _encode_offer(envelope)
            kernel = self._get_kernel()
            session_id = (
                kernel.current_session_id()
                if self._session_id is None
                else self._session_id
            )
            pipe_name = _pipe_name(session_id, self._test_namespace)
            handle = kernel.connect(
                pipe_name,
                deadline,
                self._cancel_requested,
            )
            with self._lock:
                if self._closed:
                    close_handle = True
                else:
                    self._active_handle = handle
            if close_handle:
                kernel.cancel_and_close(handle)
                handle = None
                raise _PipeUnavailable("broker port closed")

            kernel.write_frame(handle, request, deadline)
            response = kernel.read_frame(handle, deadline)
            status = _decode_response(response)
            if status != _BROKER_STATUS_ACCEPTED:
                raise OtpOpaqueBrokerUnavailable("otp_broker_rejected")
        except OtpOpaqueBrokerUnavailable:
            raise
        except _PipeTimeout:
            raise OtpOpaqueBrokerUnavailable("otp_broker_timeout") from None
        except _PipeProtocol:
            raise OtpOpaqueBrokerUnavailable("otp_broker_protocol") from None
        except Exception:
            raise OtpOpaqueBrokerUnavailable() from None
        finally:
            _wipe(request)
            _wipe(response)
            if handle is not None:
                with self._lock:
                    if self._active_handle is handle:
                        self._active_handle = None
                        close_handle = True
                    else:
                        close_handle = False
                if close_handle:
                    try:
                        self._get_kernel().cancel_and_close(handle)
                    except Exception:
                        pass
            with self._lock:
                self._in_forward = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_requested.set()
            handle = self._active_handle
            self._active_handle = None
        if handle is not None:
            try:
                self._get_kernel().cancel_and_close(handle)
            except Exception:
                pass
