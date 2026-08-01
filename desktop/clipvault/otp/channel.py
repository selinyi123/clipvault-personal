"""OTP-only authenticated envelopes for the in-process synthetic transport.

This executable slice uses an encrypt-then-MAC construction made only from
Python standard-library HMAC-SHA256 primitives so the protocol lifecycle can
be tested without adding a runtime dependency.  It is intentionally named a
synthetic channel: production Android/Windows transport must replace it with a
reviewed platform AEAD implementation while preserving these ownership,
expiry, replay, and ACK contracts.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import struct
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from .relay import (
    CapacityExceeded,
    InvalidOtp,
    OtpRelayError,
    OtpRelayStore,
    ReplayRejected,
    SessionMismatch,
    StoreClosed,
    TargetMismatch,
)


class EnvelopeAuthenticationFailed(OtpRelayError):
    """An envelope or ACK did not authenticate for this pair session."""


class EnvelopeExpired(OtpRelayError):
    """An authenticated envelope is outside its bounded delivery window."""


class AckRejected(OtpRelayError):
    """An ACK is invalid, expired, conflicting, or no longer pending."""


_VERSION = 1
_NONCE_BYTES = 24
_TAG_BYTES = hashlib.sha256().digest_size
_HEADER = struct.Struct(">B16s16s16s16sQqq24s")
_ACK_PREFIX = b"ClipVault OTP Relay ACK v1\0"
_KEY_PREFIX = b"ClipVault OTP Relay key v1\0"
_STREAM_PREFIX = b"ClipVault OTP Relay stream v1\0"
_MAX_SEQUENCE = (2**63) - 1
_MAX_CIPHERTEXT_BYTES = 32
T = TypeVar("T")


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def _uuid4(value: str, name: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, AttributeError, ValueError):
        raise InvalidOtp(f"invalid {name}") from None
    if str(parsed) != value or parsed.version != 4:
        raise InvalidOtp(f"invalid {name}")
    return parsed


def _device_uuid(value: str, name: str) -> uuid.UUID:
    OtpRelayStore._validate_device(value, name)
    return _uuid4(value[len("device:") :], f"{name} device")


def _duration(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _hmac(key: bytearray, value: bytes) -> bytes:
    return hmac.new(bytes(key), value, hashlib.sha256).digest()


def _expand(prk: bytearray, info: bytes) -> bytearray:
    return bytearray(_hmac(prk, info + b"\x01"))


def _direction_info(sender_device: str, target_device: str) -> bytes:
    sender = _device_uuid(sender_device, "sender")
    target = _device_uuid(target_device, "target")
    return sender.bytes + target.bytes


def _header_bytes(envelope: "EncryptedOtpEnvelope") -> bytes:
    return _HEADER.pack(
        envelope.protocol_version,
        _uuid4(envelope.session_epoch, "session epoch").bytes,
        _uuid4(envelope.event_id, "event id").bytes,
        _device_uuid(envelope.sender_device, "sender").bytes,
        _device_uuid(envelope.target_device, "target").bytes,
        envelope.sequence,
        envelope.issued_at_unix_ms,
        envelope.expires_at_unix_ms,
        bytes(envelope.nonce),
    )


@dataclass(slots=True, repr=False)
class EncryptedOtpEnvelope:
    """Owned encrypted envelope; transport must wipe it after final ACK."""

    protocol_version: int
    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    issued_at_unix_ms: int
    expires_at_unix_ms: int
    nonce: bytearray = field(repr=False)
    ciphertext: bytearray = field(repr=False)
    tag: bytearray = field(repr=False)
    _completion_capability: object | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.protocol_version != _VERSION:
            raise InvalidOtp("unsupported OTP envelope version")
        _uuid4(self.session_epoch, "session epoch")
        _uuid4(self.event_id, "event id")
        _device_uuid(self.sender_device, "sender")
        _device_uuid(self.target_device, "target")
        if type(self.sequence) is not int or not 0 < self.sequence <= _MAX_SEQUENCE:
            raise InvalidOtp("invalid OTP envelope sequence")
        if (
            type(self.issued_at_unix_ms) is not int
            or type(self.expires_at_unix_ms) is not int
            or not 0 <= self.issued_at_unix_ms <= _MAX_SEQUENCE
            or not 0 < self.expires_at_unix_ms <= _MAX_SEQUENCE
        ):
            raise InvalidOtp("invalid OTP envelope timestamps")
        if (
            type(self.nonce) is not bytearray
            or not 12 <= len(self.nonce) <= 32
        ):
            raise InvalidOtp("invalid OTP envelope nonce")
        if (
            type(self.ciphertext) is not bytearray
            or not 0 < len(self.ciphertext) <= _MAX_CIPHERTEXT_BYTES
        ):
            raise InvalidOtp("invalid OTP envelope ciphertext")
        if type(self.tag) is not bytearray or not 16 <= len(self.tag) <= 64:
            raise InvalidOtp("invalid OTP envelope tag")

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        _wipe(self.nonce)
        _wipe(self.ciphertext)
        _wipe(self.tag)
        self._closed = True

    def __repr__(self) -> str:
        return "<EncryptedOtpEnvelope redacted>"


@dataclass(slots=True, repr=False)
class OtpDeliveryAck:
    protocol_version: int
    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    envelope_tag_digest: bytearray = field(repr=False)
    tag: bytearray = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.protocol_version != _VERSION:
            raise InvalidOtp("unsupported OTP ACK version")
        _uuid4(self.session_epoch, "session epoch")
        _uuid4(self.event_id, "event id")
        _device_uuid(self.sender_device, "sender")
        _device_uuid(self.target_device, "target")
        if type(self.sequence) is not int or not 0 < self.sequence <= _MAX_SEQUENCE:
            raise InvalidOtp("invalid OTP ACK sequence")
        if (
            type(self.envelope_tag_digest) is not bytearray
            or len(self.envelope_tag_digest) != _TAG_BYTES
            or type(self.tag) is not bytearray
            or not 16 <= len(self.tag) <= 64
        ):
            raise InvalidOtp("invalid OTP ACK authentication")

    def close(self) -> None:
        if self._closed:
            return
        _wipe(self.envelope_tag_digest)
        _wipe(self.tag)
        self._closed = True

    def __repr__(self) -> str:
        return "<OtpDeliveryAck redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedOtpDelivery:
    """Authenticated metadata passed to the local admission callback."""

    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    expires_at_monotonic: float

    def __repr__(self) -> str:
        return "<AuthenticatedOtpDelivery redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class OtpReceiveResult:
    ack: OtpDeliveryAck = field(repr=False)
    duplicate: bool
    admitted: object | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class CompletionReceipt:
    """Immutable result of ACK authentication for atomic transport completion."""

    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    envelope_tag_digest: bytes = field(repr=False)
    _event_capability: object = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<CompletionReceipt redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _PendingAck:
    sequence: int
    expires_at_unix_ms: int
    envelope_tag_digest: bytes = field(repr=False)
    completion_capability: object = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, repr=False)
class _Receipt:
    sequence: int
    expires_at_unix_ms: int
    envelope_tag_digest: bytes = field(repr=False)


@runtime_checkable
class OtpPairChannelPort(Protocol):
    """Integration port for a reviewed pairwise encrypted OTP channel.

    The public production composer remains unavailable until a reviewed
    platform factory supplies the real implementation.
    """

    def seal(
        self,
        candidate: bytearray,
        *,
        authorized_session_epoch: str,
        authorized_sender_device: str,
        authorized_target_device: str,
        ttl_seconds: float,
    ) -> EncryptedOtpEnvelope: ...

    def receive(
        self,
        envelope: EncryptedOtpEnvelope,
        admit: Callable[[AuthenticatedOtpDelivery, memoryview], object],
    ) -> OtpReceiveResult: ...

    def verify_ack(self, ack: OtpDeliveryAck) -> CompletionReceipt: ...

    def complete_ack(self, receipt: CompletionReceipt) -> None: ...

    def cancel_pending(self, event_id: str) -> None: ...

    def close(self) -> None: ...


class SyntheticOtpPairChannel:
    """One in-memory pair session with bounded replay and authenticated ACKs."""

    def __init__(
        self,
        *,
        root_secret: bytearray,
        session_epoch: str,
        local_device: str,
        remote_device: str,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        max_ttl_seconds: float = 120.0,
        max_clock_skew_seconds: float = 30.0,
        capacity: int = 64,
        nonce_history_capacity: int = 1024,
        nonce_factory: Callable[[], bytearray] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ):
        if type(root_secret) is not bytearray or not 32 <= len(root_secret) <= 64:
            if type(root_secret) is bytearray:
                _wipe(root_secret)
            raise InvalidOtp("OTP pair secret must be an owned 32-64 byte buffer")
        try:
            self._session_epoch = str(_uuid4(session_epoch, "session epoch"))
            self._local_device = OtpRelayStore._validate_target(local_device)
            self._remote_device = OtpRelayStore._validate_target(remote_device)
            if self._local_device == self._remote_device:
                raise InvalidOtp("OTP pair devices must differ")
            if not callable(wall_clock) or not callable(monotonic_clock):
                raise ValueError("OTP channel clocks must be callable")
            if type(capacity) is not int or capacity <= 0:
                raise ValueError("OTP channel capacity must be positive")
            if (
                type(nonce_history_capacity) is not int
                or nonce_history_capacity < capacity
            ):
                raise ValueError(
                    "OTP nonce history capacity must be at least channel capacity"
                )
            self._max_ttl = _duration(max_ttl_seconds, "max_ttl_seconds")
            self._max_clock_skew = _duration(
                max_clock_skew_seconds,
                "max_clock_skew_seconds",
            )
            salt = hashlib.sha256(
                _KEY_PREFIX + _uuid4(session_epoch, "session epoch").bytes
            ).digest()
            self._prk = bytearray(hmac.new(salt, bytes(root_secret), hashlib.sha256).digest())
        finally:
            _wipe(root_secret)

        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._capacity = capacity
        self._nonce_history_capacity = nonce_history_capacity
        self._nonce_factory = nonce_factory or (
            lambda: bytearray(secrets.token_bytes(_NONCE_BYTES))
        )
        self._event_id_factory = event_id_factory or (lambda: str(uuid.uuid4()))
        self._next_sequence = 1
        self._pending: dict[str, _PendingAck] = {}
        self._receipts: dict[str, _Receipt] = {}
        self._used_nonce_digests: set[bytes] = set()
        self._last_wall: float | None = None
        self._last_monotonic: float | None = None
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "<SyntheticOtpPairChannel redacted>"

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _require_open_locked(self) -> None:
        if self._closed:
            raise StoreClosed("OTP pair channel is closed")

    def _poison_locked(self) -> None:
        if not self._closed:
            _wipe(self._prk)
        self._pending.clear()
        self._receipts.clear()
        self._used_nonce_digests.clear()
        self._closed = True

    def _read_clock_locked(self, source: Callable[[], float], *, monotonic: bool) -> float:
        try:
            value = float(source())
        except Exception:
            self._poison_locked()
            raise StoreClosed("OTP pair channel clock failed") from None
        if not math.isfinite(value):
            self._poison_locked()
            raise StoreClosed("OTP pair channel clock failed")
        previous = self._last_monotonic if monotonic else self._last_wall
        if previous is not None and value < previous:
            self._poison_locked()
            raise StoreClosed("OTP pair channel clock regressed")
        if monotonic:
            self._last_monotonic = value
        else:
            self._last_wall = value
        return value

    def _wall_locked(self) -> float:
        return self._read_clock_locked(self._wall_clock, monotonic=False)

    def _monotonic_locked(self) -> float:
        return self._read_clock_locked(self._monotonic_clock, monotonic=True)

    def _keys(self, sender: str, target: str) -> tuple[bytearray, bytearray, bytearray]:
        direction = _direction_info(sender, target)
        return (
            _expand(self._prk, _KEY_PREFIX + b"enc\0" + direction),
            _expand(self._prk, _KEY_PREFIX + b"mac\0" + direction),
            _expand(self._prk, _KEY_PREFIX + b"ack\0" + direction),
        )

    @staticmethod
    def _crypt(key: bytearray, nonce: bytearray, source: bytearray) -> bytearray:
        result = bytearray(len(source))
        offset = 0
        counter = 0
        while offset < len(source):
            block = _hmac(
                key,
                _STREAM_PREFIX + bytes(nonce) + counter.to_bytes(4, "big"),
            )
            count = min(len(block), len(source) - offset)
            for index in range(count):
                result[offset + index] = source[offset + index] ^ block[index]
            offset += count
            counter += 1
        return result

    def _sweep_locked(self, now_unix_ms: int) -> None:
        self._pending = {
            event_id: pending
            for event_id, pending in self._pending.items()
            if now_unix_ms < pending.expires_at_unix_ms
        }
        self._receipts = {
            event_id: receipt
            for event_id, receipt in self._receipts.items()
            if now_unix_ms
            < receipt.expires_at_unix_ms + int(self._max_clock_skew * 1000)
        }

    def seal(
        self,
        candidate: bytearray,
        *,
        authorized_session_epoch: str,
        authorized_sender_device: str,
        authorized_target_device: str,
        ttl_seconds: float,
    ) -> EncryptedOtpEnvelope:
        """Encrypt one normalized owned candidate and always wipe the source."""

        if type(candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")
        nonce: bytearray | None = None
        ciphertext: bytearray | None = None
        tag: bytearray | None = None
        keys: tuple[bytearray, bytearray, bytearray] | None = None
        envelope: EncryptedOtpEnvelope | None = None
        delivered = False
        try:
            if not 0 < len(candidate) <= _MAX_CIPHERTEXT_BYTES:
                raise InvalidOtp("invalid OTP candidate length")
            try:
                authorized_session_epoch = str(
                    _uuid4(authorized_session_epoch, "authorized session")
                )
                authorized_sender_device = OtpRelayStore._validate_sender(
                    authorized_sender_device
                )
                authorized_target_device = OtpRelayStore._validate_target(
                    authorized_target_device
                )
            except InvalidOtp:
                raise EnvelopeAuthenticationFailed(
                    "OTP capture authorization mismatch"
                ) from None
            if (
                authorized_session_epoch != self._session_epoch
                or authorized_sender_device != self._local_device
                or authorized_target_device != self._remote_device
            ):
                raise EnvelopeAuthenticationFailed(
                    "OTP capture authorization mismatch"
                )
            ttl = _duration(ttl_seconds, "ttl_seconds")
            if ttl > self._max_ttl:
                raise InvalidOtp("OTP envelope TTL exceeds policy")
            with self._lock:
                self._require_open_locked()
                now = self._wall_locked()
                now_ms = int(now * 1000)
                self._sweep_locked(now_ms)
                if len(self._pending) >= self._capacity:
                    raise CapacityExceeded("OTP pending ACK capacity reached")
                if self._next_sequence > _MAX_SEQUENCE:
                    self._poison_locked()
                    raise StoreClosed("OTP channel sequence exhausted")
                sequence = self._next_sequence
                event_id = str(_uuid4(self._event_id_factory(), "event id"))
                if event_id in self._pending:
                    raise ReplayRejected("OTP event id collision")
                nonce = self._nonce_factory()
                if type(nonce) is not bytearray or len(nonce) != _NONCE_BYTES:
                    raise InvalidOtp("OTP nonce factory returned an invalid buffer")
                nonce_digest = hashlib.sha256(bytes(nonce)).digest()
                if nonce_digest in self._used_nonce_digests:
                    raise ReplayRejected("OTP nonce reuse rejected")
                if len(self._used_nonce_digests) >= self._nonce_history_capacity:
                    raise CapacityExceeded(
                        "OTP nonce history exhausted; rotate pair session"
                    )
                expires_ms = now_ms + math.ceil(ttl * 1000)
                if expires_ms > _MAX_SEQUENCE:
                    raise InvalidOtp("OTP envelope timestamp exceeds protocol")
                completion_capability = object()
                envelope = EncryptedOtpEnvelope(
                    protocol_version=_VERSION,
                    session_epoch=self._session_epoch,
                    event_id=event_id,
                    sender_device=self._local_device,
                    target_device=self._remote_device,
                    sequence=sequence,
                    issued_at_unix_ms=now_ms,
                    expires_at_unix_ms=expires_ms,
                    nonce=nonce,
                    ciphertext=bytearray(len(candidate)),
                    tag=bytearray(_TAG_BYTES),
                    _completion_capability=completion_capability,
                )
                keys = self._keys(self._local_device, self._remote_device)
                ciphertext = self._crypt(keys[0], nonce, candidate)
                envelope.ciphertext[:] = ciphertext
                tag = bytearray(
                    _hmac(keys[1], _header_bytes(envelope) + bytes(envelope.ciphertext))
                )
                envelope.tag[:] = tag
                digest = hashlib.sha256(bytes(envelope.tag)).digest()
                self._pending[event_id] = _PendingAck(
                    sequence=sequence,
                    expires_at_unix_ms=expires_ms,
                    envelope_tag_digest=digest,
                    completion_capability=completion_capability,
                )
                self._used_nonce_digests.add(nonce_digest)
                self._next_sequence += 1
                delivered = True
                return envelope
        finally:
            _wipe(candidate)
            if ciphertext is not None:
                _wipe(ciphertext)
            if tag is not None:
                _wipe(tag)
            if keys is not None:
                for key in keys:
                    _wipe(key)
            if not delivered:
                if envelope is not None:
                    envelope.close()
                elif nonce is not None:
                    _wipe(nonce)

    def _validate_envelope_locked(self, envelope: EncryptedOtpEnvelope) -> tuple[int, bytes]:
        if not isinstance(envelope, EncryptedOtpEnvelope) or envelope.closed:
            raise EnvelopeAuthenticationFailed("OTP envelope authentication failed")
        try:
            if (
                envelope.protocol_version != _VERSION
                or type(envelope.sequence) is not int
                or not 0 < envelope.sequence <= _MAX_SEQUENCE
                or type(envelope.issued_at_unix_ms) is not int
                or type(envelope.expires_at_unix_ms) is not int
                or not 0 <= envelope.issued_at_unix_ms <= _MAX_SEQUENCE
                or not 0 < envelope.expires_at_unix_ms <= _MAX_SEQUENCE
                or type(envelope.nonce) is not bytearray
                or len(envelope.nonce) != _NONCE_BYTES
                or type(envelope.ciphertext) is not bytearray
                or not 0 < len(envelope.ciphertext) <= _MAX_CIPHERTEXT_BYTES
                or type(envelope.tag) is not bytearray
                or len(envelope.tag) != _TAG_BYTES
            ):
                raise InvalidOtp("invalid OTP envelope shape")
            _uuid4(envelope.session_epoch, "session epoch")
            _uuid4(envelope.event_id, "event id")
            _device_uuid(envelope.sender_device, "sender")
            _device_uuid(envelope.target_device, "target")
        except (InvalidOtp, ValueError, OverflowError, struct.error):
            raise EnvelopeAuthenticationFailed(
                "OTP envelope authentication failed"
            ) from None
        if envelope.session_epoch != self._session_epoch:
            raise SessionMismatch("OTP envelope belongs to another session")
        if envelope.sender_device != self._remote_device:
            raise EnvelopeAuthenticationFailed("OTP envelope authentication failed")
        if envelope.target_device != self._local_device:
            raise TargetMismatch("OTP target mismatch")
        now_ms = int(self._wall_locked() * 1000)
        ttl_ms = envelope.expires_at_unix_ms - envelope.issued_at_unix_ms
        if (
            ttl_ms <= 0
            or ttl_ms > int(self._max_ttl * 1000)
            or envelope.issued_at_unix_ms
            > now_ms + int(self._max_clock_skew * 1000)
            or now_ms >= envelope.expires_at_unix_ms
        ):
            raise EnvelopeExpired("OTP envelope expired")
        keys = self._keys(envelope.sender_device, envelope.target_device)
        try:
            expected = _hmac(
                keys[1],
                _header_bytes(envelope) + bytes(envelope.ciphertext),
            )
            if not secrets.compare_digest(expected, envelope.tag):
                raise EnvelopeAuthenticationFailed(
                    "OTP envelope authentication failed"
                )
        finally:
            for key in keys:
                _wipe(key)
        return now_ms, hashlib.sha256(bytes(envelope.tag)).digest()

    def _ack_locked(
        self,
        envelope: EncryptedOtpEnvelope,
        envelope_tag_digest: bytes,
    ) -> OtpDeliveryAck:
        ack = OtpDeliveryAck(
            protocol_version=_VERSION,
            session_epoch=envelope.session_epoch,
            event_id=envelope.event_id,
            sender_device=envelope.sender_device,
            target_device=envelope.target_device,
            sequence=envelope.sequence,
            envelope_tag_digest=bytearray(envelope_tag_digest),
            tag=bytearray(_TAG_BYTES),
        )
        keys = self._keys(envelope.sender_device, envelope.target_device)
        try:
            ack.tag[:] = _hmac(keys[2], self._ack_bytes(ack))
        finally:
            for key in keys:
                _wipe(key)
        return ack

    @staticmethod
    def _ack_bytes(ack: OtpDeliveryAck) -> bytes:
        return (
            _ACK_PREFIX
            + bytes([ack.protocol_version])
            + _uuid4(ack.session_epoch, "session epoch").bytes
            + _uuid4(ack.event_id, "event id").bytes
            + _device_uuid(ack.sender_device, "sender").bytes
            + _device_uuid(ack.target_device, "target").bytes
            + ack.sequence.to_bytes(8, "big")
            + bytes(ack.envelope_tag_digest)
        )

    def receive(
        self,
        envelope: EncryptedOtpEnvelope,
        admit: Callable[[AuthenticatedOtpDelivery, memoryview], T],
    ) -> OtpReceiveResult:
        """Authenticate/decrypt once, call local admission, and issue an ACK.

        An exact retry after a lost ACK returns a fresh authenticated ACK but
        never invokes ``admit`` twice.  Conflicting reuse of the event id is
        rejected.  Plaintext exists only in a temporary wiped lease.
        """

        if not callable(admit):
            raise InvalidOtp("invalid OTP admission callback")
        plaintext: bytearray | None = None
        view: memoryview | None = None
        keys: tuple[bytearray, bytearray, bytearray] | None = None
        with self._lock:
            self._require_open_locked()
            now_ms, tag_digest = self._validate_envelope_locked(envelope)
            self._sweep_locked(now_ms)
            receipt = self._receipts.get(envelope.event_id)
            if receipt is not None:
                if (
                    receipt.sequence != envelope.sequence
                    or not secrets.compare_digest(
                        receipt.envelope_tag_digest,
                        tag_digest,
                    )
                ):
                    raise ReplayRejected("OTP replay rejected")
                return OtpReceiveResult(
                    ack=self._ack_locked(envelope, tag_digest),
                    duplicate=True,
                )
            if len(self._receipts) >= self._capacity:
                raise CapacityExceeded("OTP receipt capacity reached")

            keys = self._keys(envelope.sender_device, envelope.target_device)
            try:
                plaintext = self._crypt(
                    keys[0],
                    envelope.nonce,
                    envelope.ciphertext,
                )
                view = memoryview(plaintext).toreadonly()
                remaining = (envelope.expires_at_unix_ms - now_ms) / 1000.0
                deadline = self._monotonic_locked() + min(remaining, self._max_ttl)
                delivery = AuthenticatedOtpDelivery(
                    session_epoch=envelope.session_epoch,
                    event_id=envelope.event_id,
                    sender_device=envelope.sender_device,
                    target_device=envelope.target_device,
                    sequence=envelope.sequence,
                    expires_at_monotonic=deadline,
                )
                admitted = admit(delivery, view)
                self._receipts[envelope.event_id] = _Receipt(
                    sequence=envelope.sequence,
                    expires_at_unix_ms=envelope.expires_at_unix_ms,
                    envelope_tag_digest=tag_digest,
                )
                return OtpReceiveResult(
                    ack=self._ack_locked(envelope, tag_digest),
                    duplicate=False,
                    admitted=admitted,
                )
            finally:
                if view is not None:
                    view.release()
                if plaintext is not None:
                    _wipe(plaintext)
                if keys is not None:
                    for key in keys:
                        _wipe(key)

    def verify_ack(self, ack: OtpDeliveryAck) -> CompletionReceipt:
        """Authenticate one ACK without mutating pending sender state."""

        if not isinstance(ack, OtpDeliveryAck) or ack._closed:
            raise AckRejected("OTP ACK rejected")
        with self._lock:
            self._require_open_locked()
            now_ms = int(self._wall_locked() * 1000)
            self._sweep_locked(now_ms)
            if (
                ack.session_epoch != self._session_epoch
                or ack.sender_device != self._local_device
                or ack.target_device != self._remote_device
            ):
                raise AckRejected("OTP ACK rejected")
            pending = self._pending.get(ack.event_id)
            if (
                pending is None
                or pending.sequence != ack.sequence
                or not secrets.compare_digest(
                    pending.envelope_tag_digest,
                    ack.envelope_tag_digest,
                )
            ):
                raise AckRejected("OTP ACK rejected")
            keys = self._keys(ack.sender_device, ack.target_device)
            try:
                expected = _hmac(keys[2], self._ack_bytes(ack))
                if not secrets.compare_digest(expected, ack.tag):
                    raise AckRejected("OTP ACK rejected")
            finally:
                for key in keys:
                    _wipe(key)
            return CompletionReceipt(
                session_epoch=ack.session_epoch,
                event_id=ack.event_id,
                sender_device=ack.sender_device,
                target_device=ack.target_device,
                sequence=ack.sequence,
                envelope_tag_digest=bytes(ack.envelope_tag_digest),
                _event_capability=pending.completion_capability,
            )

    def complete_ack(self, receipt: CompletionReceipt) -> None:
        """Retire pending metadata only after transport atomically completed."""

        if (
            not isinstance(receipt, CompletionReceipt)
        ):
            raise AckRejected("OTP completion receipt rejected")
        with self._lock:
            self._require_open_locked()
            pending = self._pending.get(receipt.event_id)
            if (
                pending is None
                or pending.sequence != receipt.sequence
                or receipt.session_epoch != self._session_epoch
                or receipt.sender_device != self._local_device
                or receipt.target_device != self._remote_device
                or receipt._event_capability is not pending.completion_capability
                or not secrets.compare_digest(
                    pending.envelope_tag_digest,
                    receipt.envelope_tag_digest,
                )
            ):
                raise AckRejected("OTP completion receipt rejected")
            del self._pending[receipt.event_id]

    def cancel_pending(self, event_id: str) -> None:
        """Retire sender metadata after transport refused envelope ownership."""

        event_id = str(_uuid4(event_id, "event id"))
        with self._lock:
            self._require_open_locked()
            self._pending.pop(event_id, None)

    def close(self) -> None:
        with self._lock:
            self._poison_locked()
