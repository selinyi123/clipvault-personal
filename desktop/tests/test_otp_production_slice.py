from __future__ import annotations

import ast
import gc
import inspect
import sqlite3
import weakref
from dataclasses import dataclass, replace
from itertools import count

import pytest

from clipvault.otp import (
    AckRejected,
    CaptureAuthorizationRejected,
    CaptureSource,
    EnvelopeAuthenticationFailed,
    IsolatedOtpCandidate,
    OtpCaptureAuthorization,
    OtpInboundDelivery,
    OtpRelayCoordinator,
    ReplayRejected,
    WindowsContextStale,
    WindowsOtpConsumer,
    WindowsOtpContext,
)
import clipvault.otp as otp_package
from clipvault.otp import capture as otp_capture
from clipvault.otp import channel as otp_channel
from clipvault.otp import coordinator as otp_coordinator
from clipvault.otp import pipeline as otp_pipeline
from clipvault.otp import relay as otp_relay
from clipvault.otp import testing as otp_testing
from clipvault.otp import transport as otp_transport
from clipvault.otp import windows as otp_windows
from clipvault.otp.capture import SyntheticOtpCaptureAdapter
from clipvault.otp.channel import SyntheticOtpPairChannel
from clipvault.otp.testing import (
    SyntheticOtpRelayProducer,
    SyntheticOtpRelayReceiver,
)
from clipvault.otp.transport import InMemoryOtpTransport, TransportStateError


EPOCH = "11111111-1111-4111-8111-111111111111"
ANDROID = "device:40000000-0000-4000-8000-000000000001"
WINDOWS = "device:40000000-0000-4000-8000-000000000002"
WINDOWS_B = "device:40000000-0000-4000-8000-000000000003"
GRANT = "50000000-0000-4000-8000-000000000001"
DOCUMENT = "60000000-0000-4000-8000-000000000001"
EVENT = "70000000-0000-4000-8000-000000000001"
ROOT = bytes(range(32))


class FakeClock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Stack:
    wall: FakeClock
    monotonic: FakeClock
    sender: SyntheticOtpPairChannel
    receiver_channel: SyntheticOtpPairChannel
    transport: InMemoryOtpTransport
    coordinator: OtpRelayCoordinator
    producer: SyntheticOtpRelayProducer
    receiver: SyntheticOtpRelayReceiver


def make_stack() -> Stack:
    wall = FakeClock(1_800_000_000.0)
    monotonic = FakeClock(100.0)
    event_counter = count(1)
    nonce_counter = count(1)
    sender = SyntheticOtpPairChannel(
        root_secret=bytearray(ROOT),
        session_epoch=EPOCH,
        local_device=ANDROID,
        remote_device=WINDOWS,
        wall_clock=wall,
        monotonic_clock=monotonic,
        event_id_factory=lambda: (
            f"70000000-0000-4000-8000-{next(event_counter):012x}"
        ),
        nonce_factory=lambda: bytearray(next(nonce_counter).to_bytes(24, "big")),
    )
    receiver_channel = SyntheticOtpPairChannel(
        root_secret=bytearray(ROOT),
        session_epoch=EPOCH,
        local_device=WINDOWS,
        remote_device=ANDROID,
        wall_clock=wall,
        monotonic_clock=monotonic,
    )
    transport = InMemoryOtpTransport(wall_clock=wall)
    coordinator = OtpRelayCoordinator(
        session_epoch=EPOCH,
        local_device=WINDOWS,
        clock=monotonic,
        default_ttl_seconds=30.0,
        max_ttl_seconds=120.0,
        replay_window_seconds=600.0,
    )
    producer = SyntheticOtpRelayProducer(
        channel=sender,
        transport=transport,
        monotonic_clock=monotonic,
    )
    receiver = SyntheticOtpRelayReceiver(
        channel=receiver_channel,
        transport=transport,
        coordinator=coordinator,
    )
    return Stack(
        wall=wall,
        monotonic=monotonic,
        sender=sender,
        receiver_channel=receiver_channel,
        transport=transport,
        coordinator=coordinator,
        producer=producer,
        receiver=receiver,
    )


