"""Bounded Windows Named Pipe adapter for opaque OTP broker offers.

This module mirrors the frozen ``CVOB`` v1 broker wire owned by the native
Windows OTP broker.  It never decrypts, logs, or persists an envelope.  The
pipe server owns local-only ACLs and ``PIPE_REJECT_REMOTE_CLIENTS``.  Before
the first protocol byte is written, this client verifies the server process,
user, session, final path, Authenticode trust, and package publisher.  It then
performs one overlapped request/response exchange within the caller's absolute
deadline.
"""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
import re
import struct
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

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
_BROKER_STATUS_ROTATION_REQUIRED = 9
_BROKER_STATUS_MIN = 1
_BROKER_STATUS_MAX = _BROKER_STATUS_ROTATION_REQUIRED
_BROKER_ALGORITHM_A256GCM = 1
_BROKER_MAX_FRAME_BYTES = 512
_BROKER_HEADER = struct.Struct(">4sBBBB")
_U32_BE = struct.Struct(">I")
_U64_BE = struct.Struct(">Q")
_TEST_NAMESPACE_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_DEVELOPMENT_TRUST_ENV = "CLIPVAULT_INSECURE_DEVELOPMENT_PIPE_TRUST"
_TEST_TRUST_ENV = "CLIPVAULT_INSECURE_TEST_PIPE_TRUST"


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
            status == _BROKER_STATUS_CONSUMED
            and secret_length < OTP_RELAY_MIN_CIPHERTEXT_BYTES
        )
        or (
            status != _BROKER_STATUS_CONSUMED
            and secret_length != 0
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


def _explicit_unsigned_trust_enabled(test_namespace: str) -> bool:
    """Return the narrowly scoped unsigned-build escape hatch.

    A private test namespace and the test trust switch are independent: a
    namespace never weakens trust by itself, and the test switch is ignored
    for the production pipe.  The development switch is intentionally
    explicit, is honored only by a source-tree process, and relaxes only
    Authenticode/publisher verification.
    """

    # A packaged Desktop process is part of the production trust boundary.
    # Never let an inherited environment variable turn off Authenticode
    # verification there; the escape hatch is available only to an explicit
    # source-tree development process.  Test trust remains separately scoped
    # by the private namespace below.
    development = (
        not getattr(sys, "frozen", False)
        and os.environ.get(_DEVELOPMENT_TRUST_ENV, "") == "1"
    )
    test = bool(test_namespace) and os.environ.get(_TEST_TRUST_ENV, "") == "1"
    return development or test


def _production_trust_paths() -> tuple[Path, Path]:
    """Derive the immutable installed identities from the frozen executable.

    The Broker path is never accepted from TOML, an environment variable, a
    pipe peer, or another user-writable data file.  Source-tree Python runs are
    not a production OTP transport and therefore fail closed.
    """

    if getattr(sys, "frozen", False) is not True:
        raise _PipeUnavailable("Windows broker production layout unavailable")
    desktop = Path(sys.executable)
    if not desktop.is_absolute() or desktop.name.casefold() != "clipvault.exe":
        raise _PipeUnavailable("Windows broker production layout unavailable")
    broker = desktop.parent / "ime" / "otp-broker" / "ClipVaultOtpBroker.exe"
    return desktop, broker


def _publisher_sets_intersect(
    first: tuple[bytes, ...],
    second: tuple[bytes, ...],
) -> bool:
    """Compare independently trusted signer leaves by SPKI SHA-256."""

    if not first or not second:
        return False
    if any(type(value) is not bytes or len(value) != 32 for value in first):
        return False
    if any(type(value) is not bytes or len(value) != 32 for value in second):
        return False
    return not set(first).isdisjoint(second)


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


if os.name == "nt":

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]


    class _Guid(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]


    class _WintrustFileInfo(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]


    class _WintrustSignatureSettings(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("dwIndex", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("cSecondarySigs", wintypes.DWORD),
            ("dwVerifiedSigIndex", wintypes.DWORD),
            ("pCryptoPolicy", ctypes.c_void_p),
        ]


    class _WintrustData(ctypes.Structure):
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


    class _CryptDataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]


    class _CryptAlgorithmIdentifier(ctypes.Structure):
        _fields_ = [
            ("pszObjId", ctypes.c_char_p),
            ("Parameters", _CryptDataBlob),
        ]


    class _CryptBitBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
            ("cUnusedBits", wintypes.DWORD),
        ]


    class _CertPublicKeyInfo(ctypes.Structure):
        _fields_ = [
            ("Algorithm", _CryptAlgorithmIdentifier),
            ("PublicKey", _CryptBitBlob),
        ]


    class _CertInfo(ctypes.Structure):
        _fields_ = [
            ("dwVersion", wintypes.DWORD),
            ("SerialNumber", _CryptDataBlob),
            ("SignatureAlgorithm", _CryptAlgorithmIdentifier),
            ("Issuer", _CryptDataBlob),
            ("NotBefore", wintypes.FILETIME),
            ("NotAfter", wintypes.FILETIME),
            ("Subject", _CryptDataBlob),
            ("SubjectPublicKeyInfo", _CertPublicKeyInfo),
            ("IssuerUniqueId", _CryptBitBlob),
            ("SubjectUniqueId", _CryptBitBlob),
            ("cExtension", wintypes.DWORD),
            ("rgExtension", ctypes.c_void_p),
        ]


    class _CertContext(ctypes.Structure):
        _fields_ = [
            ("dwCertEncodingType", wintypes.DWORD),
            ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbCertEncoded", wintypes.DWORD),
            ("pCertInfo", ctypes.POINTER(_CertInfo)),
            ("hCertStore", wintypes.HANDLE),
        ]


    class _CryptProviderSigner(ctypes.Structure):
        # Only the prefix needed by WTHelperGetProvCertFromChain is exposed.
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("sftVerifyAsOf", wintypes.FILETIME),
            ("csCertChain", wintypes.DWORD),
            ("pasCertChain", ctypes.c_void_p),
        ]


    class _CryptProviderCert(ctypes.Structure):
        # pCert is a PCCERT_CONTEXT owned by the WinVerifyTrust state.
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pCert", ctypes.c_void_p),
        ]


    _WINTRUST_ACTION_GENERIC_VERIFY_V2 = _Guid(
        0x00AAC56B,
        0xCD44,
        0x11D0,
        (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE),
    )


