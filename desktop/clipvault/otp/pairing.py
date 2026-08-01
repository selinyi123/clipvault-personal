"""OTP-only pair identity authority and Windows credential persistence.

The legacy sync bearer authenticates which peer is making a request, but its
device identifier and token hash are never used as OTP AEAD identities or key
material.  SQLite stores routing metadata only.  The independent pair verifier
is written to Windows Credential Manager in the frozen 96-byte ``CVPK`` v1
record and is returned once to the authenticated peer.
"""

from __future__ import annotations

import base64
import ctypes
import os
import re
import secrets
import sqlite3
import struct
import threading
import uuid
from dataclasses import dataclass
from typing import Protocol
from ctypes import wintypes

from clipvault.otp.ingress import (
    OtpOpaqueBrokerUnavailable,
    OtpPairRoute,
)
from clipvault.store.unit_of_work import unit_of_work


OTP_PAIR_ROUTE = "/api/otp/pair"
OTP_PAIR_MAX_BODY_BYTES = 512
OTP_CREDENTIAL_TARGET_PREFIX = "ClipVault/OTP/Pair/v1/"
OTP_PAIR_CREDENTIAL_BYTES = 96

_SYNC_DEVICE_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")
_CREDENTIAL_MAGIC = b"CVPK"
_CREDENTIAL_VERSION = 1
_CREDENTIAL_RESERVED = b"\x00\x00\x00"
_U64_BE = struct.Struct(">Q")


def _wipe(buffer: bytearray | None) -> None:
    if buffer is not None:
        buffer[:] = b"\x00" * len(buffer)


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid UUID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise ValueError("invalid UUID") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid UUID")
    return value


