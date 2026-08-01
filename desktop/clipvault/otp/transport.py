"""Strictly in-memory transport adapter for OTP envelope integration tests."""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from .channel import CompletionReceipt, EncryptedOtpEnvelope
from .relay import CapacityExceeded, OtpRelayError, StoreClosed, TargetMismatch


class TransportStateError(OtpRelayError):
    pass


@runtime_checkable
class OtpEnvelopeTransportPort(Protocol):
    """Transport contract; production providers must be externally reviewed."""

    def send(self, envelope: EncryptedOtpEnvelope) -> None: ...

    def take(self, *, target_device: str) -> EncryptedOtpEnvelope | None: ...

    def retry(self, event_id: str) -> None: ...

    def discard(self, event_id: str) -> None: ...

    def complete(self, receipt: CompletionReceipt) -> None: ...

    def close(self) -> None: ...


class InMemoryOtpTransport:
    """Ownership-transfer queue that never touches sync outbox or storage.

    ``send`` transfers envelope ownership to the transport. ``take`` leases it
    to one receiver while retaining ownership. A verified ACK completes and
    wipes the envelope; failures may explicitly return it to the queue.
    """

    def __init__(
        self,
        *,
        capacity: int = 32,
        wall_clock: Callable[[], float] = time.time,
    ):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("OTP transport capacity must be positive")
        if not callable(wall_clock):
            raise ValueError("OTP transport clock must be callable")
        self._capacity = capacity
        self._clock = wall_clock
        self._queued: dict[str, EncryptedOtpEnvelope] = {}
        self._inflight: dict[str, EncryptedOtpEnvelope] = {}
        self._last_now: float | None = None
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "<InMemoryOtpTransport redacted>"

    def _require_open_locked(self) -> None:
        if self._closed:
            raise StoreClosed("OTP transport is closed")

    def _now_locked(self) -> float:
        try:
            now = float(self._clock())
        except Exception:
            self._close_locked()
            raise StoreClosed("OTP transport clock failed") from None
        if (
            not math.isfinite(now)
            or (self._last_now is not None and now < self._last_now)
        ):
            self._close_locked()
            raise StoreClosed("OTP transport clock failed")
        self._last_now = now
        return now

    def _expire_locked(self, now: float) -> int:
        now_ms = int(now * 1000)
        expired = [
            event_id
            for event_id, envelope in (*self._queued.items(), *self._inflight.items())
            if now_ms >= envelope.expires_at_unix_ms
        ]
        for event_id in expired:
            envelope = self._queued.pop(event_id, None)
            if envelope is None:
                envelope = self._inflight.pop(event_id, None)
            if envelope is not None:
                envelope.close()
        return len(expired)

    def send(self, envelope: EncryptedOtpEnvelope) -> None:
        if not isinstance(envelope, EncryptedOtpEnvelope) or envelope.closed:
            raise TransportStateError("invalid OTP transport envelope")
        with self._lock:
            self._require_open_locked()
            self._expire_locked(self._now_locked())
            if (
                envelope.event_id in self._queued
                or envelope.event_id in self._inflight
            ):
                raise TransportStateError("OTP envelope already queued")
            if len(self._queued) + len(self._inflight) >= self._capacity:
                raise CapacityExceeded("OTP transport capacity reached")
            self._queued[envelope.event_id] = envelope

    def take(self, *, target_device: str) -> EncryptedOtpEnvelope | None:
        with self._lock:
            self._require_open_locked()
            self._expire_locked(self._now_locked())
            for event_id, envelope in self._queued.items():
                if envelope.target_device == target_device:
                    del self._queued[event_id]
                    self._inflight[event_id] = envelope
                    return envelope
            return None

    def retry(self, event_id: str) -> None:
        with self._lock:
            self._require_open_locked()
            self._expire_locked(self._now_locked())
            envelope = self._inflight.pop(event_id, None)
            if envelope is None:
                raise TransportStateError("OTP envelope is not in flight")
            self._queued[event_id] = envelope

    def discard(self, event_id: str) -> None:
        """Wipe one invalid or terminal envelope without acknowledging it."""

        with self._lock:
            self._require_open_locked()
            envelope = self._inflight.pop(event_id, None)
            if envelope is None:
                envelope = self._queued.pop(event_id, None)
            if envelope is None:
                raise TransportStateError("OTP envelope is unavailable")
            envelope.close()

    def complete(self, receipt: CompletionReceipt) -> None:
        if not isinstance(receipt, CompletionReceipt):
            raise TransportStateError("invalid OTP completion receipt")
        with self._lock:
            self._require_open_locked()
            envelope = self._inflight.get(receipt.event_id)
            if envelope is None:
                raise TransportStateError("OTP envelope is not in flight")
            if (
                envelope.session_epoch != receipt.session_epoch
                or envelope.sender_device != receipt.sender_device
                or envelope.target_device != receipt.target_device
                or envelope.sequence != receipt.sequence
                or envelope._completion_capability is not receipt._event_capability
                or not secrets.compare_digest(
                    hashlib.sha256(bytes(envelope.tag)).digest(),
                    receipt.envelope_tag_digest,
                )
            ):
                raise TargetMismatch("OTP completion target mismatch")
            del self._inflight[receipt.event_id]
            envelope.close()

    def expire(self) -> int:
        with self._lock:
            self._require_open_locked()
            return self._expire_locked(self._now_locked())

    def counts(self) -> tuple[int, int]:
        with self._lock:
            self._require_open_locked()
            self._expire_locked(self._now_locked())
            return len(self._queued), len(self._inflight)

    def _close_locked(self) -> None:
        envelopes = (*self._queued.values(), *self._inflight.values())
        self._queued.clear()
        self._inflight.clear()
        for envelope in envelopes:
            envelope.close()
        self._closed = True

    def close(self) -> None:
        with self._lock:
            self._close_locked()