class _Win32PipeKernel:
    """Small ctypes boundary; all transfers use FILE_FLAG_OVERLAPPED."""

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _SECURITY_SQOS_PRESENT = 0x00100000
    _SECURITY_IDENTIFICATION = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_SHARE_READ = 0x00000001
    _FILE_NAME_NORMALIZED = 0x00000000
    _VOLUME_NAME_DOS = 0x00000000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x00001000
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _WTD_UI_NONE = 2
    _WTD_REVOKE_NONE = 0
    _WTD_CHOICE_FILE = 1
    _WTD_STATEACTION_VERIFY = 1
    _WTD_STATEACTION_CLOSE = 2
    _WTD_CACHE_ONLY_URL_RETRIEVAL = 0x00001000
    _WSS_VERIFY_SPECIFIC = 0x00000001
    _WSS_GET_SECONDARY_SIG_COUNT = 0x00000002
    _X509_ASN_ENCODING = 0x00000001
    _X509_PUBLIC_KEY_INFO = 8
    _MAX_SECONDARY_SIGNATURES = 16
    _MAX_ENCODED_SPKI_BYTES = 64 * 1024
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
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        self._kernel32 = kernel32
        self._advapi32 = advapi32
        self._crypt32 = crypt32
        self._wintrust = wintrust
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
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self._GetCurrentProcessId = kernel32.GetCurrentProcessId
        self._GetCurrentProcessId.argtypes = []
        self._GetCurrentProcessId.restype = wintypes.DWORD
        self._ProcessIdToSessionId = kernel32.ProcessIdToSessionId
        self._ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._ProcessIdToSessionId.restype = wintypes.BOOL
        self._GetNamedPipeServerProcessId = kernel32.GetNamedPipeServerProcessId
        self._GetNamedPipeServerProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.ULONG),
        ]
        self._GetNamedPipeServerProcessId.restype = wintypes.BOOL
        self._OpenProcess = kernel32.OpenProcess
        self._OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._OpenProcess.restype = wintypes.HANDLE
        self._QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        self._QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._GetModuleFileNameW = kernel32.GetModuleFileNameW
        self._GetModuleFileNameW.argtypes = [
            wintypes.HMODULE,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        self._GetModuleFileNameW.restype = wintypes.DWORD
        self._GetFinalPathNameByHandleW = kernel32.GetFinalPathNameByHandleW
        self._GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self._LocalFree = kernel32.LocalFree
        self._LocalFree.argtypes = [ctypes.c_void_p]
        self._LocalFree.restype = ctypes.c_void_p
        self._OpenProcessToken = advapi32.OpenProcessToken
        self._OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._OpenProcessToken.restype = wintypes.BOOL
        self._GetTokenInformation = advapi32.GetTokenInformation
        self._GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._GetTokenInformation.restype = wintypes.BOOL
        self._ConvertSidToStringSidW = advapi32.ConvertSidToStringSidW
        self._ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self._ConvertSidToStringSidW.restype = wintypes.BOOL
        self._WinVerifyTrust = wintrust.WinVerifyTrust
        self._WinVerifyTrust.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_Guid),
            ctypes.c_void_p,
        ]
        self._WinVerifyTrust.restype = ctypes.c_long
        self._WTHelperProvDataFromStateData = (
            wintrust.WTHelperProvDataFromStateData
        )
        self._WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
        self._WTHelperProvDataFromStateData.restype = ctypes.c_void_p
        self._WTHelperGetProvSignerFromChain = (
            wintrust.WTHelperGetProvSignerFromChain
        )
        self._WTHelperGetProvSignerFromChain.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._WTHelperGetProvSignerFromChain.restype = ctypes.POINTER(
            _CryptProviderSigner
        )
        self._WTHelperGetProvCertFromChain = (
            wintrust.WTHelperGetProvCertFromChain
        )
        self._WTHelperGetProvCertFromChain.argtypes = [
            ctypes.POINTER(_CryptProviderSigner),
            wintypes.DWORD,
        ]
        self._WTHelperGetProvCertFromChain.restype = ctypes.POINTER(
            _CryptProviderCert
        )
        self._CryptEncodeObjectEx = crypt32.CryptEncodeObjectEx
        self._CryptEncodeObjectEx.argtypes = [
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._CryptEncodeObjectEx.restype = wintypes.BOOL
        current_process = kernel32.GetCurrentProcess()
        self._current_user_sid = self._process_user_sid(current_process)

    def current_session_id(self) -> int:
        session_id = wintypes.DWORD()
        if not self._ProcessIdToSessionId(
            self._GetCurrentProcessId(),
            ctypes.byref(session_id),
        ):
            raise _PipeUnavailable("Windows session unavailable")
        return int(session_id.value)

    @staticmethod
    def _normalize_final_path(path: str) -> str:
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[len("\\\\?\\UNC\\") :]
        elif path.startswith("\\\\?\\"):
            path = path[len("\\\\?\\") :]
        return path.casefold()

    def _process_user_sid(self, process) -> str:
        token = wintypes.HANDLE()
        if not self._OpenProcessToken(
            process,
            self._TOKEN_QUERY,
            ctypes.byref(token),
        ):
            raise _PipeUnavailable("broker identity unavailable")
        try:
            required = wintypes.DWORD()
            self._GetTokenInformation(
                token,
                self._TOKEN_USER,
                None,
                0,
                ctypes.byref(required),
            )
            if required.value == 0:
                raise _PipeUnavailable("broker identity unavailable")
            storage = ctypes.create_string_buffer(required.value)
            if not self._GetTokenInformation(
                token,
                self._TOKEN_USER,
                storage,
                required,
                ctypes.byref(required),
            ):
                raise _PipeUnavailable("broker identity unavailable")
            sid = ctypes.cast(
                storage,
                ctypes.POINTER(_TokenUser),
            ).contents.User.Sid
            text = wintypes.LPWSTR()
            if not self._ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise _PipeUnavailable("broker identity unavailable")
            try:
                return str(text.value)
            finally:
                self._LocalFree(text)
        finally:
            self._CloseHandle(token)

    def _process_image(self, process) -> str:
        size = wintypes.DWORD(32_768)
        output = ctypes.create_unicode_buffer(size.value)
        if not self._QueryFullProcessImageNameW(
            process,
            0,
            output,
            ctypes.byref(size),
        ):
            raise _PipeUnavailable("broker identity unavailable")
        return output.value

    def _current_process_image(self) -> str:
        output = ctypes.create_unicode_buffer(32_768)
        length = self._GetModuleFileNameW(None, output, len(output))
        if length == 0 or length >= len(output):
            raise _PipeUnavailable("broker identity unavailable")
        return output.value

    def _open_identity_file(self, path: str | os.PathLike[str]):
        handle = self._CreateFileW(
            os.fspath(path),
            self._GENERIC_READ | self._FILE_READ_ATTRIBUTES,
            self._FILE_SHARE_READ,
            None,
            self._OPEN_EXISTING,
            self._FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == self._INVALID_HANDLE_VALUE:
            raise _PipeUnavailable("broker identity unavailable")
        return handle

    def _final_path(self, handle) -> str:
        output = ctypes.create_unicode_buffer(32_768)
        length = self._GetFinalPathNameByHandleW(
            handle,
            output,
            len(output),
            self._FILE_NAME_NORMALIZED | self._VOLUME_NAME_DOS,
        )
        if length == 0 or length >= len(output):
            raise _PipeUnavailable("broker identity unavailable")
        return self._normalize_final_path(output.value[:length])

    def _certificate_spki_sha256(self, certificate_context) -> bytes | None:
        if not certificate_context:
            return None
        try:
            certificate = ctypes.cast(
                certificate_context,
                ctypes.POINTER(_CertContext),
            ).contents
            if not certificate.pCertInfo:
                return None
            public_key_info = ctypes.byref(
                certificate.pCertInfo.contents.SubjectPublicKeyInfo
            )
        except (TypeError, ValueError):
            return None

        encoded_size = wintypes.DWORD()
        if not self._CryptEncodeObjectEx(
            self._X509_ASN_ENCODING,
            ctypes.c_void_p(self._X509_PUBLIC_KEY_INFO),
            public_key_info,
            0,
            None,
            None,
            ctypes.byref(encoded_size),
        ):
            return None
        if not 0 < encoded_size.value <= self._MAX_ENCODED_SPKI_BYTES:
            return None

        encoded = (ctypes.c_ubyte * encoded_size.value)()
        actual_size = wintypes.DWORD(encoded_size.value)
        try:
            if not self._CryptEncodeObjectEx(
                self._X509_ASN_ENCODING,
                ctypes.c_void_p(self._X509_PUBLIC_KEY_INFO),
                public_key_info,
                0,
                None,
                encoded,
                ctypes.byref(actual_size),
            ):
                return None
            if not 0 < actual_size.value <= encoded_size.value:
                return None
            return hashlib.sha256(
                bytes(encoded[: actual_size.value])
            ).digest()
        finally:
            ctypes.memset(encoded, 0, encoded_size.value)

    def _trusted_signature_spki(
        self,
        path: str,
        handle,
        *,
        signature_index: int,
        query_secondary_count: bool,
    ) -> tuple[bytes | None, int | None]:
        file_info = _WintrustFileInfo(
            ctypes.sizeof(_WintrustFileInfo),
            path,
            handle,
            None,
        )
        signature = _WintrustSignatureSettings(
            cbStruct=ctypes.sizeof(_WintrustSignatureSettings),
            dwIndex=signature_index,
            dwFlags=(
                self._WSS_GET_SECONDARY_SIG_COUNT
                if query_secondary_count
                else self._WSS_VERIFY_SPECIFIC
            ),
        )
        trust = _WintrustData(
            cbStruct=ctypes.sizeof(_WintrustData),
            dwUIChoice=self._WTD_UI_NONE,
            fdwRevocationChecks=self._WTD_REVOKE_NONE,
            dwUnionChoice=self._WTD_CHOICE_FILE,
            pFile=ctypes.addressof(file_info),
            dwStateAction=self._WTD_STATEACTION_VERIFY,
            dwProvFlags=self._WTD_CACHE_ONLY_URL_RETRIEVAL,
            pSignatureSettings=ctypes.addressof(signature),
        )
        secondary_count = None
        try:
            status = self._WinVerifyTrust(
                None,
                ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
                ctypes.byref(trust),
            )
            if query_secondary_count:
                secondary_count = int(signature.cSecondarySigs)
                if secondary_count > self._MAX_SECONDARY_SIGNATURES:
                    return None, None
            if status != 0 or not trust.hWVTStateData:
                return None, secondary_count
            provider = self._WTHelperProvDataFromStateData(trust.hWVTStateData)
            if not provider:
                return None, secondary_count
            signer = self._WTHelperGetProvSignerFromChain(provider, 0, False, 0)
            if not signer:
                return None, secondary_count
            certificate = self._WTHelperGetProvCertFromChain(signer, 0)
            if not certificate or not certificate.contents.pCert:
                return None, secondary_count
            return (
                self._certificate_spki_sha256(certificate.contents.pCert),
                secondary_count,
            )
        finally:
            if trust.hWVTStateData:
                trust.dwStateAction = self._WTD_STATEACTION_CLOSE
                self._WinVerifyTrust(
                    None,
                    ctypes.byref(_WINTRUST_ACTION_GENERIC_VERIFY_V2),
                    ctypes.byref(trust),
                )

    def _trusted_publisher_spkis(self, path: str, handle) -> tuple[bytes, ...]:
        primary, secondary_count = self._trusted_signature_spki(
            path,
            handle,
            signature_index=0,
            query_secondary_count=True,
        )
        if secondary_count is None:
            return ()
        publishers = []
        if primary is not None:
            publishers.append(primary)
        for signature_index in range(1, secondary_count + 1):
            publisher, _ = self._trusted_signature_spki(
                path,
                handle,
                signature_index=signature_index,
                query_secondary_count=False,
            )
            if publisher is not None and publisher not in publishers:
                publishers.append(publisher)
        return tuple(publishers)

    def verify_server(
        self,
        handle,
        *,
        expected_broker_path: Path,
        expected_desktop_path: Path,
        allow_unsigned: bool,
    ) -> bool:
        """Authenticate the pipe server before any protocol byte is written."""

        server_pid = wintypes.ULONG()
        if not self._GetNamedPipeServerProcessId(
            handle,
            ctypes.byref(server_pid),
        ) or server_pid.value == 0:
            return False
        current_session = self.current_session_id()
        peer_session = wintypes.DWORD()
        if not self._ProcessIdToSessionId(
            server_pid.value,
            ctypes.byref(peer_session),
        ) or peer_session.value != current_session:
            return False

        process = self._OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            server_pid.value,
        )
        if not process:
            return False
        try:
            if self._process_user_sid(process) != self._current_user_sid:
                return False
            process_image = self._process_image(process)
        finally:
            self._CloseHandle(process)

        actual_broker = None
        expected_broker = None
        actual_desktop = None
        expected_desktop = None
        try:
            actual_broker = self._open_identity_file(process_image)
            expected_broker = self._open_identity_file(expected_broker_path)
            actual_desktop = self._open_identity_file(self._current_process_image())
            expected_desktop = self._open_identity_file(expected_desktop_path)
            if self._final_path(actual_broker) != self._final_path(expected_broker):
                return False
            if self._final_path(actual_desktop) != self._final_path(expected_desktop):
                return False
            if allow_unsigned:
                return True

            # Match the native pipe trust contract: independently verify every
            # bounded embedded signature and compare publisher public keys,
            # not renewable leaf certificates.  This permits controlled
            # certificate rotation and dual signing without accepting another
            # publisher that merely chains to a trusted root.
            broker_publishers = self._trusted_publisher_spkis(
                self._final_path(actual_broker),
                actual_broker,
            )
            desktop_publishers = self._trusted_publisher_spkis(
                self._final_path(actual_desktop),
                actual_desktop,
            )
            return _publisher_sets_intersect(
                broker_publishers,
                desktop_publishers,
            )
        except (
            ctypes.ArgumentError,
            OSError,
            OverflowError,
            TypeError,
            ValueError,
            _PipeUnavailable,
        ):
            return False
        finally:
            for file_handle in (
                expected_desktop,
                actual_desktop,
                expected_broker,
                actual_broker,
            ):
                if file_handle not in (None, self._INVALID_HANDLE_VALUE):
                    self._CloseHandle(file_handle)

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
                self._FILE_ATTRIBUTE_NORMAL
                | self._FILE_FLAG_OVERLAPPED
                | self._SECURITY_SQOS_PRESENT
                | self._SECURITY_IDENTIFICATION,
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

    def cancel(self, handle) -> None:
        """Request cancellation without invalidating another thread's OVERLAPPED."""

        if handle is None or handle == self._INVALID_HANDLE_VALUE:
            return
        self._CancelIoEx(handle, None)


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
        _test_install_executable_path: str | os.PathLike[str] | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise ValueError("OTP Windows broker enabled must be a boolean")
        self._enabled = enabled
        if test_namespace is None:
            environment_namespace = os.environ.get(
                "CLIPVAULT_OTP_TEST_NAMESPACE",
                "",
            )
            self._test_namespace = _validated_test_namespace(environment_namespace)
        else:
            self._test_namespace = _validated_test_namespace(test_namespace)
        if _test_install_executable_path is not None:
            if _kernel is None or not self._test_namespace:
                raise ValueError(
                    "test install path requires an injected kernel and namespace"
                )
            test_desktop = Path(_test_install_executable_path)
            if not test_desktop.is_absolute():
                raise ValueError("test install executable path must be absolute")
            self._test_trust_paths = (
                test_desktop,
                test_desktop.parent
                / "ime"
                / "otp-broker"
                / "ClipVaultOtpBroker.exe",
            )
        else:
            self._test_trust_paths = None
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
            desktop_path, broker_path = (
                self._test_trust_paths
                if self._test_trust_paths is not None
                else _production_trust_paths()
            )
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

            if not kernel.verify_server(
                handle,
                expected_broker_path=broker_path,
                expected_desktop_path=desktop_path,
                allow_unsigned=_explicit_unsigned_trust_enabled(
                    self._test_namespace
                ),
            ):
                raise _PipeUnavailable("broker identity rejected")

            kernel.write_frame(handle, request, deadline)
            response = kernel.read_frame(handle, deadline)
            status = _decode_response(response)
            if status == _BROKER_STATUS_ROTATION_REQUIRED:
                raise OtpOpaqueBrokerUnavailable(
                    "otp_pair_rotation_required"
                )
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
            if handle is not None:
                try:
                    # Keep the state lock through this non-blocking cancellation
                    # request.  The forwarding thread cannot clear and close the
                    # numeric handle until CancelIoEx has captured it, so handle
                    # reuse cannot redirect cancellation to unrelated I/O.  The
                    # forwarding thread still owns OVERLAPPED completion and the
                    # final CloseHandle operation.
                    self._get_kernel().cancel(handle)
                except Exception:
                    pass