def authorization(
    stack: Stack,
    *,
    source: CaptureSource = CaptureSource.SYNTHETIC,
    automatic: bool = False,
    granted: bool = True,
    session_epoch: str = EPOCH,
    sender_device: str = ANDROID,
    target_device: str = WINDOWS,
) -> OtpCaptureAuthorization:
    return OtpCaptureAuthorization(
        grant_id=GRANT,
        source=source,
        session_epoch=session_epoch,
        sender_device=sender_device,
        target_device=target_device,
        expires_at_monotonic=stack.monotonic.now + 60.0,
        platform_granted=granted,
        automatic_capture=automatic,
    )


def send_synthetic(stack: Stack, source: bytearray) -> str:
    receipt = stack.producer.capture_and_send(
        SyntheticOtpCaptureAdapter(source),
        authorization(stack),
        explicit_user_action=True,
        ttl_seconds=30.0,
    )
    return receipt.event_id


class DirectTsfPort:
    def __init__(self):
        self.current = True
        self.inserted: list[bytes] = []

    def is_context_current(self, context: WindowsOtpContext) -> bool:
        return self.current

    def insert_at_selection(
        self,
        context: WindowsOtpContext,
        secret: memoryview,
    ) -> bool:
        self.inserted.append(bytes(secret))
        return True


def windows_context() -> WindowsOtpContext:
    return WindowsOtpContext(
        process_id=1234,
        window_handle=5678,
        document_token=DOCUMENT,
    )


