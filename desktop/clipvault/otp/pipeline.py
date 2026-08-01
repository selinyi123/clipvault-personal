"""Production OTP relay composition over reviewed channel/transport ports."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .capture import OtpCaptureAuthorization, OtpCapturePort
from .channel import (
    EnvelopeAuthenticationFailed,
    EnvelopeExpired,
    OtpDeliveryAck,
    OtpPairChannelPort,
)
from .coordinator import OtpCapturePolicy, OtpRelayCoordinator
from .relay import (
    InvalidOtp,
    OtpEventView,
    ReplayRejected,
    SessionMismatch,
    TargetMismatch,
)
from .transport import OtpEnvelopeTransportPort


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0


@dataclass(frozen=True, slots=True, repr=False)
class OtpSendReceipt:
    event_id: str
    target_device: str

    def __repr__(self) -> str:
        return "<OtpSendReceipt redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class OtpInboundDelivery:
    event_id: str
    duplicate: bool
    ack: OtpDeliveryAck = field(repr=False)
    admitted: OtpEventView | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"<OtpInboundDelivery redacted duplicate={self.duplicate!r}>"


class _OtpRelayProducerBase:
    """Restricted capture -> normalize -> encrypt -> OTP-only transport."""

    def __init__(
        self,
        *,
        channel: OtpPairChannelPort,
        transport: OtpEnvelopeTransportPort,
        capture_policy: OtpCapturePolicy | None = None,
        monotonic_clock=time.monotonic,
    ):
        if not isinstance(channel, OtpPairChannelPort):
            raise ValueError("OTP producer requires an encrypted channel port")
        if not isinstance(transport, OtpEnvelopeTransportPort):
            raise ValueError("OTP producer requires an OTP-only transport")
        policy = capture_policy or OtpCapturePolicy()
        if not isinstance(policy, OtpCapturePolicy):
            raise ValueError("invalid OTP capture policy")
        if not callable(monotonic_clock):
            raise ValueError("OTP producer clock must be callable")
        self._channel = channel
        self._transport = transport
        self._capture_policy = policy
        self._clock = monotonic_clock

    def capture_and_send(
        self,
        adapter: OtpCapturePort,
        authorization: OtpCaptureAuthorization,
        *,
        explicit_user_action: bool,
        ttl_seconds: float,
    ) -> OtpSendReceipt:
        if not isinstance(adapter, OtpCapturePort):
            raise InvalidOtp("invalid OTP capture adapter")
        if adapter.source is not authorization.source:
            raise InvalidOtp("OTP capture adapter source mismatch")
        isolated = adapter.capture(authorization)
        if isolated is None:
            raise InvalidOtp("OTP capture adapter returned no candidate")
        candidate: bytearray | None = None
        normalized: bytearray | None = None
        envelope = None
        try:
            try:
                now = float(self._clock())
            except Exception:
                raise InvalidOtp("OTP capture clock failed") from None
            if not math.isfinite(now):
                raise InvalidOtp("OTP capture clock failed")
            candidate = isolated.take(
                authorization,
                now_monotonic=now,
                explicit_user_action=explicit_user_action,
            )
            normalized = self._capture_policy.normalize(candidate)
            envelope = self._channel.seal(
                normalized,
                authorized_session_epoch=authorization.session_epoch,
                authorized_sender_device=authorization.sender_device,
                authorized_target_device=authorization.target_device,
                ttl_seconds=ttl_seconds,
            )
            normalized = None  # seal consumed and wiped it
            self._transport.send(envelope)
            return OtpSendReceipt(
                event_id=envelope.event_id,
                target_device=envelope.target_device,
            )
        except BaseException:
            if envelope is not None:
                self._channel.cancel_pending(envelope.event_id)
                envelope.close()
            raise
        finally:
            isolated.close()
            if candidate is not None:
                _wipe(candidate)
            if normalized is not None:
                _wipe(normalized)

    def accept_delivery_ack(self, ack: OtpDeliveryAck) -> None:
        """Atomically finish transport before retiring sender ACK state."""

        try:
            receipt = self._channel.verify_ack(ack)
            self._transport.complete(receipt)
            self._channel.complete_ack(receipt)
        finally:
            ack.close()


class OtpRelayProducer(_OtpRelayProducerBase):
    """Fail-closed placeholder until a reviewed platform factory exists."""

    def __init__(
        self,
        *,
        channel: OtpPairChannelPort | None = None,
        transport: OtpEnvelopeTransportPort | None = None,
        capture_policy: OtpCapturePolicy | None = None,
        monotonic_clock=time.monotonic,
    ):
        raise RuntimeError(
            "production OTP relay is unavailable; use a reviewed platform factory"
        )


class _OtpRelayReceiverBase:
    """OTP-only transport -> authenticate -> local in-memory admission."""

    def __init__(
        self,
        *,
        channel: OtpPairChannelPort,
        transport: OtpEnvelopeTransportPort,
        coordinator: OtpRelayCoordinator,
    ):
        if not isinstance(channel, OtpPairChannelPort):
            raise ValueError("OTP receiver requires an encrypted channel port")
        if not isinstance(transport, OtpEnvelopeTransportPort):
            raise ValueError("OTP receiver requires an OTP-only transport")
        if not isinstance(coordinator, OtpRelayCoordinator):
            raise ValueError("OTP receiver requires an OTP coordinator")
        self._channel = channel
        self._transport = transport
        self._coordinator = coordinator

    def receive_next(self) -> OtpInboundDelivery | None:
        envelope = self._transport.take(
            target_device=self._coordinator.local_device
        )
        if envelope is None:
            return None
        try:
            def admit(delivery, secret: memoryview):
                return self._coordinator.admit_authenticated(
                    bytearray(secret),
                    authenticated_session_epoch=delivery.session_epoch,
                    authenticated_sender_device=delivery.sender_device,
                    authenticated_sequence=delivery.sequence,
                    authenticated_expires_at_monotonic=(
                        delivery.expires_at_monotonic
                    ),
                    event_id=delivery.event_id,
                    target_device=delivery.target_device,
                    nonce=bytearray(envelope.nonce),
                )

            result = self._channel.receive(envelope, admit)
            admitted = (
                result.admitted if isinstance(result.admitted, OtpEventView) else None
            )
            return OtpInboundDelivery(
                event_id=envelope.event_id,
                duplicate=result.duplicate,
                ack=result.ack,
                admitted=admitted,
            )
        except (
            EnvelopeAuthenticationFailed,
            EnvelopeExpired,
            ReplayRejected,
            SessionMismatch,
            TargetMismatch,
        ):
            try:
                self._transport.discard(envelope.event_id)
            except BaseException:
                # Concurrent expiry/close already removed and wiped the
                # invalid envelope; preserve the authentication failure.
                pass
            raise
        except BaseException:
            try:
                self._transport.retry(envelope.event_id)
            except BaseException:
                # Concurrent transport shutdown/expiry is already terminal and
                # must not replace the original admission failure.
                pass
            raise


class OtpRelayReceiver(_OtpRelayReceiverBase):
    """Fail-closed placeholder until a reviewed platform factory exists."""

    def __init__(
        self,
        *,
        channel: OtpPairChannelPort | None = None,
        transport: OtpEnvelopeTransportPort | None = None,
        coordinator: OtpRelayCoordinator | None = None,
    ):
        raise RuntimeError(
            "production OTP relay is unavailable; use a reviewed platform factory"
        )
