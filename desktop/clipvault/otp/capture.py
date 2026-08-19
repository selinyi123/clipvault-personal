"""Restricted capture ports for isolated OTP candidates.

The relay core never scans SMS, notifications, clipboard data, or typed text.
Platform code may implement :class:`OtpCapturePort` only after the operating
system has produced an isolated candidate and the user has granted the exact
capture capability.  This repository currently ships only the synthetic
adapter used by deterministic integration tests.
"""

from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from .relay import InvalidOtp, OtpRelayError, OtpRelayStore


class CaptureAuthorizationRejected(OtpRelayError):
    """The capture grant does not authorize this one candidate transfer."""


class CaptureSource(str, Enum):
    SYNTHETIC = "synthetic"
    ANDROID_SMS_CODE_AUTOFILL = "android_sms_code_autofill"
    ANDROID_SMS_PERMISSION = "android_sms_permission"


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


def _uuid4(value: str, name: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, AttributeError, ValueError):
        raise InvalidOtp(f"invalid {name}") from None
    if str(parsed) != value or parsed.version != 4:
        raise InvalidOtp(f"invalid {name}")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class OtpCaptureAuthorization:
    """Content-free proof of one user-authorized capture capability.

    A platform adapter is responsible for creating this value only after the
    corresponding OS grant is active.  Synthetic capture always additionally
    requires ``explicit_user_action``; Android sources may be automatic only
    when ``automatic_capture`` was explicitly enabled for that grant.
    """

    grant_id: str
    source: CaptureSource
    session_epoch: str
    sender_device: str
    target_device: str
    expires_at_monotonic: float
    platform_granted: bool
    automatic_capture: bool = False

    def __post_init__(self) -> None:
        _uuid4(self.grant_id, "capture grant")
        if not isinstance(self.source, CaptureSource):
            raise InvalidOtp("invalid capture source")
        _uuid4(self.session_epoch, "capture session")
        OtpRelayStore._validate_sender(self.sender_device)
        OtpRelayStore._validate_target(self.target_device)
        if (
            isinstance(self.expires_at_monotonic, bool)
            or not isinstance(self.expires_at_monotonic, (int, float))
            or not math.isfinite(float(self.expires_at_monotonic))
        ):
            raise InvalidOtp("invalid capture grant expiry")
        if type(self.platform_granted) is not bool:
            raise InvalidOtp("invalid platform grant state")
        if type(self.automatic_capture) is not bool:
            raise InvalidOtp("invalid automatic capture state")
        if self.source is CaptureSource.SYNTHETIC and self.automatic_capture:
            raise CaptureAuthorizationRejected(
                "synthetic capture cannot be automatically authorized"
            )

    def __repr__(self) -> str:
        return f"<OtpCaptureAuthorization redacted source={self.source.value!r}>"


@dataclass(slots=True, repr=False)
class IsolatedOtpCandidate:
    """One ownership-transfer buffer emitted by a restricted adapter."""

    source: CaptureSource
    grant_id: str
    target_device: str
    _candidate: bytearray = field(repr=False)
    _taken: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, CaptureSource):
            raise InvalidOtp("invalid capture source")
        _uuid4(self.grant_id, "capture grant")
        OtpRelayStore._validate_target(self.target_device)
        if type(self._candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")

    def take(
        self,
        authorization: OtpCaptureAuthorization,
        *,
        now_monotonic: float,
        explicit_user_action: bool,
    ) -> bytearray:
        """Transfer the candidate exactly once after validating the grant."""

        with self._lock:
            if self._taken:
                raise CaptureAuthorizationRejected("OTP candidate is unavailable")
            self._taken = True
            candidate = self._candidate
            self._candidate = bytearray()

        try:
            if not isinstance(authorization, OtpCaptureAuthorization):
                raise CaptureAuthorizationRejected("OTP capture grant required")
            if (
                authorization.source is not self.source
                or authorization.grant_id != self.grant_id
                or authorization.target_device != self.target_device
            ):
                raise CaptureAuthorizationRejected("OTP capture grant mismatch")
            if authorization.platform_granted is not True:
                raise CaptureAuthorizationRejected("OTP platform grant required")
            if (
                isinstance(now_monotonic, bool)
                or not isinstance(now_monotonic, (int, float))
                or not math.isfinite(float(now_monotonic))
                or float(now_monotonic) >= authorization.expires_at_monotonic
            ):
                raise CaptureAuthorizationRejected("OTP capture grant expired")
            if self.source is CaptureSource.SYNTHETIC:
                if explicit_user_action is not True:
                    raise CaptureAuthorizationRejected(
                        "explicit synthetic capture action required"
                    )
            elif explicit_user_action is not True and not authorization.automatic_capture:
                raise CaptureAuthorizationRejected(
                    "automatic OTP capture is not authorized"
                )
            return candidate
        except BaseException:
            _wipe(candidate)
            raise

    def close(self) -> None:
        with self._lock:
            candidate = self._candidate
            self._candidate = bytearray()
            self._taken = True
        _wipe(candidate)

    def __repr__(self) -> str:
        return f"<IsolatedOtpCandidate redacted source={self.source.value!r}>"


@runtime_checkable
class OtpCapturePort(Protocol):
    """Platform-facing port; implementations return only isolated candidates."""

    @property
    def source(self) -> CaptureSource: ...

    def capture(self, authorization: OtpCaptureAuthorization) -> IsolatedOtpCandidate | None:
        """Return one isolated candidate without message or notification text."""


class SyntheticOtpCaptureAdapter:
    """Single-use deterministic adapter; not an Android SMS implementation."""

    source = CaptureSource.SYNTHETIC

    def __init__(self, candidate: bytearray):
        if type(candidate) is not bytearray:
            raise InvalidOtp("OTP candidate must be an owned bytearray")
        self._candidate = candidate
        self._used = False
        self._lock = threading.Lock()

    def capture(self, authorization: OtpCaptureAuthorization) -> IsolatedOtpCandidate:
        if not isinstance(authorization, OtpCaptureAuthorization):
            raise CaptureAuthorizationRejected("OTP capture grant required")
        with self._lock:
            if self._used:
                raise CaptureAuthorizationRejected("synthetic adapter is exhausted")
            self._used = True
            candidate = self._candidate
            self._candidate = bytearray()
        if authorization.source is not self.source:
            _wipe(candidate)
            raise CaptureAuthorizationRejected("OTP capture source mismatch")
        return IsolatedOtpCandidate(
            source=self.source,
            grant_id=authorization.grant_id,
            target_device=authorization.target_device,
            _candidate=candidate,
        )

    def close(self) -> None:
        with self._lock:
            candidate = self._candidate
            self._candidate = bytearray()
            self._used = True
        _wipe(candidate)