def test_otp_v011_authorized_capture_encrypted_delivery_ack_and_tsf_use(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stack = make_stack()
    original = bytearray(b"731-992")

    event_id = send_synthetic(stack, original)
    assert original == bytearray(b"\0" * 7)
    assert stack.transport.counts() == (1, 0)
    envelope = stack.transport._queued[event_id]
    wire = bytes(envelope.ciphertext) + bytes(envelope.tag)
    assert b"731992" not in wire
    assert "731992" not in repr(envelope)

    inbound = stack.receiver.receive_next()
    assert isinstance(inbound, OtpInboundDelivery)
    assert inbound.duplicate is False
    assert inbound.admitted is not None
    assert stack.transport.counts() == (0, 1)
    stack.producer.accept_delivery_ack(inbound.ack)
    assert stack.transport.counts() == (0, 0)
    assert envelope.closed

    port = DirectTsfPort()
    WindowsOtpConsumer(stack.coordinator).consume(
        event_id=event_id,
        context=windows_context(),
        insert_port=port,
    )
    assert port.inserted == [b"731992"]
    assert stack.coordinator.snapshot() == ()
    assert not hasattr(port, "clipboard")
    assert list(tmp_path.iterdir()) == []


def test_otp_v012_automatic_platform_adapter_requires_exact_live_grant():
    stack = make_stack()
    source = bytearray(b"842061")

    class AuthorizedAndroidAdapter:
        source = CaptureSource.ANDROID_SMS_PERMISSION

        def capture(self, grant):
            return IsolatedOtpCandidate(
                source=self.source,
                grant_id=grant.grant_id,
                target_device=grant.target_device,
                _candidate=source,
            )

    event_id = stack.producer.capture_and_send(
        AuthorizedAndroidAdapter(),
        authorization(
            stack,
            source=CaptureSource.ANDROID_SMS_PERMISSION,
            automatic=True,
        ),
        explicit_user_action=False,
        ttl_seconds=15.0,
    ).event_id
    assert event_id == EVENT
    assert source == bytearray(b"\0" * 6)


def test_otp_v012_unauthorized_or_implicit_synthetic_capture_wipes_source():
    stack = make_stack()
    source = bytearray(b"842061")
    with pytest.raises(
        CaptureAuthorizationRejected,
        match="explicit synthetic",
    ):
        stack.producer.capture_and_send(
            SyntheticOtpCaptureAdapter(source),
            authorization(stack),
            explicit_user_action=False,
            ttl_seconds=15.0,
        )
    assert source == bytearray(b"\0" * 6)
    assert stack.transport.counts() == (0, 0)


def test_otp_v013_production_constructors_always_fail_closed():
    stack = make_stack()
    stack.sender.production_ready = True
    stack.receiver_channel.production_ready = True
    stack.transport.production_ready = True
    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        otp_package.OtpRelayProducer(
            channel=stack.sender,
            transport=stack.transport,
            monotonic_clock=stack.monotonic,
        )
    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        otp_package.OtpRelayReceiver(
            channel=stack.receiver_channel,
            transport=stack.transport,
            coordinator=stack.coordinator,
        )
    assert not hasattr(otp_package, "SyntheticOtpPairChannel")
    assert not hasattr(otp_package, "SyntheticOtpCaptureAdapter")
    assert not hasattr(otp_package, "InMemoryOtpTransport")
    assert not hasattr(otp_package, "CompletionReceipt")
    assert not hasattr(otp_package, "_PROVIDER_REGISTRATION_CAPABILITY")
    assert not hasattr(otp_package, "_OtpRelayProducerBase")
    assert not hasattr(otp_package, "_OtpRelayReceiverBase")


def test_otp_v013_synthetic_subclasses_cannot_override_into_production():
    class ForgedChannel(SyntheticOtpPairChannel):
        production_ready = True

    class ForgedTransport(InMemoryOtpTransport):
        production_ready = True

    wall = FakeClock(1_800_000_000.0)
    monotonic = FakeClock(100.0)
    channel = ForgedChannel(
        root_secret=bytearray(ROOT),
        session_epoch=EPOCH,
        local_device=ANDROID,
        remote_device=WINDOWS,
        wall_clock=wall,
        monotonic_clock=monotonic,
    )
    transport = ForgedTransport(wall_clock=wall)

    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        otp_package.OtpRelayProducer(
            channel=channel,
            transport=transport,
            monotonic_clock=monotonic,
        )
    coordinator = OtpRelayCoordinator(
        session_epoch=EPOCH,
        local_device=WINDOWS,
        clock=monotonic,
    )
    with pytest.raises(RuntimeError, match="reviewed platform factory"):
        otp_package.OtpRelayReceiver(
            channel=channel,
            transport=transport,
            coordinator=coordinator,
        )


def test_otp_v013_no_python_provider_registry_or_registration_symbols_exist():
    for symbol in (
        "_PROVIDER_REGISTRATION_CAPABILITY",
        "_registered_provider_pairs",
        "_provider_registry_lock",
        "_register_production_provider_pair",
        "_is_registered_production_pair",
    ):
        assert not hasattr(otp_pipeline, symbol)


def test_otp_v013_closed_synthetic_providers_have_no_registry_strong_reference():
    wall = FakeClock(1_800_000_000.0)
    monotonic = FakeClock(100.0)
    channel = SyntheticOtpPairChannel(
        root_secret=bytearray(ROOT),
        session_epoch=EPOCH,
        local_device=ANDROID,
        remote_device=WINDOWS,
        wall_clock=wall,
        monotonic_clock=monotonic,
    )
    transport = InMemoryOtpTransport(wall_clock=wall)
    channel_ref = weakref.ref(channel)
    transport_ref = weakref.ref(transport)
    channel.close()
    transport.close()
    del channel
    del transport
    gc.collect()

    assert channel_ref() is None
    assert transport_ref() is None


@pytest.mark.parametrize(
    ("override", "value"),
    (
        ("session_epoch", "11111111-1111-4111-8111-111111111112"),
        ("sender_device", WINDOWS_B),
        ("target_device", WINDOWS_B),
    ),
)
def test_otp_v013_capture_authorization_is_bound_to_exact_pair(override, value):
    stack = make_stack()
    source = bytearray(b"731992")
    options = {override: value}

    with pytest.raises(
        EnvelopeAuthenticationFailed,
        match="authorization mismatch",
    ):
        stack.producer.capture_and_send(
            SyntheticOtpCaptureAdapter(source),
            authorization(stack, **options),
            explicit_user_action=True,
            ttl_seconds=30.0,
        )
    assert source == bytearray(b"\0" * 6)
    assert stack.transport.counts() == (0, 0)


def test_otp_v014_ciphertext_tampering_is_dropped_before_local_admission():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    envelope = stack.transport._queued[event_id]
    envelope.ciphertext[0] ^= 1

    with pytest.raises(EnvelopeAuthenticationFailed):
        stack.receiver.receive_next()
    assert stack.coordinator.snapshot() == ()
    assert stack.transport.counts() == (0, 0)
    assert envelope.closed


def test_otp_v015_lost_ack_retry_is_idempotent_and_never_readmits_plaintext():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    first = stack.receiver.receive_next()
    assert first is not None and first.duplicate is False
    assert len(stack.coordinator.snapshot()) == 1

    first.ack.close()  # simulate an ACK lost before it reaches the sender
    stack.transport.retry(event_id)
    second = stack.receiver.receive_next()
    assert second is not None and second.duplicate is True
    assert second.admitted is None
    assert len(stack.coordinator.snapshot()) == 1
    stack.producer.accept_delivery_ack(second.ack)
    assert stack.transport.counts() == (0, 0)


def test_otp_v016_tampered_ack_cannot_retire_ciphertext_or_sender_state():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    first = stack.receiver.receive_next()
    assert first is not None
    first.ack.tag[0] ^= 1
    with pytest.raises(AckRejected):
        stack.producer.accept_delivery_ack(first.ack)
    assert stack.transport.counts() == (0, 1)

    stack.transport.retry(event_id)
    retry = stack.receiver.receive_next()
    assert retry is not None and retry.duplicate is True
    stack.producer.accept_delivery_ack(retry.ack)
    assert stack.transport.counts() == (0, 0)


def test_otp_v016_ack_mutation_after_verify_cannot_change_transport_completion():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    inbound = stack.receiver.receive_next()
    assert inbound is not None
    envelope = stack.transport._inflight[event_id]

    receipt = stack.sender.verify_ack(inbound.ack)
    inbound.ack.event_id = "70000000-0000-4000-8000-000000000099"
    inbound.ack.sequence = 999
    inbound.ack.tag[:] = b"x" * len(inbound.ack.tag)

    stack.transport.complete(receipt)
    assert envelope.closed
    assert event_id in stack.sender._pending
    stack.sender.complete_ack(receipt)
    assert event_id not in stack.sender._pending
    inbound.ack.close()


def test_otp_v016_event_a_receipt_cannot_be_transplanted_to_event_b():
    stack = make_stack()
    event_a = send_synthetic(stack, bytearray(b"111111"))
    event_b = send_synthetic(stack, bytearray(b"222222"))
    inbound_a = stack.receiver.receive_next()
    inbound_b = stack.receiver.receive_next()
    assert inbound_a is not None and inbound_a.event_id == event_a
    assert inbound_b is not None and inbound_b.event_id == event_b
    receipt_a = stack.sender.verify_ack(inbound_a.ack)
    receipt_b = stack.sender.verify_ack(inbound_b.ack)

    transplanted = replace(
        receipt_a,
        event_id=receipt_b.event_id,
        sequence=receipt_b.sequence,
        envelope_tag_digest=receipt_b.envelope_tag_digest,
    )
    with pytest.raises(otp_package.TargetMismatch, match="completion target"):
        stack.transport.complete(transplanted)
    with pytest.raises(AckRejected, match="completion receipt"):
        stack.sender.complete_ack(transplanted)
    assert event_a in stack.sender._pending
    assert event_b in stack.sender._pending
    assert stack.transport.counts() == (0, 2)

    stack.transport.complete(receipt_a)
    stack.sender.complete_ack(receipt_a)
    stack.transport.complete(receipt_b)
    stack.sender.complete_ack(receipt_b)
    inbound_a.ack.close()
    inbound_b.ack.close()
    assert stack.transport.counts() == (0, 0)
    assert event_a not in stack.sender._pending
    assert event_b not in stack.sender._pending


def test_otp_v016_transport_failure_keeps_sender_pending_until_atomic_completion():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    inbound = stack.receiver.receive_next()
    assert inbound is not None
    receipt = stack.sender.verify_ack(inbound.ack)
    assert event_id in stack.sender._pending

    forged = replace(receipt, _event_capability=object())
    with pytest.raises(otp_package.TargetMismatch, match="completion target"):
        stack.transport.complete(forged)
    assert event_id in stack.sender._pending

    stack.transport.retry(event_id)
    with pytest.raises(TransportStateError, match="not in flight"):
        stack.transport.complete(receipt)
    assert event_id in stack.sender._pending

    assert stack.transport.take(target_device=WINDOWS) is not None
    stack.transport.complete(receipt)
    assert event_id in stack.sender._pending
    stack.sender.complete_ack(receipt)
    assert event_id not in stack.sender._pending
    inbound.ack.close()


def test_otp_v016_pair_session_rejects_nonce_reuse_and_wipes_rejected_source():
    wall = FakeClock(1_800_000_000.0)
    monotonic = FakeClock(100.0)
    event_ids = iter(
        (
            "70000000-0000-4000-8000-000000000001",
            "70000000-0000-4000-8000-000000000002",
        )
    )
    channel = SyntheticOtpPairChannel(
        root_secret=bytearray(ROOT),
        session_epoch=EPOCH,
        local_device=ANDROID,
        remote_device=WINDOWS,
        wall_clock=wall,
        monotonic_clock=monotonic,
        event_id_factory=lambda: next(event_ids),
        nonce_factory=lambda: bytearray(b"r" * 24),
    )
    first = channel.seal(
        bytearray(b"111111"),
        authorized_session_epoch=EPOCH,
        authorized_sender_device=ANDROID,
        authorized_target_device=WINDOWS,
        ttl_seconds=30.0,
    )
    rejected = bytearray(b"222222")

    with pytest.raises(ReplayRejected, match="nonce reuse"):
        channel.seal(
            rejected,
            authorized_session_epoch=EPOCH,
            authorized_sender_device=ANDROID,
            authorized_target_device=WINDOWS,
            ttl_seconds=30.0,
        )
    assert rejected == bytearray(b"\0" * 6)
    first.close()
    channel.close()


def test_otp_v017_stale_windows_context_never_leases_or_destroys_pending_code():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    inbound = stack.receiver.receive_next()
    assert inbound is not None
    stack.producer.accept_delivery_ack(inbound.ack)
    port = DirectTsfPort()
    port.current = False

    with pytest.raises(WindowsContextStale):
        WindowsOtpConsumer(stack.coordinator).consume(
            event_id=event_id,
            context=windows_context(),
            insert_port=port,
        )
    assert port.inserted == []
    assert len(stack.coordinator.snapshot()) == 1

    port.current = True
    WindowsOtpConsumer(stack.coordinator).consume(
        event_id=event_id,
        context=windows_context(),
        insert_port=port,
    )
    assert port.inserted == [b"731992"]


def test_otp_v018_transport_expiry_wipes_unacknowledged_envelope():
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    envelope = stack.transport._queued[event_id]
    stack.wall.advance(30.0)

    assert stack.transport.expire() == 1
    assert envelope.closed
    assert envelope.nonce == bytearray(b"\0" * 24)
    assert all(value == 0 for value in envelope.ciphertext)
    assert all(value == 0 for value in envelope.tag)


def test_otp_v019_relay_modules_have_no_storage_clipboard_or_network_imports():
    modules = (
        otp_capture,
        otp_channel,
        otp_coordinator,
        otp_pipeline,
        otp_relay,
        otp_testing,
        otp_transport,
        otp_windows,
    )
    forbidden_roots = {
        "clipvault.pipeline",
        "clipvault.store",
        "clipvault.sync",
        "clipvault.watcher",
        "http",
        "json",
        "logging",
        "pathlib",
        "socket",
        "sqlite3",
        "urllib",
    }
    forbidden_calls = set()
    imports = set()
    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"open", "print", "input", "exec", "eval"}
            ):
                forbidden_calls.add(node.func.id)

    assert not any(
        imported == forbidden
        or imported.startswith(forbidden + ".")
        for imported in imports
        for forbidden in forbidden_roots
    )
    assert forbidden_calls == set()


def test_otp_v020_complete_relay_never_appends_to_normal_sync_outbox():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE sync_outbox ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT, created_at TEXT)"
    )
    stack = make_stack()
    event_id = send_synthetic(stack, bytearray(b"731992"))
    inbound = stack.receiver.receive_next()
    assert inbound is not None
    stack.producer.accept_delivery_ack(inbound.ack)
    WindowsOtpConsumer(stack.coordinator).consume(
        event_id=event_id,
        context=windows_context(),
        insert_port=DirectTsfPort(),
    )

    assert conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0] == 0
