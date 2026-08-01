"""Process-local orchestration for admitted OTP relay credentials.

This module never captures platform data or performs transport itself.  It
accepts explicit synthetic candidates or candidates already authenticated by
the isolated OTP channel, normalizes only an isolated numeric code, and owns
their in-memory lifecycle until a bound sink consumes them.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .relay import (
    InvalidOtp,
    OtpClaimContext,
    OtpClaim,
    OtpEventView,
    OtpNotFound,
    OtpRelayError,
    OtpRelayStore,
    OtpUseFailed,
    ReplayRejected,
    StoreClosed,
    TargetMismatch,
)


class CaptureRejected(OtpRelayError):
    """The supplied input did not meet the explicit local capture policy."""


class PairingRequired(OtpRelayError):
    """A cross-device request did not have an authenticated pairing."""


class E2eeRequired(OtpRelayError):
    """A paired cross-device request did not have authenticated E2EE."""


class TransportUnavailable(OtpRelayError):
    """No cross-device OTP transport exists in this local-only slice."""


@dataclass(frozen=True, slots=True)
class CrossDeviceSecurity:
    """Non-secret capability facts used only to produce fail-closed errors."""

    paired: bool = False
    e2ee_ready: bool = False

    def __post_init__(self) -> None:
        if type(self.paired) is not bool or type(self.e2ee_ready) is not bool:
            raise ValueError("cross-device security flags must be booleans")


@dataclass(frozen=True, slots=True)
class OtpCapturePolicy:
    """Strict policy for an already isolated local synthetic OTP candidate.

    The coordinator does not scan notification, clipboard, message, or typed
    text bodies.  Its caller must explicitly supply only the candidate itself
    in an owned ``bytearray``.  ASCII space and hyphen are accepted solely as
    grouping separators and are removed before the credential enters storage.
    """

    min_digits: int = 4
    max_digits: int = 8
    max_candidate_bytes: int = 24

    def __post_init__(self) -> None:
        if (
            type(self.min_digits) is not int
            or type(self.max_digits) is not int
            or type(self.max_candidate_bytes) is not int
            or self.min_digits <= 0
            or self.max_digits < self.min_digits
            or self.max_candidate_bytes < self.max_digits
        ):
            raise ValueError("invalid OTP capture policy")

    def normalize(self, candidate: bytearray) -> bytearray:
        """Return a new normalized buffer without retaining the candidate."""

        if type(candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")
        if not 0 < len(candidate) <= self.max_candidate_bytes:
            raise CaptureRejected("OTP candidate rejected")

        normalized = bytearray()
        previous_was_separator = False
        try:
            for value in candidate:
                if 48 <= value <= 57:
                    normalized.append(value)
                    previous_was_separator = False
                elif value in (32, 45):
                    if not normalized or previous_was_separator:
                        raise CaptureRejected("OTP candidate rejected")
                    previous_was_separator = True
                else:
                    raise CaptureRejected("OTP candidate rejected")
            if previous_was_separator or not (
                self.min_digits <= len(normalized) <= self.max_digits
            ):
                raise CaptureRejected("OTP candidate rejected")
            return normalized
        except BaseException:
            _wipe(normalized)
            raise


@dataclass(slots=True, repr=False)
class _PendingIdentity:
    sender_device: str
    target_device: str
    sequence: int
    expires_at_monotonic: float
    nonce: bytearray = field(repr=False)


class _ClockFailure(Exception):
    pass


class _CheckedClock:
    """Serialize and reject failures/regression before consumers use time."""

    def __init__(self, source: Callable[[], float]):
        if not callable(source):
            raise ValueError("clock must be callable")
        self._source = source
        self._last: float | None = None
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            try:
                now = float(self._source())
            except Exception:
                raise _ClockFailure from None
            if not math.isfinite(now):
                raise _ClockFailure
            if self._last is not None and now < self._last:
                raise _ClockFailure
            self._last = now
        return now


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _new_nonce() -> bytearray:
    return bytearray(secrets.token_bytes(32))


class OtpRelayCoordinator:
    """Executable local/synthetic OTP relay slice with fail-closed cleanup.

    The coordinator owns one process-local store and the raw nonces needed to
    claim still-pending synthetic events.  Those nonces are wiped immediately
    after a successful claim, expiry, clear, revocation, or close.  OTP bytes
    remain owned by ``OtpRelayStore`` and are exposed only to one synchronous
    callback through ``use_and_ack``.
    """

    def __init__(
        self,
        *,
        session_epoch: str,
        local_device: str,
        capture_policy: OtpCapturePolicy | None = None,
        default_ttl_seconds: float = 30.0,
        max_ttl_seconds: float = 120.0,
        replay_window_seconds: float = 600.0,
        capacity: int = 32,
        replay_capacity: int = 256,
        clock: Callable[[], float] = time.monotonic,
        event_id_factory: Callable[[], str] = _new_event_id,
        nonce_factory: Callable[[], bytearray] = _new_nonce,
        claim_token_factory: Callable[[], str] | None = None,
    ):
        policy = capture_policy if capture_policy is not None else OtpCapturePolicy()
        if not isinstance(policy, OtpCapturePolicy):
            raise ValueError("capture_policy must be an OtpCapturePolicy")
        local_device = OtpRelayStore._validate_target(local_device)
        OtpRelayStore._validate_sender(local_device)
        if not callable(event_id_factory) or not callable(nonce_factory):
            raise ValueError("identity factories must be callable")

        max_ttl = self._validate_duration(max_ttl_seconds, "max_ttl_seconds")
        default_ttl = self._validate_duration(
            default_ttl_seconds,
            "default_ttl_seconds",
        )
        if default_ttl > max_ttl:
            raise ValueError("default TTL must not exceed maximum TTL")

        checked_clock = _CheckedClock(clock)
        store_options: dict[str, object] = {
            "session_epoch": session_epoch,
            "capacity": capacity,
            "replay_capacity": replay_capacity,
            "max_ttl_seconds": max_ttl,
            "replay_window_seconds": replay_window_seconds,
            "max_code_bytes": policy.max_digits,
            "clock": checked_clock,
        }
        if claim_token_factory is not None:
            store_options["claim_token_factory"] = claim_token_factory

        self._local_device = local_device
        self._capture_policy = policy
        self._default_ttl = default_ttl
        self._max_ttl = max_ttl
        self._clock = checked_clock
        self._event_id_factory = event_id_factory
        self._nonce_factory = nonce_factory
        self._store = OtpRelayStore(**store_options)
        self._pending: dict[str, _PendingIdentity] = {}
        self._next_sequence = 1
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "<OtpRelayCoordinator redacted>"

    @property
    def session_epoch(self) -> str:
        return self._store.session_epoch

    @property
    def local_device(self) -> str:
        return self._local_device

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed or self._store.closed

    @staticmethod
    def _validate_duration(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a positive finite number")
        duration = float(value)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"{name} must be a positive finite number")
        return duration

    def _require_open_locked(self) -> None:
        if self._closed:
            raise StoreClosed("OTP relay coordinator is closed")

    def _wipe_pending_locked(self) -> None:
        pending = tuple(self._pending.values())
        self._pending.clear()
        for identity in pending:
            _wipe(identity.nonce)

    def _store_closed_locked(self) -> None:
        self._wipe_pending_locked()
        self._closed = True

    def _close_locked(self) -> None:
        try:
            self._store.close()
        finally:
            self._store_closed_locked()

    def _read_clock_locked(self) -> float:
        try:
            return self._clock()
        except Exception:
            self._close_locked()
            raise StoreClosed("OTP relay clock failed; coordinator closed") from None

    def _prune_expired_pending_locked(self, now: float) -> None:
        expired_ids = [
            event_id
            for event_id, identity in self._pending.items()
            if now >= identity.expires_at_monotonic
        ]
        for event_id in expired_ids:
            identity = self._pending.pop(event_id)
            _wipe(identity.nonce)

    def _reject_cross_device(
        self,
        security: CrossDeviceSecurity | None,
    ) -> None:
        if security is None:
            security = CrossDeviceSecurity()
        if not isinstance(security, CrossDeviceSecurity):
            raise InvalidOtp("invalid cross-device security context")
        if not security.paired:
            raise PairingRequired("authenticated pairing required")
        if not security.e2ee_ready:
            raise E2eeRequired("authenticated E2EE required")
        raise TransportUnavailable("cross-device OTP transport is unavailable")

    def capture_synthetic(
        self,
        candidate: bytearray,
        *,
        target_device: str,
        explicit_user_action: bool,
        ttl_seconds: float | None = None,
        cross_device_security: CrossDeviceSecurity | None = None,
    ) -> OtpEventView:
        """Capture one explicit isolated candidate for the local device only.

        The caller transfers ownership of ``candidate``.  It is wiped on every
        return and error path, including policy and cross-device rejection.
        """

        if type(candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")

        normalized: bytearray | None = None
        nonce: bytearray | None = None
        nonce_retained = False
        try:
            target_device = OtpRelayStore._validate_target(target_device)
            if explicit_user_action is not True:
                raise CaptureRejected("explicit local OTP capture required")
            if target_device != self._local_device:
                self._reject_cross_device(cross_device_security)

            ttl = (
                self._default_ttl
                if ttl_seconds is None
                else self._validate_duration(ttl_seconds, "ttl_seconds")
            )
            if ttl > self._max_ttl:
                raise CaptureRejected("OTP TTL exceeds capture policy")
            normalized = self._capture_policy.normalize(candidate)

            with self._lock:
                self._require_open_locked()
                now = self._read_clock_locked()
                self._prune_expired_pending_locked(now)
                expires_at = now + ttl
                if not math.isfinite(expires_at):
                    raise CaptureRejected("OTP TTL exceeds clock range")

                event_id = self._store._validate_uuid_token(
                    self._event_id_factory(),
                    "event id",
                )
                nonce = self._nonce_factory()
                if type(nonce) is not bytearray:
                    raise InvalidOtp("nonce factory must return an owned bytearray")
                self._store._nonce_digest(nonce)
                if event_id in self._pending:
                    raise ReplayRejected("OTP replay rejected")
                if self._next_sequence > (2**63 - 1):
                    self._close_locked()
                    raise StoreClosed("OTP sender sequence exhausted; store closed")

                sequence = self._next_sequence
                self._next_sequence += 1
                identity = _PendingIdentity(
                    sender_device=self._local_device,
                    target_device=target_device,
                    sequence=sequence,
                    expires_at_monotonic=expires_at,
                    nonce=nonce,
                )
                self._pending[event_id] = identity
                nonce_retained = True
                try:
                    return self._store.add(
                        authenticated_session_epoch=self.session_epoch,
                        authenticated_sender_device=identity.sender_device,
                        authenticated_sequence=identity.sequence,
                        authenticated_expires_at_monotonic=(
                            identity.expires_at_monotonic
                        ),
                        event_id=event_id,
                        target_device=identity.target_device,
                        nonce=identity.nonce,
                        code=normalized,
                    )
                except StoreClosed:
                    self._store_closed_locked()
                    nonce_retained = False
                    raise
                except BaseException:
                    current = self._pending.get(event_id)
                    if current is identity:
                        del self._pending[event_id]
                    _wipe(identity.nonce)
                    nonce_retained = False
                    raise
        finally:
            _wipe(candidate)
            if normalized is not None:
                _wipe(normalized)
            if nonce is not None and not nonce_retained:
                _wipe(nonce)

    def claim(
        self,
        *,
        event_id: str,
        target_device: str,
        claim_context: OtpClaimContext,
    ) -> OtpClaim:
        """Atomically claim one pending event admitted to this process."""

        event_id = self._store._validate_uuid_token(event_id, "event id")
        target_device = self._store._validate_target(target_device)
        with self._lock:
            self._require_open_locked()
            identity = self._pending.get(event_id)
            if identity is None:
                raise OtpNotFound("OTP event is unavailable")
            try:
                claim = self._store.claim(
                    authenticated_sender_device=identity.sender_device,
                    authenticated_sequence=identity.sequence,
                    authenticated_expires_at_monotonic=(
                        identity.expires_at_monotonic
                    ),
                    event_id=event_id,
                    target_device=target_device,
                    claim_context=claim_context,
                    nonce=identity.nonce,
                )
            except StoreClosed:
                self._store_closed_locked()
                raise
            except OtpNotFound:
                if self._pending.get(event_id) is identity:
                    del self._pending[event_id]
                _wipe(identity.nonce)
                raise
            if self._pending.get(event_id) is identity:
                del self._pending[event_id]
            _wipe(identity.nonce)
            return claim

    def claim_synthetic(
        self,
        *,
        event_id: str,
        target_device: str,
        claim_context: OtpClaimContext,
    ) -> OtpClaim:
        """Compatibility alias for existing local synthetic callers."""

        return self.claim(
            event_id=event_id,
            target_device=target_device,
            claim_context=claim_context,
        )

    def admit_authenticated(
        self,
        candidate: bytearray,
        *,
        authenticated_session_epoch: str,
        authenticated_sender_device: str,
        authenticated_sequence: int,
        authenticated_expires_at_monotonic: float,
        event_id: str,
        target_device: str,
        nonce: bytearray,
    ) -> OtpEventView:
        """Admit one candidate after an authenticated transport opens it.

        ``candidate`` and ``nonce`` are ownership-transfer buffers and are
        wiped on every path.  This boundary deliberately accepts no message
        body, sender label, clipboard value, persistence handle, or outbox
        object.  Authentication must already have been completed by the
        caller's OTP-only channel.
        """

        if type(candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")
        if type(nonce) is not bytearray:
            _wipe(candidate)
            raise InvalidOtp("OTP nonce must be an owned bytearray")

        normalized: bytearray | None = None
        nonce_retained = False
        try:
            target_device = self._store._validate_target(target_device)
            if target_device != self._local_device:
                raise TargetMismatch("OTP target mismatch")
            authenticated_sender_device = self._store._validate_sender(
                authenticated_sender_device
            )
            normalized = self._capture_policy.normalize(candidate)

            with self._lock:
                self._require_open_locked()
                now = self._read_clock_locked()
                self._prune_expired_pending_locked(now)
                identity = _PendingIdentity(
                    sender_device=authenticated_sender_device,
                    target_device=target_device,
                    sequence=authenticated_sequence,
                    expires_at_monotonic=authenticated_expires_at_monotonic,
                    nonce=nonce,
                )
                if event_id in self._pending:
                    raise ReplayRejected("OTP replay rejected")
                self._pending[event_id] = identity
                nonce_retained = True
                try:
                    view = self._store.add(
                        authenticated_session_epoch=authenticated_session_epoch,
                        authenticated_sender_device=authenticated_sender_device,
                        authenticated_sequence=authenticated_sequence,
                        authenticated_expires_at_monotonic=(
                            authenticated_expires_at_monotonic
                        ),
                        event_id=event_id,
                        target_device=target_device,
                        nonce=nonce,
                        code=normalized,
                    )
                except StoreClosed:
                    self._store_closed_locked()
                    nonce_retained = False
                    raise
                except BaseException:
                    if self._pending.get(event_id) is identity:
                        del self._pending[event_id]
                    _wipe(identity.nonce)
                    nonce_retained = False
                    raise
                return view
        finally:
            _wipe(candidate)
            if normalized is not None:
                _wipe(normalized)
            if not nonce_retained:
                _wipe(nonce)

    def _dismiss_after_active_failure(self, claim: OtpClaim) -> None:
        try:
            self._store.dismiss(claim)
        except StoreClosed:
            with self._lock:
                self._store_closed_locked()
        except OtpRelayError:
            pass

    def use_and_ack(
        self,
        claim: OtpClaim,
        current_context: OtpClaimContext,
        callback: Callable[[memoryview], object],
    ) -> None:
        """Use once, ACK on success, and destroy after an active-use failure."""

        with self._lock:
            self._require_open_locked()
        try:
            self._store.use_secret(claim, current_context, callback)
        except OtpUseFailed:
            self._dismiss_after_active_failure(claim)
            raise
        except StoreClosed:
            with self._lock:
                self._store_closed_locked()
            raise
        except OtpRelayError:
            # Validation and competing-use failures did not acquire a lease.
            raise
        except BaseException:
            # A callback BaseException still leaves the store in CONSUMED (or
            # already terminal) state after its lease-finally path.
            self._dismiss_after_active_failure(claim)
            raise

        try:
            self._store.ack(claim)
        except OtpNotFound:
            # Concurrent expiry/revocation already achieved terminal destroy.
            return
        except StoreClosed:
            with self._lock:
                self._store_closed_locked()
            # The sink already completed successfully. Concurrent shutdown is
            # a terminal destroy, so reporting failure here could provoke an
            # unsafe duplicate use attempt by the caller.
            return
        except BaseException:
            self._dismiss_after_active_failure(claim)
            raise

    def dismiss(self, claim: OtpClaim) -> None:
        """Destroy one claimed event without using it."""

        try:
            self._store.dismiss(claim)
        except StoreClosed:
            with self._lock:
                self._store_closed_locked()
            raise

    def snapshot(self) -> tuple[OtpEventView, ...]:
        """Return redacted-repr metadata for the configured local target."""

        with self._lock:
            self._require_open_locked()
            maintenance = self._maintain_locked()
            return maintenance.live_views

    def _maintain_locked(self):
        """Reconcile store and coordinator ownership before returning."""

        try:
            maintenance = self._store.maintenance_snapshot(
                target_device=self._local_device
            )
        except StoreClosed:
            self._store_closed_locked()
            raise
        live_ids = {view.event_id for view in maintenance.live_views}
        stale_ids = [event_id for event_id in self._pending if event_id not in live_ids]
        for event_id in stale_ids:
            identity = self._pending.pop(event_id)
            _wipe(identity.nonce)
        return maintenance

    def expire(self) -> int:
        """Run scheduled expiry and wipe matching pending claim nonces."""

        with self._lock:
            self._require_open_locked()
            return self._maintain_locked().expired_count

    def next_deadline_monotonic(self) -> float | None:
        """Return the host cleanup deadline for the local synthetic slice."""

        with self._lock:
            self._require_open_locked()
            return self._maintain_locked().next_deadline_monotonic

    def revoke_target(self, target_device: str) -> int:
        """Revoke the configured local target and destroy all pending events."""

        target_device = self._store._validate_target(target_device)
        if target_device != self._local_device:
            raise TargetMismatch("OTP target mismatch")
        with self._lock:
            self._require_open_locked()
            try:
                removed = self._store.revoke_target(target_device)
            except StoreClosed:
                self._store_closed_locked()
                raise
            self._wipe_pending_locked()
            return removed

    def clear_all(self) -> int:
        """Destroy local synthetic events while retaining replay state."""

        with self._lock:
            removed = self._store.clear_all()
            self._wipe_pending_locked()
            return removed

    def close(self) -> None:
        """Wipe every in-memory relay value and permanently close this slice."""

        with self._lock:
            self._close_locked()
