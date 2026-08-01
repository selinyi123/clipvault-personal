"""Windows non-clipboard OTP consumption boundary.

The concrete TSF implementation is platform-owned and not present in this
branch.  It must implement ``WindowsOtpInsertPort`` using a bound TSF document
context and InsertTextAtSelection-equivalent behavior; clipboard and SendInput
implementations are outside this interface.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .coordinator import OtpRelayCoordinator
from .relay import InvalidOtp, OtpClaimContext, OtpSinkKind, OtpUseFailed


class WindowsContextStale(OtpUseFailed):
    pass


class WindowsInsertFailed(OtpUseFailed):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class WindowsOtpContext:
    process_id: int
    window_handle: int
    document_token: str

    def __post_init__(self) -> None:
        if type(self.process_id) is not int or self.process_id <= 0:
            raise InvalidOtp("invalid Windows OTP process")
        if type(self.window_handle) is not int or self.window_handle <= 0:
            raise InvalidOtp("invalid Windows OTP window")
        try:
            parsed = uuid.UUID(self.document_token)
        except (TypeError, AttributeError, ValueError):
            raise InvalidOtp("invalid Windows OTP document token") from None
        if str(parsed) != self.document_token or parsed.version != 4:
            raise InvalidOtp("invalid Windows OTP document token")

    def claim_context(self) -> OtpClaimContext:
        return OtpClaimContext(OtpSinkKind.WINDOWS_TSF, self.document_token)

    def __repr__(self) -> str:
        return "<WindowsOtpContext redacted>"


@runtime_checkable
class WindowsOtpInsertPort(Protocol):
    """TSF-bound direct insert port; no clipboard method is intentionally exposed."""

    def is_context_current(self, context: WindowsOtpContext) -> bool: ...

    def insert_at_selection(
        self,
        context: WindowsOtpContext,
        secret: memoryview,
    ) -> bool: ...


class WindowsOtpConsumer:
    def __init__(self, coordinator: OtpRelayCoordinator):
        if not isinstance(coordinator, OtpRelayCoordinator):
            raise ValueError("Windows OTP consumer requires a coordinator")
        self._coordinator = coordinator

    def consume(
        self,
        *,
        event_id: str,
        context: WindowsOtpContext,
        insert_port: WindowsOtpInsertPort,
    ) -> None:
        """Insert once into the exact TSF context and atomically consume."""

        if not isinstance(context, WindowsOtpContext):
            raise InvalidOtp("invalid Windows OTP context")
        if not isinstance(insert_port, WindowsOtpInsertPort):
            raise InvalidOtp("invalid Windows OTP insert port")
        if not insert_port.is_context_current(context):
            raise WindowsContextStale("Windows OTP context is stale")
        claim_context = context.claim_context()
        claim = self._coordinator.claim(
            event_id=event_id,
            target_device=self._coordinator.local_device,
            claim_context=claim_context,
        )

        def insert(secret: memoryview) -> None:
            if not insert_port.is_context_current(context):
                raise WindowsContextStale("Windows OTP context is stale")
            if insert_port.insert_at_selection(context, secret) is not True:
                raise WindowsInsertFailed("Windows OTP insert failed")

        self._coordinator.use_and_ack(claim, claim_context, insert)