def _canonical_device_id(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("device:"):
        raise ValueError("invalid OTP device identity")
    _canonical_uuid(value[len("device:") :])
    return value


def _sync_device_id(value: str) -> str:
    if not isinstance(value, str) or _SYNC_DEVICE_ID_RE.fullmatch(value) is None:
        raise ValueError("invalid sync device identity")
    return value


def _credential_target(session_epoch: str) -> str:
    return OTP_CREDENTIAL_TARGET_PREFIX + _canonical_uuid(session_epoch)


class OtpPairingError(RuntimeError):
    """Content-free error safe for the OTP pairing HTTP boundary."""

    def __init__(self, security_code: str, http_status: int) -> None:
        super().__init__(security_code)
        self.security_code = security_code
        self.http_status = http_status


class OtpPairingRejected(OtpPairingError):
    def __init__(self, security_code: str = "otp_pair_bad_request") -> None:
        super().__init__(security_code, 400)


class OtpPairingConflict(OtpPairingError):
    def __init__(self) -> None:
        super().__init__("otp_pair_exists", 409)


class OtpPairingUnavailable(OtpPairingError):
    def __init__(self, security_code: str = "otp_pairing_unavailable") -> None:
        super().__init__(security_code, 503)


class OtpCredentialStore(Protocol):
    def write(self, target: str, blob: bytearray) -> None: ...

    def read(self, target: str) -> bytearray | None: ...

    def delete(self, target: str) -> bool: ...

    def close(self) -> None: ...


class DisabledOtpCredentialStore:
    def write(self, target: str, blob: bytearray) -> None:
        del target, blob
        raise OtpPairingUnavailable()

    def read(self, target: str) -> bytearray | None:
        del target
        raise OtpPairingUnavailable()

    def delete(self, target: str) -> bool:
        del target
        raise OtpPairingUnavailable()

    def close(self) -> None:
        return None


def encode_pair_credential(
    *,
    session_epoch: str,
    sender_device_id: str,
    target_device_id: str,
    verifier: bytearray,
    high_sequence: int = 0,
) -> bytearray:
    if (
        not isinstance(verifier, bytearray)
        or len(verifier) != 32
        or isinstance(high_sequence, bool)
        or not isinstance(high_sequence, int)
        or not 0 <= high_sequence <= 0xFFFFFFFFFFFFFFFF
    ):
        raise ValueError("invalid OTP pair credential")
    frame = bytearray(OTP_PAIR_CREDENTIAL_BYTES)
    try:
        frame[0:4] = _CREDENTIAL_MAGIC
        frame[4] = _CREDENTIAL_VERSION
        frame[5:8] = _CREDENTIAL_RESERVED
        frame[8:24] = uuid.UUID(_canonical_uuid(session_epoch)).bytes
        frame[24:40] = uuid.UUID(
            _canonical_device_id(sender_device_id)[len("device:") :]
        ).bytes
        frame[40:56] = uuid.UUID(
            _canonical_device_id(target_device_id)[len("device:") :]
        ).bytes
        frame[56:88] = verifier
        _U64_BE.pack_into(frame, 88, high_sequence)
        return frame
    except BaseException:
        _wipe(frame)
        raise


@dataclass(slots=True, repr=False)
class DecodedPairCredential:
    session_epoch: str
    sender_device_id: str
    target_device_id: str
    verifier: bytearray
    high_sequence: int
    _closed: bool = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _wipe(self.verifier)

    def __repr__(self) -> str:
        return "<DecodedPairCredential redacted>"


def decode_pair_credential(blob: bytearray) -> DecodedPairCredential:
    if (
        not isinstance(blob, bytearray)
        or len(blob) != OTP_PAIR_CREDENTIAL_BYTES
        or blob[0:4] != _CREDENTIAL_MAGIC
        or blob[4] != _CREDENTIAL_VERSION
        or blob[5:8] != _CREDENTIAL_RESERVED
    ):
        raise ValueError("invalid OTP pair credential")
    session_epoch = str(uuid.UUID(bytes=bytes(blob[8:24])))
    sender_device_id = f"device:{uuid.UUID(bytes=bytes(blob[24:40]))}"
    target_device_id = f"device:{uuid.UUID(bytes=bytes(blob[40:56]))}"
    try:
        _canonical_uuid(session_epoch)
        _canonical_device_id(sender_device_id)
        _canonical_device_id(target_device_id)
    except ValueError:
        raise ValueError("invalid OTP pair credential") from None
    return DecodedPairCredential(
        session_epoch=session_epoch,
        sender_device_id=sender_device_id,
        target_device_id=target_device_id,
        verifier=bytearray(blob[56:88]),
        high_sequence=_U64_BE.unpack_from(blob, 88)[0],
    )


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialManagerStore:
    """Per-user Generic Credentials for frozen ``CVPK`` records."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2
    _ERROR_NOT_FOUND = 1168

    def __init__(self, *, enabled: bool = False) -> None:
        if type(enabled) is not bool:
            raise ValueError("OTP credential store enabled must be a boolean")
        self._enabled = enabled
        self._lock = threading.Lock()
        self._closed = False
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self._enabled or os.name != "nt":
            raise OtpPairingUnavailable()
        try:
            advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
            self._CredWriteW = advapi32.CredWriteW
            self._CredWriteW.argtypes = [
                ctypes.POINTER(_CredentialW),
                wintypes.DWORD,
            ]
            self._CredWriteW.restype = wintypes.BOOL
            self._CredReadW = advapi32.CredReadW
            self._CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_CredentialW)),
            ]
            self._CredReadW.restype = wintypes.BOOL
            self._CredDeleteW = advapi32.CredDeleteW
            self._CredDeleteW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self._CredDeleteW.restype = wintypes.BOOL
            self._CredFree = advapi32.CredFree
            self._CredFree.argtypes = [ctypes.c_void_p]
            self._CredFree.restype = None
        except Exception:
            raise OtpPairingUnavailable() from None
        self._loaded = True

    def _assert_open(self) -> None:
        if self._closed:
            raise OtpPairingUnavailable()
        self._load()

    @staticmethod
    def _validate(target: str, blob: bytearray | None = None) -> None:
        if not isinstance(target, str) or not target.startswith(
            OTP_CREDENTIAL_TARGET_PREFIX
        ):
            raise OtpPairingUnavailable("otp_pair_credential_invalid")
        try:
            if _credential_target(
                target[len(OTP_CREDENTIAL_TARGET_PREFIX) :]
            ) != target:
                raise ValueError
        except ValueError:
            raise OtpPairingUnavailable(
                "otp_pair_credential_invalid"
            ) from None
        if blob is not None and (
            not isinstance(blob, bytearray)
            or len(blob) != OTP_PAIR_CREDENTIAL_BYTES
        ):
            raise OtpPairingUnavailable("otp_pair_credential_invalid")

    def write(self, target: str, blob: bytearray) -> None:
        self._validate(target, blob)
        with self._lock:
            self._assert_open()
            buffer = (wintypes.BYTE * len(blob)).from_buffer(blob)
            credential = _CredentialW()
            credential.Type = self._CRED_TYPE_GENERIC
            credential.TargetName = target
            credential.CredentialBlobSize = len(blob)
            credential.CredentialBlob = ctypes.cast(
                buffer,
                ctypes.POINTER(wintypes.BYTE),
            )
            credential.Persist = self._CRED_PERSIST_LOCAL_MACHINE
            credential.UserName = "ClipVault OTP Pair v1"
            if not self._CredWriteW(ctypes.byref(credential), 0):
                raise OtpPairingUnavailable()

    def read(self, target: str) -> bytearray | None:
        self._validate(target)
        with self._lock:
            self._assert_open()
            credential_pointer = ctypes.POINTER(_CredentialW)()
            if not self._CredReadW(
                target,
                self._CRED_TYPE_GENERIC,
                0,
                ctypes.byref(credential_pointer),
            ):
                if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                    return None
                raise OtpPairingUnavailable()
            result = None
            try:
                credential = credential_pointer.contents
                if credential.CredentialBlobSize != OTP_PAIR_CREDENTIAL_BYTES:
                    raise OtpPairingUnavailable(
                        "otp_pair_credential_invalid"
                    )
                result = bytearray(credential.CredentialBlobSize)
                output = (wintypes.BYTE * len(result)).from_buffer(result)
                ctypes.memmove(
                    output,
                    credential.CredentialBlob,
                    len(result),
                )
                return result
            except OtpPairingError:
                _wipe(result)
                raise
            except BaseException:
                _wipe(result)
                raise OtpPairingUnavailable() from None
            finally:
                self._CredFree(credential_pointer)

    def delete(self, target: str) -> bool:
        self._validate(target)
        with self._lock:
            self._assert_open()
            if self._CredDeleteW(target, self._CRED_TYPE_GENERIC, 0):
                return True
            if ctypes.get_last_error() == self._ERROR_NOT_FOUND:
                return False
            raise OtpPairingUnavailable()

    def close(self) -> None:
        with self._lock:
            self._closed = True


@dataclass(frozen=True, slots=True)
class OtpPairRouteRecord:
    sync_device_id: str
    session_epoch: str
    sender_device_id: str
    target_device_id: str
    credential_target: str
    revoked: bool

    def __post_init__(self) -> None:
        _sync_device_id(self.sync_device_id)
        _canonical_uuid(self.session_epoch)
        _canonical_device_id(self.sender_device_id)
        _canonical_device_id(self.target_device_id)
        if self.sender_device_id == self.target_device_id:
            raise ValueError("invalid OTP pair route")
        if self.credential_target != _credential_target(self.session_epoch):
            raise ValueError("invalid OTP credential target")
        if type(self.revoked) is not bool:
            raise ValueError("invalid OTP revocation state")


class _OtpPairRouteRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def _record(row) -> OtpPairRouteRecord:
        try:
            revoked = row["revoked"]
            if type(revoked) is not int or revoked not in (0, 1):
                raise ValueError("invalid OTP revocation state")
            return OtpPairRouteRecord(
                sync_device_id=row["sync_device_id"],
                session_epoch=row["session_epoch"],
                sender_device_id=row["sender_device_id"],
                target_device_id=row["target_device_id"],
                credential_target=row["credential_target"],
                revoked=revoked == 1,
            )
        except (KeyError, TypeError, ValueError):
            raise OtpPairingUnavailable("otp_pair_metadata_invalid") from None

    def get(self, sync_device_id: str) -> OtpPairRouteRecord | None:
        row = self.conn.execute(
            "SELECT sync_device_id, session_epoch, sender_device_id, "
            "target_device_id, credential_target, revoked "
            "FROM otp_pair_routes WHERE sync_device_id = ?",
            (_sync_device_id(sync_device_id),),
        ).fetchone()
        return None if row is None else self._record(row)

    def insert(self, record: OtpPairRouteRecord) -> None:
        self.conn.execute(
            "INSERT INTO otp_pair_routes"
            "(sync_device_id, session_epoch, sender_device_id, "
            "target_device_id, credential_target, revoked) "
            "VALUES (?,?,?,?,?,0)",
            (
                record.sync_device_id,
                record.session_epoch,
                record.sender_device_id,
                record.target_device_id,
                record.credential_target,
            ),
        )

    def mark_revoked(self, sync_device_id: str) -> bool:
        cursor = self.conn.execute(
            "UPDATE otp_pair_routes SET revoked = 1 WHERE sync_device_id = ?",
            (_sync_device_id(sync_device_id),),
        )
        return cursor.rowcount > 0

    def delete(self, sync_device_id: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM otp_pair_routes WHERE sync_device_id = ?",
            (_sync_device_id(sync_device_id),),
        )
        return cursor.rowcount > 0


class SqliteOtpPairIdentityPort:
    """Read-only authenticated sync peer to OTP identity mapping."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._repo = _OtpPairRouteRepo(conn)
        self._lock = threading.Lock()
        self._closed = False

    def resolve(self, authenticated_sync_device_id: str) -> OtpPairRoute | None:
        with self._lock:
            if self._closed:
                raise OtpOpaqueBrokerUnavailable(
                    "otp_pair_identity_unavailable"
                )
            try:
                record = self._repo.get(authenticated_sync_device_id)
            except Exception:
                raise OtpOpaqueBrokerUnavailable(
                    "otp_pair_identity_unavailable"
                ) from None
        if record is None or record.revoked:
            return None
        return OtpPairRoute(
            sender_device=record.sender_device_id,
            target_device=record.target_device_id,
        )

    def close(self) -> None:
        with self._lock:
            self._closed = True


@dataclass(frozen=True, slots=True, repr=False)
class OtpPairingResult:
    session_epoch: str
    sender_device_id: str
    target_device_id: str
    verifier: str

    def response(self) -> dict[str, object]:
        return {
            "version": 1,
            "session_epoch": self.session_epoch,
            "sender_device_id": self.sender_device_id,
            "target_device_id": self.target_device_id,
            "verifier": self.verifier,
        }

    def __repr__(self) -> str:
        return "<OtpPairingResult redacted>"


class DisabledOtpPairingAuthority:
    def pair(
        self,
        authenticated_sync_device_id: str,
        body: dict,
    ) -> OtpPairingResult:
        del authenticated_sync_device_id, body
        raise OtpPairingUnavailable()

    def revoke(self, authenticated_sync_device_id: str) -> bool:
        del authenticated_sync_device_id
        return False

    def close(self) -> None:
        return None


class SqliteOtpPairingAuthority:
    """Create/revoke one OTP route and its external verifier credential."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        credential_store: OtpCredentialStore,
        *,
        pairing_enabled: bool = False,
        uuid4=uuid.uuid4,
        token_bytes=secrets.token_bytes,
    ) -> None:
        if type(pairing_enabled) is not bool:
            raise ValueError("OTP pairing enabled must be a boolean")
        for method in ("write", "read", "delete", "close"):
            if not callable(getattr(credential_store, method, None)):
                raise TypeError("invalid OTP credential store")
        self._conn = conn
        self._repo = _OtpPairRouteRepo(conn)
        self._store = credential_store
        self._pairing_enabled = pairing_enabled
        self._uuid4 = uuid4
        self._token_bytes = token_bytes
        self._lock = threading.Lock()
        self._closed = False

    def _assert_idle(self) -> None:
        if self._conn.in_transaction:
            raise OtpPairingUnavailable("otp_pair_transaction_busy")

    def pair(
        self,
        authenticated_sync_device_id: str,
        body: dict,
    ) -> OtpPairingResult:
        if not self._pairing_enabled:
            raise OtpPairingUnavailable()
        if not isinstance(body, dict) or set(body) != {"sender_device_id"}:
            raise OtpPairingRejected()
        try:
            sync_device_id = _sync_device_id(authenticated_sync_device_id)
            sender_device_id = _canonical_device_id(body["sender_device_id"])
        except (KeyError, ValueError):
            raise OtpPairingRejected() from None

        with self._lock:
            if self._closed:
                raise OtpPairingUnavailable()
            self._assert_idle()
            try:
                if self._repo.get(sync_device_id) is not None:
                    raise OtpPairingConflict()
            except OtpPairingError:
                raise
            except Exception:
                raise OtpPairingUnavailable() from None

            verifier = None
            try:
                session_epoch = str(self._uuid4())
                target_device_id = f"device:{self._uuid4()}"
                _canonical_uuid(session_epoch)
                _canonical_device_id(target_device_id)
                verifier = bytearray(self._token_bytes(32))
            except Exception:
                raise OtpPairingUnavailable() from None
            if sender_device_id == target_device_id:
                _wipe(verifier)
                raise OtpPairingUnavailable()
            credential_target = _credential_target(session_epoch)
            credential = None
            credential_written = False
            try:
                if len(verifier) != 32:
                    raise OtpPairingUnavailable()
                verifier_text = (
                    base64.urlsafe_b64encode(verifier)
                    .rstrip(b"=")
                    .decode("ascii")
                )
                credential = encode_pair_credential(
                    session_epoch=session_epoch,
                    sender_device_id=sender_device_id,
                    target_device_id=target_device_id,
                    verifier=verifier,
                )
                record = OtpPairRouteRecord(
                    sync_device_id=sync_device_id,
                    session_epoch=session_epoch,
                    sender_device_id=sender_device_id,
                    target_device_id=target_device_id,
                    credential_target=credential_target,
                    revoked=False,
                )
                try:
                    with unit_of_work(self._conn):
                        if self._repo.get(sync_device_id) is not None:
                            raise OtpPairingConflict()
                        self._store.write(credential_target, credential)
                        credential_written = True
                        self._repo.insert(record)
                except OtpPairingConflict:
                    raise
                except Exception:
                    if credential_written:
                        try:
                            self._store.delete(credential_target)
                        except Exception:
                            raise OtpPairingUnavailable(
                                "otp_pair_rollback_failed"
                            ) from None
                    raise OtpPairingUnavailable() from None
                return OtpPairingResult(
                    session_epoch=session_epoch,
                    sender_device_id=sender_device_id,
                    target_device_id=target_device_id,
                    verifier=verifier_text,
                )
            finally:
                _wipe(credential)
                _wipe(verifier)

    def revoke(self, authenticated_sync_device_id: str) -> bool:
        try:
            sync_device_id = _sync_device_id(authenticated_sync_device_id)
        except ValueError:
            raise OtpPairingUnavailable() from None
        with self._lock:
            if self._closed:
                raise OtpPairingUnavailable()
            self._assert_idle()
            try:
                record = self._repo.get(sync_device_id)
                if record is None:
                    return False
                if not record.revoked:
                    with unit_of_work(self._conn):
                        self._repo.mark_revoked(sync_device_id)
                self._store.delete(record.credential_target)
                with unit_of_work(self._conn):
                    self._repo.delete(sync_device_id)
                return True
            except OtpPairingError:
                raise
            except Exception:
                raise OtpPairingUnavailable("otp_pair_revoke_failed") from None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._store.close()


def disabled_pair_identity_factory(_conn):
    del _conn
    from clipvault.otp.ingress import DisabledOtpPairIdentityPort

    return DisabledOtpPairIdentityPort()


def disabled_pairing_authority_factory(_conn) -> DisabledOtpPairingAuthority:
    del _conn
    return DisabledOtpPairingAuthority()
