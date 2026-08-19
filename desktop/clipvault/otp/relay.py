"""Bounded, process-local storage for one-time relay credentials.

There is intentionally no transport, persistence, clipboard, or UI integration
in this module.  Credential bytes are owned by the store and may be accessed
only inside one synchronous ``use_secret`` callback.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum


class OtpRelayError(Exception):
    """Base error whose subclasses use content-free messages."""


class InvalidOtp(OtpRelayError):
    pass


class CapacityExceeded(OtpRelayError):
    pass


class ReplayRejected(OtpRelayError):
    pass


class OtpNotFound(OtpRelayError):
    pass


class TargetMismatch(OtpRelayError):
    pass


class SenderMismatch(OtpRelayError):
    pass


class TargetRevoked(OtpRelayError):
    pass


class ClaimContextMismatch(OtpRelayError):
    pass


class SessionMismatch(OtpRelayError):
    pass


class InvalidTransition(OtpRelayError):
    pass


class OtpUseFailed(OtpRelayError):
    pass


class StoreClosed(OtpRelayError):
    pass


class EventState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_USE = "in_use"
    CONSUMED = "consumed"


class OtpSinkKind(str, Enum):
    """Strongly typed destinations that may consume a claimed OTP."""

    ANDROID_AUTOFILL = "android_autofill"
    ANDROID_IME = "android_ime"
    WINDOWS_TSF = "windows_tsf"


def _validate_uuid4(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise InvalidOtp(f"invalid {name}")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise InvalidOtp(f"invalid {name}") from None
    if str(parsed) != value or parsed.version != 4:
        raise InvalidOtp(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class OtpClaimContext:
    """Content-free identity of the exact sink authorized to use a claim."""

    sink_kind: OtpSinkKind
    context_token: str

    def __post_init__(self) -> None:
        if not isinstance(self.sink_kind, OtpSinkKind):
            raise InvalidOtp("invalid OTP sink kind")
        _validate_uuid4(self.context_token, "OTP context token")

    def __repr__(self) -> str:
        return f"<OtpClaimContext redacted sink={self.sink_kind.value!r}>"


@dataclass(frozen=True, slots=True, repr=False)
class OtpEventView:
    """Content-free metadata for one current-session relay event."""

    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    state: EventState
    expires_at_monotonic: float

    def __repr__(self) -> str:
        return f"<OtpEventView redacted state={self.state.value!r}>"


@dataclass(frozen=True, slots=True, repr=False)
class OtpClaim:
    """Opaque proof that one target atomically reserved one event."""

    session_epoch: str
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    expires_at_monotonic: float
    context: OtpClaimContext = field(repr=False)
    _claim_token: str = field(repr=False, compare=False)

    def __repr__(self) -> str:
        return "<OtpClaim redacted>"


@dataclass(slots=True, repr=False)
class _StoredEvent:
    event_id: str
    sender_device: str
    target_device: str
    sequence: int
    code: bytearray = field(repr=False)
    expires_at: float
    state: EventState = EventState.PENDING
    claim_token: str | None = field(default=None, repr=False)
    claim_context: OtpClaimContext | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _ReplayMarker:
    event_id: str
    sender_device: str
    nonce_digest: bytes = field(repr=False)
    target_device: str
    sequence: int
    event_expires_at: float
    replay_expires_at: float


@dataclass(frozen=True, slots=True, repr=False)
class _MaintenanceSnapshot:
    """One-clock store maintenance result for coordinator reconciliation."""

    expired_count: int
    live_views: tuple[OtpEventView, ...]
    next_deadline_monotonic: float | None


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def _new_claim_token() -> str:
    return secrets.token_urlsafe(32)


class OtpRelayStore:
    """Thread-safe, in-memory state machine for one authenticated session.

    ``session_epoch`` and the authenticated sender sequence are part of every
    replay identity and claim.  Per-sender sequence high-water marks remain
    bounded and monotonic for this store's lifetime; transport authentication
    and replay protection across process restarts remain protocol-layer
    responsibilities.

    The host must schedule ``expire`` for ``next_deadline_monotonic``.  Every
    public operation also checks the injected monotonic clock, and
    ``use_secret`` refuses an event whose deadline passed even if the host did
    not run the scheduled cleanup.
    """

    def __init__(
        self,
        *,
        session_epoch: str,
        capacity: int = 32,
        replay_capacity: int = 256,
        sender_capacity: int | None = None,
        per_target_capacity: int | None = None,
        per_target_replay_capacity: int | None = None,
        max_ttl_seconds: float = 180.0,
        replay_window_seconds: float = 600.0,
        max_code_bytes: int = 32,
        clock: Callable[[], float] = time.monotonic,
        claim_token_factory: Callable[[], str] = _new_claim_token,
    ):
        self._session_epoch = self._validate_uuid_token(
            session_epoch,
            "session epoch",
        )
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(replay_capacity, bool)
            or not isinstance(replay_capacity, int)
            or replay_capacity < capacity
        ):
            raise ValueError("replay_capacity must be an integer at least capacity")
        if per_target_capacity is None:
            per_target_capacity = min(8, capacity)
        if (
            isinstance(per_target_capacity, bool)
            or not isinstance(per_target_capacity, int)
            or not 0 < per_target_capacity <= capacity
        ):
            raise ValueError("per_target_capacity must be between 1 and capacity")
        if per_target_replay_capacity is None:
            per_target_replay_capacity = min(64, replay_capacity)
        if (
            isinstance(per_target_replay_capacity, bool)
            or not isinstance(per_target_replay_capacity, int)
            or not per_target_capacity <= per_target_replay_capacity <= replay_capacity
        ):
            raise ValueError(
                "per_target_replay_capacity must be between the per-target active "
                "capacity and replay_capacity"
            )
        if sender_capacity is None:
            sender_capacity = min(32, replay_capacity)
        if (
            isinstance(sender_capacity, bool)
            or not isinstance(sender_capacity, int)
            or not 0 < sender_capacity <= replay_capacity
        ):
            raise ValueError("sender_capacity must be between 1 and replay_capacity")
        if (
            isinstance(max_code_bytes, bool)
            or not isinstance(max_code_bytes, int)
            or max_code_bytes <= 0
        ):
            raise ValueError("max_code_bytes must be a positive integer")
        self._validate_duration(max_ttl_seconds, "max_ttl_seconds")
        self._validate_duration(replay_window_seconds, "replay_window_seconds")
        if replay_window_seconds < max_ttl_seconds:
            raise ValueError("replay window must cover the maximum TTL")

        self._capacity = capacity
        self._replay_capacity = replay_capacity
        self._sender_capacity = sender_capacity
        self._per_target_capacity = per_target_capacity
        self._per_target_replay_capacity = per_target_replay_capacity
        self._max_ttl = float(max_ttl_seconds)
        self._replay_window = float(replay_window_seconds)
        self._max_code_bytes = max_code_bytes
        self._clock = clock
        self._claim_token_factory = claim_token_factory
        self._events: dict[str, _StoredEvent] = {}
        self._markers: dict[tuple[str, str, int], _ReplayMarker] = {}
        self._event_index: dict[tuple[str, str], tuple[str, str, int]] = {}
        self._nonce_index: dict[tuple[str, bytes], tuple[str, str, int]] = {}
        self._highest_sequence_by_sender: dict[str, int] = {}
        self._revoked_targets: set[str] = set()
        self._last_now: float | None = None
        self._closed = False
        # The lock is intentionally non-reentrant.  use_secret releases it
        # before invoking the sink callback so unrelated lifecycle work can run.
        self._lock = threading.Lock()

    @property
    def session_epoch(self) -> str:
        return self._session_epoch

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @staticmethod
    def _validate_duration(value: float, name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a positive finite number")
        if not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError(f"{name} must be a positive finite number")

    @staticmethod
    def _validate_uuid_token(value: str, name: str) -> str:
        return _validate_uuid4(value, name)

    @staticmethod
    def _validate_device(value: str, name: str) -> str:
        if not isinstance(value, str) or not value.startswith("device:"):
            raise InvalidOtp(f"invalid {name} device")
        _validate_uuid4(value[len("device:") :], f"{name} device")
        return value

    @staticmethod
    def _validate_target(value: str) -> str:
        return OtpRelayStore._validate_device(value, "target")

    @staticmethod
    def _validate_sender(value: str) -> str:
        return OtpRelayStore._validate_device(value, "sender")

    @staticmethod
    def _validate_claim_context(value: OtpClaimContext) -> OtpClaimContext:
        if not isinstance(value, OtpClaimContext):
            raise InvalidOtp("invalid OTP claim context")
        return value

    @staticmethod
    def _validate_sequence(value: int) -> int:
        if type(value) is not int or not 0 < value <= (2**63 - 1):
            raise InvalidOtp("invalid sequence")
        return value

    @staticmethod
    def _validate_deadline(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidOtp("invalid authenticated expiry")
        deadline = float(value)
        if not math.isfinite(deadline):
            raise InvalidOtp("invalid authenticated expiry")
        return deadline

    @staticmethod
    def _nonce_digest(nonce: bytes | bytearray | memoryview) -> bytes:
        if not isinstance(nonce, (bytes, bytearray, memoryview)):
            raise InvalidOtp("invalid nonce")
        if not 16 <= len(nonce) <= 64:
            raise InvalidOtp("invalid nonce")
        return hashlib.sha256(nonce).digest()

    def _require_open_locked(self) -> None:
        if self._closed:
            raise StoreClosed("OTP relay store is closed")

    def _clear_active_locked(self, target_device: str | None = None) -> int:
        event_ids = [
            event_id
            for event_id, event in self._events.items()
            if target_device is None or event.target_device == target_device
        ]
        for event_id in event_ids:
            self._terminate_locked(event_id)
        return len(event_ids)

    def _poison_locked(self) -> None:
        self._clear_active_locked()
        self._markers.clear()
        self._event_index.clear()
        self._nonce_index.clear()
        self._highest_sequence_by_sender.clear()
        self._revoked_targets.clear()
        self._closed = True

    def _now_locked(self) -> float:
        self._require_open_locked()
        try:
            now = float(self._clock())
        except Exception:
            self._poison_locked()
            raise StoreClosed("OTP relay clock failed; store closed") from None
        if not math.isfinite(now):
            self._poison_locked()
            raise StoreClosed("OTP relay clock failed; store closed")
        if self._last_now is not None and now < self._last_now:
            self._poison_locked()
            raise StoreClosed("OTP relay clock regressed; store closed")
        self._last_now = now
        return now

    def _terminate_locked(self, event_id: str) -> None:
        event = self._events.pop(event_id, None)
        if event is not None:
            _wipe(event.code)
            event.claim_token = None
            event.claim_context = None

    def _sweep_locked(self, now: float) -> int:
        expired = [
            event_id
            for event_id, event in self._events.items()
            if now >= event.expires_at
        ]
        for event_id in expired:
            self._terminate_locked(event_id)

        stale_keys = [
            replay_key
            for replay_key, marker in self._markers.items()
            if now >= marker.replay_expires_at
        ]
        for replay_key in stale_keys:
            marker = self._markers.pop(replay_key)
            event_key = (self._session_epoch, marker.event_id)
            if self._event_index.get(event_key) == replay_key:
                del self._event_index[event_key]
            nonce_key = (self._session_epoch, marker.nonce_digest)
            if self._nonce_index.get(nonce_key) == replay_key:
                del self._nonce_index[nonce_key]
        return len(expired)

    def _event_for_claim_locked(
        self,
        claim: OtpClaim,
        allowed_states: tuple[EventState, ...],
    ) -> _StoredEvent:
        if not isinstance(claim, OtpClaim):
            raise InvalidOtp("invalid claim")
        if claim.session_epoch != self._session_epoch:
            raise SessionMismatch("OTP claim belongs to another session")
        event = self._events.get(claim.event_id)
        if event is None:
            raise OtpNotFound("OTP event is unavailable")
        if event.sender_device != claim.sender_device:
            raise SenderMismatch("OTP sender mismatch")
        if event.target_device != claim.target_device:
            raise TargetMismatch("OTP target mismatch")
        if (
            event.sequence != claim.sequence
            or event.expires_at != claim.expires_at_monotonic
        ):
            raise InvalidTransition("OTP claim identity mismatch")
        token = event.claim_token
        if token is None or not secrets.compare_digest(token, claim._claim_token):
            raise InvalidTransition("OTP claim is no longer valid")
        if event.claim_context != claim.context:
            raise ClaimContextMismatch("OTP claim context mismatch")
        if event.state not in allowed_states:
            raise InvalidTransition("OTP transition is not allowed")
        return event

    def add(
        self,
        *,
        authenticated_session_epoch: str,
        authenticated_sender_device: str,
        authenticated_sequence: int,
        authenticated_expires_at_monotonic: float,
        event_id: str,
        target_device: str,
        nonce: bytes | bytearray | memoryview,
        code: bytearray,
    ) -> OtpEventView:
        """Admit one authenticated envelope and always clear its owned buffer."""

        if type(code) is not bytearray:
            raise InvalidOtp("OTP code must be an owned bytearray")
        try:
            if not 0 < len(code) <= self._max_code_bytes:
                raise InvalidOtp("invalid OTP buffer")
            authenticated_session_epoch = self._validate_uuid_token(
                authenticated_session_epoch,
                "authenticated session epoch",
            )
            if not secrets.compare_digest(
                authenticated_session_epoch,
                self._session_epoch,
            ):
                raise SessionMismatch("OTP event belongs to another session")
            authenticated_sender_device = self._validate_sender(
                authenticated_sender_device
            )
            authenticated_sequence = self._validate_sequence(authenticated_sequence)
            authenticated_expires_at_monotonic = self._validate_deadline(
                authenticated_expires_at_monotonic
            )
            event_id = self._validate_uuid_token(event_id, "event id")
            target_device = self._validate_target(target_device)
            nonce_digest = self._nonce_digest(nonce)

            with self._lock:
                now = self._now_locked()
                self._sweep_locked(now)
                if authenticated_expires_at_monotonic <= now:
                    raise InvalidOtp("OTP envelope expired")
                if authenticated_expires_at_monotonic - now > self._max_ttl:
                    raise InvalidOtp("OTP authenticated expiry exceeds maximum")

                previous_sequence = self._highest_sequence_by_sender.get(
                    authenticated_sender_device
                )
                if (
                    previous_sequence is not None
                    and authenticated_sequence <= previous_sequence
                ):
                    raise ReplayRejected("OTP replay rejected")
                if (
                    previous_sequence is None
                    and len(self._highest_sequence_by_sender) >= self._sender_capacity
                ):
                    raise CapacityExceeded("OTP sender capacity reached")

                replay_key = (
                    self._session_epoch,
                    authenticated_sender_device,
                    authenticated_sequence,
                )
                event_key = (self._session_epoch, event_id)
                nonce_key = (self._session_epoch, nonce_digest)
                if (
                    event_id in self._events
                    or replay_key in self._markers
                    or event_key in self._event_index
                    or nonce_key in self._nonce_index
                ):
                    raise ReplayRejected("OTP replay rejected")
                if target_device in self._revoked_targets:
                    raise TargetRevoked("OTP target is revoked")
                target_event_count = sum(
                    event.target_device == target_device
                    for event in self._events.values()
                )
                if target_event_count >= self._per_target_capacity:
                    raise CapacityExceeded("target OTP capacity reached")
                target_marker_count = sum(
                    marker.target_device == target_device
                    for marker in self._markers.values()
                )
                if target_marker_count >= self._per_target_replay_capacity:
                    raise CapacityExceeded("target OTP replay capacity reached")
                if len(self._events) >= self._capacity:
                    raise CapacityExceeded("active OTP capacity reached")
                if len(self._markers) >= self._replay_capacity:
                    raise CapacityExceeded("OTP replay capacity reached")

                expires_at = authenticated_expires_at_monotonic
                replay_expires_at = max(expires_at, now + self._replay_window)
                if not math.isfinite(replay_expires_at):
                    self._poison_locked()
                    raise StoreClosed("OTP relay clock range failed; store closed")

                stored_code: bytearray | None = None
                stored: _StoredEvent | None = None
                marker: _ReplayMarker | None = None
                view: OtpEventView | None = None
                try:
                    stored_code = bytearray(code)
                    stored = _StoredEvent(
                        event_id=event_id,
                        sender_device=authenticated_sender_device,
                        target_device=target_device,
                        sequence=authenticated_sequence,
                        code=stored_code,
                        expires_at=expires_at,
                    )
                    marker = _ReplayMarker(
                        event_id=event_id,
                        sender_device=authenticated_sender_device,
                        nonce_digest=nonce_digest,
                        target_device=target_device,
                        sequence=authenticated_sequence,
                        event_expires_at=expires_at,
                        replay_expires_at=replay_expires_at,
                    )
                    view = OtpEventView(
                        session_epoch=self._session_epoch,
                        event_id=event_id,
                        sender_device=authenticated_sender_device,
                        target_device=target_device,
                        sequence=authenticated_sequence,
                        state=EventState.PENDING,
                        expires_at_monotonic=expires_at,
                    )
                    self._events[event_id] = stored
                    self._markers[replay_key] = marker
                    self._event_index[event_key] = replay_key
                    self._nonce_index[nonce_key] = replay_key
                    self._highest_sequence_by_sender[
                        authenticated_sender_device
                    ] = authenticated_sequence
                except BaseException:
                    if self._events.get(event_id) is stored:
                        self._events.pop(event_id, None)
                    if self._markers.get(replay_key) is marker:
                        self._markers.pop(replay_key, None)
                    if self._event_index.get(event_key) == replay_key:
                        del self._event_index[event_key]
                    if self._nonce_index.get(nonce_key) == replay_key:
                        del self._nonce_index[nonce_key]
                    if previous_sequence is None:
                        if (
                            self._highest_sequence_by_sender.get(
                                authenticated_sender_device
                            )
                            == authenticated_sequence
                        ):
                            del self._highest_sequence_by_sender[
                                authenticated_sender_device
                            ]
                    elif (
                        self._highest_sequence_by_sender.get(
                            authenticated_sender_device
                        )
                        == authenticated_sequence
                    ):
                        self._highest_sequence_by_sender[
                            authenticated_sender_device
                        ] = previous_sequence
                    if stored_code is not None:
                        _wipe(stored_code)
                    raise
                assert view is not None
                return view
        finally:
            _wipe(code)

    def claim(
        self,
        *,
        authenticated_sender_device: str,
        authenticated_sequence: int,
        authenticated_expires_at_monotonic: float,
        event_id: str,
        target_device: str,
        claim_context: OtpClaimContext,
        nonce: bytes | bytearray | memoryview,
    ) -> OtpClaim:
        """Atomically reserve a pending event for its bound target."""

        authenticated_sender_device = self._validate_sender(
            authenticated_sender_device
        )
        authenticated_sequence = self._validate_sequence(authenticated_sequence)
        authenticated_expires_at_monotonic = self._validate_deadline(
            authenticated_expires_at_monotonic
        )
        event_id = self._validate_uuid_token(event_id, "event id")
        target_device = self._validate_target(target_device)
        claim_context = self._validate_claim_context(claim_context)
        nonce_digest = self._nonce_digest(nonce)
        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            event = self._events.get(event_id)
            if event is None:
                raise OtpNotFound("OTP event is unavailable")
            if event.sender_device != authenticated_sender_device:
                raise SenderMismatch("OTP sender mismatch")
            if event.target_device != target_device:
                raise TargetMismatch("OTP target mismatch")
            if (
                event.sequence != authenticated_sequence
                or event.expires_at != authenticated_expires_at_monotonic
            ):
                raise ReplayRejected("OTP replay rejected")
            replay_key = (
                self._session_epoch,
                authenticated_sender_device,
                authenticated_sequence,
            )
            marker = self._markers.get(replay_key)
            if (
                marker is None
                or marker.event_id != event_id
                or marker.sender_device != authenticated_sender_device
                or marker.target_device != target_device
                or marker.sequence != authenticated_sequence
                or marker.event_expires_at != authenticated_expires_at_monotonic
                or not secrets.compare_digest(marker.nonce_digest, nonce_digest)
            ):
                raise ReplayRejected("OTP replay rejected")
            if event.state is not EventState.PENDING:
                raise InvalidTransition("OTP transition is not allowed")
            claim_token = self._claim_token_factory()
            if (
                not isinstance(claim_token, str)
                or not claim_token
                or len(claim_token) > 256
                or not claim_token.isascii()
            ):
                raise RuntimeError("claim token factory returned an invalid token")
            event.state = EventState.CLAIMED
            event.claim_token = claim_token
            event.claim_context = claim_context
            return OtpClaim(
                session_epoch=self._session_epoch,
                event_id=event.event_id,
                sender_device=event.sender_device,
                target_device=event.target_device,
                sequence=event.sequence,
                expires_at_monotonic=event.expires_at,
                context=claim_context,
                _claim_token=claim_token,
            )

    def use_secret(
        self,
        claim: OtpClaim,
        current_context: OtpClaimContext,
        callback: Callable[[memoryview], object],
    ) -> None:
        """Use one short-lived lease without holding the store's global lock.

        The callback receives a temporary read-only view and its return value is
        ignored. Ordinary callback exceptions are replaced with ``OtpUseFailed``
        after the lease is released and wiped. A callback must still be trusted
        and bounded because Python cannot forcibly terminate a blocked sink.
        """

        current_context = self._validate_claim_context(current_context)
        if not callable(callback):
            raise InvalidOtp("invalid OTP callback")
        lease: bytearray | None = None
        temporary_view: memoryview | None = None
        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            event = self._event_for_claim_locked(claim, (EventState.CLAIMED,))
            if current_context != claim.context:
                raise ClaimContextMismatch("OTP claim context mismatch")
            try:
                lease = bytearray(event.code)
                temporary_view = memoryview(lease).toreadonly()
                _wipe(event.code)
                event.state = EventState.IN_USE
            except BaseException:
                if temporary_view is not None:
                    temporary_view.release()
                if lease is not None:
                    _wipe(lease)
                self._terminate_locked(event.event_id)
                raise

        callback_failed = False
        try:
            callback(temporary_view)
        except Exception:
            callback_failed = True
        finally:
            temporary_view.release()
            _wipe(lease)
            with self._lock:
                current = self._events.get(claim.event_id)
                if (
                    current is not None
                    and current.state is EventState.IN_USE
                    and current.target_device == claim.target_device
                    and current.claim_token is not None
                    and secrets.compare_digest(
                        current.claim_token,
                        claim._claim_token,
                    )
                ):
                    current.state = EventState.CONSUMED

        if callback_failed:
            raise OtpUseFailed("OTP sink failed") from None

    def ack(self, claim: OtpClaim) -> None:
        """Acknowledge one consumed event and remove its metadata."""

        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            event = self._event_for_claim_locked(claim, (EventState.CONSUMED,))
            self._terminate_locked(event.event_id)

    def dismiss(self, claim: OtpClaim) -> None:
        """Atomically terminate a claimed, in-use, or consumed event."""

        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            event = self._event_for_claim_locked(
                claim,
                (EventState.CLAIMED, EventState.IN_USE, EventState.CONSUMED),
            )
            self._terminate_locked(event.event_id)

    def expire(self) -> int:
        """Wipe all events whose monotonic deadline has elapsed."""

        with self._lock:
            now = self._now_locked()
            return self._sweep_locked(now)

    def next_deadline_monotonic(self) -> float | None:
        """Return the next cleanup deadline the host must schedule."""

        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            if not self._events:
                return None
            return min(event.expires_at for event in self._events.values())

    def maintenance_snapshot(self, *, target_device: str) -> _MaintenanceSnapshot:
        """Sweep and snapshot live metadata from one clock sample and lock hold."""

        target_device = self._validate_target(target_device)
        with self._lock:
            now = self._now_locked()
            expired_count = self._sweep_locked(now)
            views = tuple(
                OtpEventView(
                    session_epoch=self._session_epoch,
                    event_id=event.event_id,
                    sender_device=event.sender_device,
                    target_device=event.target_device,
                    sequence=event.sequence,
                    state=event.state,
                    expires_at_monotonic=event.expires_at,
                )
                for event in self._events.values()
                if event.target_device == target_device
            )
            deadline = (
                min(event.expires_at for event in self._events.values())
                if self._events
                else None
            )
            return _MaintenanceSnapshot(
                expired_count=expired_count,
                live_views=views,
                next_deadline_monotonic=deadline,
            )

    def revoke_target(self, target_device: str) -> int:
        """Revoke one target for this session and wipe all of its events."""

        target_device = self._validate_target(target_device)
        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            if (
                target_device not in self._revoked_targets
                and len(self._revoked_targets) >= self._replay_capacity
            ):
                self._poison_locked()
                raise StoreClosed("OTP revocation capacity reached; store closed")
            self._revoked_targets.add(target_device)
            return self._clear_active_locked(target_device)

    def clear_all(self) -> int:
        """Wipe active events while retaining current-session replay markers."""

        with self._lock:
            return self._clear_active_locked()

    def close(self) -> None:
        """Wipe all state and permanently close this store instance."""

        with self._lock:
            self._poison_locked()

    def snapshot(self, *, target_device: str) -> tuple[OtpEventView, ...]:
        """Return only content-free metadata for one target device."""

        target_device = self._validate_target(target_device)
        with self._lock:
            now = self._now_locked()
            self._sweep_locked(now)
            return tuple(
                OtpEventView(
                    session_epoch=self._session_epoch,
                    event_id=event.event_id,
                    sender_device=event.sender_device,
                    target_device=event.target_device,
                    sequence=event.sequence,
                    state=event.state,
                    expires_at_monotonic=event.expires_at,
                )
                for event in self._events.values()
                if event.target_device == target_device
            )
