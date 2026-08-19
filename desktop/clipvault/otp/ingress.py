"""Strictly-online ingress for opaque platform AEAD envelopes.

Python never decrypts an envelope and this module has no persistence, sync,
clipboard, or network dependency.  A paired HTTP request is validated here and
then synchronously handed to a local Windows broker.  The broker owns CNG key
access and must authenticate every metadata field as AEAD associated data.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import struct
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


log = logging.getLogger("clipvault.otp.ingress")

OTP_RELAY_ROUTE = "/api/otp/relay"
OTP_RELAY_MAX_BODY_BYTES = 4_096
OTP_RELAY_PROTOCOL_VERSION = 1
OTP_RELAY_ALGORITHM = "A256GCM"
OTP_RELAY_MAX_TTL_MS = 180_000
OTP_RELAY_MAX_FUTURE_SKEW_MS = 30_000
OTP_RELAY_MAX_INTEGER = (2**63) - 1
OTP_RELAY_NONCE_BYTES = 12
OTP_RELAY_MIN_CIPHERTEXT_BYTES = 4
OTP_RELAY_MAX_CIPHERTEXT_BYTES = 8
OTP_RELAY_AUTHENTICATION_TAG_BYTES = 16
OTP_BROKER_FORWARD_TIMEOUT_S = 0.25

_BASE64URL_RE = re.compile(r"^[0-9A-Za-z_-]+$")
_EXPECTED_FIELDS = frozenset(
    {
        "version",
        "algorithm",
        "session_epoch",
        "event_id",
        "sender_device_id",
        "target_device_id",
        "sequence",
        "issued_at_ms",
        "expires_at_ms",
        "nonce",
        "ciphertext",
        "authentication_tag",
    }
)
_AAD_PREFIX = b"ClipVault OTP Relay AEAD v1\0"
_AAD_FIELDS = struct.Struct(">B16s16s16s16sQQQ")
_REJECTION_LOG_INTERVAL_S = 30.0


def _wipe(buffer: bytearray | None) -> None:
    if buffer is not None:
        buffer[:] = b"\x00" * len(buffer)


class OtpOpaqueIngressError(RuntimeError):
    """Content-free ingress failure suitable for an HTTP security response."""

    def __init__(self, security_code: str, http_status: int) -> None:
        super().__init__(security_code)
        self.security_code = security_code
        self.http_status = http_status


class OtpOpaqueEnvelopeRejected(OtpOpaqueIngressError):
    def __init__(self, security_code: str = "otp_bad_envelope", http_status: int = 400) -> None:
        super().__init__(security_code, http_status)


class OtpOpaqueBrokerUnavailable(OtpOpaqueIngressError):
    def __init__(self, security_code: str = "otp_broker_unavailable") -> None:
        super().__init__(security_code, 503)


class OtpOpaqueIngressPort(Protocol):
    """Bounded synchronous handoff to the local Windows OTP broker.

    A production implementation must use a per-user authenticated Named Pipe,
    perform CNG AEAD verification/decryption out of process, and enforce the
    absolute deadline with overlapped I/O plus ``CancelIoEx``. ``forward`` must
    return no later than that deadline and never retain ``envelope`` or its
    memoryviews. ``close`` must be idempotent, safe while ``forward`` is active,
    cancel pending I/O, and release pipe buffers. There is intentionally no
    generic Python worker fallback for a non-conforming adapter.
    """

    def forward(
        self,
        envelope: "OtpOpaqueEnvelope",
        *,
        deadline_monotonic: float,
    ) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class OtpPairRoute:
    """Versioned OTP-only pair identities, never legacy sync device IDs."""

    sender_device: str
    target_device: str

    def __post_init__(self) -> None:
        _device_identity(self.sender_device, "otp_bad_sender")
        _device_identity(self.target_device, "otp_bad_target")
        if self.sender_device == self.target_device:
            raise OtpOpaqueEnvelopeRejected("otp_bad_pair_identity")


class OtpPairIdentityPort(Protocol):
    """Resolve one authenticated sync peer into its OTP-only paired identities.

    The mapping belongs to the reviewed platform pairing authority.  Python
    receives only the two opaque ``device:<UUIDv4>`` identities needed to route
    this request; legacy sync IDs and human-readable names are never AEAD AAD.
    """

    def resolve(self, authenticated_sync_device_id: str) -> OtpPairRoute | None: ...

    def close(self) -> None: ...


class DisabledOtpOpaqueIngressPort:
    """Default-off placeholder until the reviewed Windows adapter exists."""

    def forward(
        self,
        envelope: "OtpOpaqueEnvelope",
        *,
        deadline_monotonic: float,
    ) -> None:
        del envelope
        del deadline_monotonic
        raise OtpOpaqueBrokerUnavailable()

    def close(self) -> None:
        return None


class DisabledOtpPairIdentityPort:
    def resolve(self, authenticated_sync_device_id: str) -> OtpPairRoute | None:
        del authenticated_sync_device_id
        raise OtpOpaqueBrokerUnavailable("otp_pair_identity_unavailable")

    def close(self) -> None:
        return None


@dataclass(slots=True)
class OtpOpaqueEnvelope:
    """Validated routing metadata plus still-encrypted, mutable wire buffers."""

    version: int
    algorithm: str
    session_epoch: str
    event_id: str
    sender_device_id: str
    target_device_id: str
    sequence: int
    issued_at_ms: int
    expires_at_ms: int
    nonce: bytearray = field(repr=False)
    ciphertext: bytearray = field(repr=False)
    authentication_tag: bytearray = field(repr=False)
    event_hash: str
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _wipe(self.nonce)
        _wipe(self.ciphertext)
        _wipe(self.authentication_tag)

    def __repr__(self) -> str:
        return (
            "<OtpOpaqueEnvelope redacted "
            f"event_hash={self.event_hash!r} closed={self._closed}>"
        )

@dataclass(frozen=True, slots=True)
class OtpOpaqueIngressReceipt:
    event_hash: str


def _identity_uuid_bytes(value: str) -> bytes:
    return uuid.UUID(value[len("device:") :]).bytes


def canonical_otp_aad(envelope: OtpOpaqueEnvelope) -> bytes:
    """Frozen v1 AAD shared by Android JCA and the Windows CNG broker.

    The algorithm is fixed by protocol v1. Nonce, ciphertext, and authentication
    tag are separate AEAD inputs and are not AAD fields.
    """

    return _AAD_PREFIX + _AAD_FIELDS.pack(
        envelope.version,
        uuid.UUID(envelope.session_epoch).bytes,
        uuid.UUID(envelope.event_id).bytes,
        _identity_uuid_bytes(envelope.sender_device_id),
        _identity_uuid_bytes(envelope.target_device_id),
        envelope.sequence,
        envelope.issued_at_ms,
        envelope.expires_at_ms,
    )


def _reject_constant(_value: str):
    raise ValueError("non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _uuid4(value, security_code: str) -> str:
    if not isinstance(value, str):
        raise OtpOpaqueEnvelopeRejected(security_code)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise OtpOpaqueEnvelopeRejected(security_code) from None
    canonical = str(parsed)
    if parsed.version != 4 or value != canonical:
        raise OtpOpaqueEnvelopeRejected(security_code)
    return canonical


def _device_identity(value, security_code: str) -> str:
    if not isinstance(value, str) or not value.startswith("device:"):
        raise OtpOpaqueEnvelopeRejected(security_code)
    canonical = _uuid4(value[len("device:") :], security_code)
    identity = f"device:{canonical}"
    if value != identity:
        raise OtpOpaqueEnvelopeRejected(security_code)
    return identity


def _integer(value, security_code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= OTP_RELAY_MAX_INTEGER
    ):
        raise OtpOpaqueEnvelopeRejected(security_code)
    return value


def _base64url(value, expected_min: int, expected_max: int) -> bytearray:
    if not isinstance(value, str) or _BASE64URL_RE.fullmatch(value) is None:
        raise OtpOpaqueEnvelopeRejected("otp_bad_encoding")
    encoded = value.encode("ascii")
    padded = encoded + (b"=" * ((4 - len(encoded) % 4) % 4))
    try:
        decoded = bytearray(
            base64.b64decode(padded, altchars=b"-_", validate=True)
        )
    except (ValueError, binascii.Error):
        raise OtpOpaqueEnvelopeRejected("otp_bad_encoding") from None
    if not expected_min <= len(decoded) <= expected_max:
        _wipe(decoded)
        raise OtpOpaqueEnvelopeRejected("otp_bad_encoding")
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=")
    if canonical != encoded:
        _wipe(decoded)
        raise OtpOpaqueEnvelopeRejected("otp_bad_encoding")
    return decoded


def parse_opaque_envelope(
    raw: bytes | bytearray,
    *,
    authenticated_sender: str,
    expected_target: str,
    now_ms: int,
) -> OtpOpaqueEnvelope:
    """Validate only wire shape, time bounds, and authenticated routing."""

    if not isinstance(raw, (bytes, bytearray)):
        raise OtpOpaqueEnvelopeRejected()
    if not 0 < len(raw) <= OTP_RELAY_MAX_BODY_BYTES:
        raise OtpOpaqueEnvelopeRejected()
    raw_prefix = bytes(raw[:3])
    if raw_prefix == b"\xef\xbb\xbf":
        raise OtpOpaqueEnvelopeRejected("otp_bad_encoding")
    try:
        body = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        raise OtpOpaqueEnvelopeRejected() from None
    if not isinstance(body, dict) or set(body) != _EXPECTED_FIELDS:
        raise OtpOpaqueEnvelopeRejected()

    nonce = None
    ciphertext = None
    authentication_tag = None
    try:
        version = _integer(body["version"], "otp_bad_version")
        if version != OTP_RELAY_PROTOCOL_VERSION:
            raise OtpOpaqueEnvelopeRejected("otp_bad_version")
        algorithm = body["algorithm"]
        if algorithm != OTP_RELAY_ALGORITHM:
            raise OtpOpaqueEnvelopeRejected("otp_bad_algorithm")
        session_epoch = _uuid4(body["session_epoch"], "otp_bad_session")
        event_id = _uuid4(body["event_id"], "otp_bad_event")
        sender_device_id = _device_identity(
            body["sender_device_id"], "otp_bad_sender"
        )
        target_device_id = _device_identity(
            body["target_device_id"], "otp_bad_target"
        )
        sequence = _integer(body["sequence"], "otp_bad_sequence")
        if sequence == 0:
            raise OtpOpaqueEnvelopeRejected("otp_bad_sequence")
        issued_at_ms = _integer(
            body["issued_at_ms"], "otp_bad_time"
        )
        expires_at_ms = _integer(
            body["expires_at_ms"], "otp_bad_time"
        )

        if sender_device_id != authenticated_sender:
            raise OtpOpaqueEnvelopeRejected("otp_sender_mismatch", 403)
        if target_device_id != expected_target:
            raise OtpOpaqueEnvelopeRejected("otp_target_mismatch", 403)
        if expires_at_ms <= now_ms:
            raise OtpOpaqueEnvelopeRejected("otp_expired", 410)
        if issued_at_ms > now_ms + OTP_RELAY_MAX_FUTURE_SKEW_MS:
            raise OtpOpaqueEnvelopeRejected("otp_not_yet_valid")
        ttl_ms = expires_at_ms - issued_at_ms
        if not 0 < ttl_ms <= OTP_RELAY_MAX_TTL_MS:
            raise OtpOpaqueEnvelopeRejected("otp_bad_ttl")

        nonce = _base64url(
            body["nonce"], OTP_RELAY_NONCE_BYTES, OTP_RELAY_NONCE_BYTES
        )
        ciphertext = _base64url(
            body["ciphertext"],
            OTP_RELAY_MIN_CIPHERTEXT_BYTES,
            OTP_RELAY_MAX_CIPHERTEXT_BYTES,
        )
        authentication_tag = _base64url(
            body["authentication_tag"],
            OTP_RELAY_AUTHENTICATION_TAG_BYTES,
            OTP_RELAY_AUTHENTICATION_TAG_BYTES,
        )
        return OtpOpaqueEnvelope(
            version=version,
            algorithm=algorithm,
            session_epoch=session_epoch,
            event_id=event_id,
            sender_device_id=sender_device_id,
            target_device_id=target_device_id,
            sequence=sequence,
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            nonce=nonce,
            ciphertext=ciphertext,
            authentication_tag=authentication_tag,
            event_hash=hashlib.sha256(event_id.encode("ascii")).hexdigest(),
        )
    except BaseException:
        _wipe(nonce)
        _wipe(ciphertext)
        _wipe(authentication_tag)
        raise


class _SecurityLogLimiter:
    """Bound repeated attacker-controlled rejects by a fixed security code."""

    def __init__(
        self,
        interval_s: float = _REJECTION_LOG_INTERVAL_S,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval_s = interval_s
        self._monotonic = monotonic
        self._last_by_code: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, security_code: str) -> bool:
        now = self._monotonic()
        with self._lock:
            previous = self._last_by_code.get(security_code)
            if previous is not None and now - previous < self._interval_s:
                return False
            # Codes are internal constants, but keep this defensive state
            # bounded if a future platform adapter violates that contract.
            if security_code not in self._last_by_code and len(self._last_by_code) >= 32:
                return False
            self._last_by_code[security_code] = now
            return True


_rejection_log_limiter = _SecurityLogLimiter()


def log_otp_security_event(
    level: int,
    security_code: str,
    error_class: str,
) -> None:
    """Emit one rate-limited, content-free OTP security event."""

    if _rejection_log_limiter.allow(security_code):
        log.log(
            level,
            "otp ingress rejected code=%s error=%s",
            security_code,
            error_class,
        )


class _OtpOpaqueIngressGate:
    """One synchronous deadline-aware call; no background OTP worker copy."""

    def __init__(self, port: OtpOpaqueIngressPort, timeout_s: float) -> None:
        if not callable(getattr(port, "forward", None)) or not callable(
            getattr(port, "close", None)
        ):
            raise TypeError("OTP ingress port must implement forward and close")
        self._port = port
        self._lock = threading.Lock()
        self._closed = False
        self._poisoned = False
        self._in_flight = False
        self._timeout_s = timeout_s

    def forward(self, envelope: OtpOpaqueEnvelope) -> None:
        with self._lock:
            if self._closed or self._poisoned or self._in_flight:
                raise OtpOpaqueBrokerUnavailable()
            self._in_flight = True
        deadline = time.monotonic() + self._timeout_s
        try:
            self._port.forward(
                envelope,
                deadline_monotonic=deadline,
            )
            if time.monotonic() > deadline:
                with self._lock:
                    self._poisoned = True
                raise OtpOpaqueBrokerUnavailable("otp_broker_timeout")
        except OtpOpaqueBrokerUnavailable as exc:
            if exc.security_code == "otp_broker_timeout":
                with self._lock:
                    self._poisoned = True
            raise
        finally:
            with self._lock:
                self._in_flight = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Do not hold the state lock: a reviewed adapter may need to cancel an
        # active forward call from the Runtime shutdown thread.
        self._port.close()


class _OtpPairIdentityGate:
    def __init__(self, port: OtpPairIdentityPort) -> None:
        if not callable(getattr(port, "resolve", None)) or not callable(
            getattr(port, "close", None)
        ):
            raise TypeError("OTP pair identity port must implement resolve and close")
        self._port = port
        self._lock = threading.Lock()
        self._closed = False

    def resolve(self, authenticated_sync_device_id: str) -> OtpPairRoute:
        with self._lock:
            if self._closed:
                raise OtpOpaqueBrokerUnavailable("otp_pair_identity_unavailable")
            route = self._port.resolve(authenticated_sync_device_id)
        if route is None:
            raise OtpOpaqueEnvelopeRejected("otp_pair_not_authorized", 403)
        if not isinstance(route, OtpPairRoute):
            raise OtpOpaqueBrokerUnavailable("otp_pair_identity_invalid")
        return route

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._port.close()


class OtpOpaqueIngress:
    """Content-free composition boundary used by the Desktop API."""

    def __init__(
        self,
        port: OtpOpaqueIngressPort | None = None,
        pair_identity_port: OtpPairIdentityPort | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
        broker_timeout_s: float = OTP_BROKER_FORWARD_TIMEOUT_S,
    ) -> None:
        if (
            isinstance(broker_timeout_s, bool)
            or not isinstance(broker_timeout_s, (int, float))
            or not math.isfinite(float(broker_timeout_s))
            or not 0 < float(broker_timeout_s) <= 2.0
        ):
            raise ValueError("OTP broker timeout must be between 0 and 2 seconds")
        self._gate = _OtpOpaqueIngressGate(
            port if port is not None else DisabledOtpOpaqueIngressPort(),
            float(broker_timeout_s),
        )
        self._pair_identity_gate = _OtpPairIdentityGate(
            pair_identity_port
            if pair_identity_port is not None
            else DisabledOtpPairIdentityPort()
        )
        self._now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)

    def relay(
        self,
        raw: bytes | bytearray,
        *,
        authenticated_sync_device_id: str,
    ) -> OtpOpaqueIngressReceipt:
        envelope = None
        try:
            pair_route = self._pair_identity_gate.resolve(
                authenticated_sync_device_id
            )
            envelope = parse_opaque_envelope(
                raw,
                authenticated_sender=pair_route.sender_device,
                expected_target=pair_route.target_device,
                now_ms=self._now_ms(),
            )
            self._gate.forward(envelope)
            return OtpOpaqueIngressReceipt(event_hash=envelope.event_hash)
        except OtpOpaqueIngressError as exc:
            log_otp_security_event(
                logging.WARNING,
                exc.security_code,
                exc.__class__.__name__,
            )
            raise
        except Exception as exc:
            log_otp_security_event(
                logging.ERROR,
                "otp_broker_failure",
                exc.__class__.__name__,
            )
            raise OtpOpaqueBrokerUnavailable("otp_broker_failure") from None
        finally:
            if envelope is not None:
                envelope.close()

    def close(self) -> None:
        for security_code, gate in (
            ("otp_broker_close_failure", self._gate),
            ("otp_pair_identity_close_failure", self._pair_identity_gate),
        ):
            try:
                gate.close()
            except Exception as exc:
                log.error(
                    "otp ingress close failed code=%s error=%s",
                    security_code,
                    exc.__class__.__name__,
                )
