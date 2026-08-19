"""Explicit test-only composition for the synthetic OTP providers."""

from __future__ import annotations

import time

from .channel import SyntheticOtpPairChannel
from .coordinator import OtpCapturePolicy, OtpRelayCoordinator
from .pipeline import _OtpRelayProducerBase, _OtpRelayReceiverBase
from .transport import InMemoryOtpTransport


class SyntheticOtpRelayProducer(_OtpRelayProducerBase):
    """Test-only producer; production factories never construct this type."""

    def __init__(
        self,
        *,
        channel: SyntheticOtpPairChannel,
        transport: InMemoryOtpTransport,
        capture_policy: OtpCapturePolicy | None = None,
        monotonic_clock=time.monotonic,
    ):
        if not isinstance(channel, SyntheticOtpPairChannel):
            raise ValueError("synthetic producer requires the synthetic channel")
        if not isinstance(transport, InMemoryOtpTransport):
            raise ValueError("synthetic producer requires the in-memory transport")
        super().__init__(
            channel=channel,
            transport=transport,
            capture_policy=capture_policy,
            monotonic_clock=monotonic_clock,
        )


class SyntheticOtpRelayReceiver(_OtpRelayReceiverBase):
    """Test-only receiver; production factories never construct this type."""

    def __init__(
        self,
        *,
        channel: SyntheticOtpPairChannel,
        transport: InMemoryOtpTransport,
        coordinator: OtpRelayCoordinator,
    ):
        if not isinstance(channel, SyntheticOtpPairChannel):
            raise ValueError("synthetic receiver requires the synthetic channel")
        if not isinstance(transport, InMemoryOtpTransport):
            raise ValueError("synthetic receiver requires the in-memory transport")
        super().__init__(
            channel=channel,
            transport=transport,
            coordinator=coordinator,
        )
