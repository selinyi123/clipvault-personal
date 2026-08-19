from __future__ import annotations

import ast
import inspect
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from threading import Event, Lock

import pytest

from clipvault.otp import (
    CapacityExceeded,
    CaptureRejected,
    ClaimContextMismatch,
    CrossDeviceSecurity,
    E2eeRequired,
    InvalidTransition,
    OtpNotFound,
    OtpClaimContext,
    OtpRelayCoordinator,
    OtpSinkKind,
    OtpUseFailed,
    PairingRequired,
    StoreClosed,
    TargetMismatch,
    TargetRevoked,
    TransportUnavailable,
)
from clipvault.otp import coordinator as otp_coordinator


EPOCH = "11111111-1111-4111-8111-111111111111"
LOCAL_DEVICE = "device:40000000-0000-4000-8000-000000000001"
REMOTE_DEVICE = "device:40000000-0000-4000-8000-000000000002"
CLAIM_CONTEXT = OtpClaimContext(
    OtpSinkKind.WINDOWS_TSF,
    "50000000-0000-4000-8000-000000000001",
)
STALE_CONTEXT = OtpClaimContext(
    OtpSinkKind.WINDOWS_TSF,
    "50000000-0000-4000-8000-000000000002",
)


class FakeClock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_coordinator(**overrides) -> OtpRelayCoordinator:
    event_counter = count(1)
    nonce_counter = count(1)

    def event_id_factory() -> str:
        value = next(event_counter)
        return f"00000000-0000-4000-8000-{value:012x}"

    def nonce_factory() -> bytearray:
        value = next(nonce_counter)
        return bytearray(f"nonce-{value:011d}".encode("ascii"))

    options = {
        "session_epoch": EPOCH,
        "local_device": LOCAL_DEVICE,
        "clock": FakeClock(),
        "default_ttl_seconds": 5.0,
        "max_ttl_seconds": 10.0,
        "replay_window_seconds": 10.0,
        "event_id_factory": event_id_factory,
        "nonce_factory": nonce_factory,
        "claim_token_factory": lambda: "claim-token",
    }
    options.update(overrides)
    return OtpRelayCoordinator(**options)


def capture(
    coordinator: OtpRelayCoordinator,
    text: bytes = b"731992",
    **overrides,
):
    source = bytearray(text)
    options = {
        "target_device": LOCAL_DEVICE,
        "explicit_user_action": True,
    }
    options.update(overrides)
    view = coordinator.capture_synthetic(source, **options)
    return source, view


def test_otp_v001_local_synthetic_claim_use_ack_is_single_use():
    coordinator = make_coordinator()
    source, view = capture(coordinator, b"731-992")
    assert source == bytearray(b"\0" * 7)
    assert (view.sender_device, view.target_device, view.sequence) == (
        LOCAL_DEVICE,
        LOCAL_DEVICE,
        1,
    )

    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    observed = []
    assert coordinator.use_and_ack(
        claim,
        CLAIM_CONTEXT,
        lambda secret: observed.append(bytes(secret)),
    ) is None
    assert observed == [b"731992"]
    assert coordinator.snapshot() == ()
    with pytest.raises(OtpNotFound):
        coordinator.use_and_ack(claim, CLAIM_CONTEXT, lambda secret: None)


def test_local_capture_policy_requires_explicit_isolated_numeric_candidate():
    coordinator = make_coordinator()

    implicit = bytearray(b"731992")
    with pytest.raises(CaptureRejected, match="explicit local"):
        coordinator.capture_synthetic(
            implicit,
            target_device=LOCAL_DEVICE,
            explicit_user_action=False,
        )
    assert implicit == bytearray(b"\0" * 6)

    message_body = bytearray(b"code 731992")
    with pytest.raises(CaptureRejected, match="candidate rejected"):
        coordinator.capture_synthetic(
            message_body,
            target_device=LOCAL_DEVICE,
            explicit_user_action=True,
        )
    assert message_body == bytearray(b"\0" * len(b"code 731992"))
    assert coordinator.snapshot() == ()


def test_otp_v003_expiry_wipes_payload_and_pending_claim_identity():
    clock = FakeClock()
    coordinator = make_coordinator(clock=clock)
    source, view = capture(coordinator, ttl_seconds=5.0)
    pending_nonce = coordinator._pending[view.event_id].nonce
    assert coordinator.next_deadline_monotonic() == 105.0

    clock.advance(5.0)
    assert coordinator.expire() == 1
    assert source == bytearray(b"\0" * 6)
    assert pending_nonce == bytearray(b"\0" * len(pending_nonce))
    assert coordinator.snapshot() == ()
    with pytest.raises(OtpNotFound):
        coordinator.claim_synthetic(
            event_id=view.event_id,
            target_device=LOCAL_DEVICE,
            claim_context=CLAIM_CONTEXT,
        )


def test_otp_v004_claim_rejects_stale_sink_context_before_secret_lease():
    coordinator = make_coordinator()
    _, view = capture(coordinator)
    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    called = False

    def must_not_receive_secret(secret: memoryview) -> None:
        nonlocal called
        called = True

    with pytest.raises(ClaimContextMismatch, match="context mismatch"):
        coordinator.use_and_ack(claim, STALE_CONTEXT, must_not_receive_secret)
    assert not called

    observed = []
    coordinator.use_and_ack(
        claim,
        CLAIM_CONTEXT,
        lambda secret: observed.append(bytes(secret)),
    )
    assert observed == [b"731992"]
    assert coordinator.snapshot() == ()


def test_claim_rejects_wrong_target_without_consuming_pending_event():
    coordinator = make_coordinator()
    _, view = capture(coordinator)

    with pytest.raises(TargetMismatch):
        coordinator.claim_synthetic(
            event_id=view.event_id,
            target_device=REMOTE_DEVICE,
            claim_context=CLAIM_CONTEXT,
        )

    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    coordinator.dismiss(claim)


def test_otp_v005_capacity_failure_wipes_rejected_input_without_eviction():
    coordinator = make_coordinator(capacity=1, replay_capacity=2)
    _, first = capture(coordinator, b"111111")

    rejected = bytearray(b"222222")
    with pytest.raises(CapacityExceeded):
        coordinator.capture_synthetic(
            rejected,
            target_device=LOCAL_DEVICE,
            explicit_user_action=True,
        )
    assert rejected == bytearray(b"\0" * 6)
    assert [view.event_id for view in coordinator.snapshot()] == [first.event_id]

    claim = coordinator.claim_synthetic(
        event_id=first.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    observed = []
    coordinator.use_and_ack(
        claim,
        CLAIM_CONTEXT,
        lambda secret: observed.append(bytes(secret)),
    )
    assert observed == [b"111111"]


def test_concurrent_local_capture_assigns_one_monotonic_sequence_per_event():
    coordinator = make_coordinator()
    sources = [bytearray(f"{1000 + index}".encode("ascii")) for index in range(8)]

    def admit(source: bytearray):
        return coordinator.capture_synthetic(
            source,
            target_device=LOCAL_DEVICE,
            explicit_user_action=True,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        views = list(executor.map(admit, sources))

    assert sorted(view.sequence for view in views) == list(range(1, 9))
    assert len({view.event_id for view in views}) == 8
    assert all(source == bytearray(b"\0" * 4) for source in sources)
    assert coordinator.clear_all() == 8


def test_otp_v006_competing_use_cannot_destroy_the_active_winner():
    coordinator = make_coordinator()
    _, view = capture(coordinator)
    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    entered = Event()
    release = Event()
    escaped = {}

    def first_callback(secret: memoryview) -> None:
        escaped["lease"] = secret.obj
        entered.set()
        assert release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            coordinator.use_and_ack,
            claim,
            CLAIM_CONTEXT,
            first_callback,
        )
        try:
            assert entered.wait(timeout=2)
            second = executor.submit(
                coordinator.use_and_ack,
                claim,
                CLAIM_CONTEXT,
                lambda secret: None,
            )
            with pytest.raises(InvalidTransition):
                second.result(timeout=0.5)
        finally:
            release.set()
        assert first.result(timeout=2) is None

    assert escaped["lease"] == bytearray(b"\0" * 6)
    assert coordinator.snapshot() == ()


def test_otp_v006_callback_failure_is_sanitized_and_terminally_destroyed():
    coordinator = make_coordinator()
    _, view = capture(coordinator)
    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    escaped = {}

    def fail(secret: memoryview) -> None:
        escaped["lease"] = secret.obj
        raise RuntimeError("callback detail must not escape")

    with pytest.raises(OtpUseFailed, match="OTP sink failed") as caught:
        coordinator.use_and_ack(claim, CLAIM_CONTEXT, fail)
    assert caught.value.__cause__ is None
    assert escaped["lease"] == bytearray(b"\0" * 6)
    assert coordinator.snapshot() == ()


def test_otp_v007_clear_dismiss_revoke_and_close_destroy_local_state():
    coordinator = make_coordinator()
    _, first = capture(coordinator, b"111111")
    first_nonce = coordinator._pending[first.event_id].nonce
    assert coordinator.clear_all() == 1
    assert first_nonce == bytearray(b"\0" * len(first_nonce))

    _, second = capture(coordinator, b"222222")
    claim = coordinator.claim_synthetic(
        event_id=second.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    coordinator.dismiss(claim)
    assert coordinator.snapshot() == ()

    _, third = capture(coordinator, b"333333")
    third_nonce = coordinator._pending[third.event_id].nonce
    assert coordinator.revoke_target(LOCAL_DEVICE) == 1
    assert third_nonce == bytearray(b"\0" * len(third_nonce))

    rejected = bytearray(b"444444")
    with pytest.raises(TargetRevoked):
        coordinator.capture_synthetic(
            rejected,
            target_device=LOCAL_DEVICE,
            explicit_user_action=True,
        )
    assert rejected == bytearray(b"\0" * 6)
    coordinator.close()
    assert coordinator.closed


def test_otp_v008_cross_device_requests_fail_closed_before_transport():
    coordinator = make_coordinator()
    cases = (
        (None, PairingRequired),
        (CrossDeviceSecurity(paired=True), E2eeRequired),
        (
            CrossDeviceSecurity(paired=True, e2ee_ready=True),
            TransportUnavailable,
        ),
    )

    for security, expected_error in cases:
        source = bytearray(b"731992")
        with pytest.raises(expected_error):
            coordinator.capture_synthetic(
                source,
                target_device=REMOTE_DEVICE,
                explicit_user_action=True,
                cross_device_security=security,
            )
        assert source == bytearray(b"\0" * 6)

    assert coordinator.snapshot() == ()


def test_otp_v009_repr_diagnostics_and_runtime_have_no_content_sinks(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    coordinator = make_coordinator()
    source, view = capture(coordinator)
    pending_nonce = bytes(coordinator._pending[view.event_id].nonce)
    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )

    rendered = " ".join((repr(coordinator), repr(view), repr(claim)))
    for sensitive in (
        "731992",
        view.event_id,
        LOCAL_DEVICE,
        pending_nonce.decode("ascii"),
        "claim-token",
    ):
        assert sensitive not in rendered
    coordinator.use_and_ack(claim, CLAIM_CONTEXT, lambda secret: None)
    assert source == bytearray(b"\0" * 6)
    assert list(tmp_path.iterdir()) == []

    tree = ast.parse(inspect.getsource(otp_coordinator))
    imported_roots = set()
    forbidden_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"open", "print", "input", "exec", "eval", "compile"}
        ):
            forbidden_calls.add(node.func.id)

    assert imported_roots.isdisjoint(
        {"http", "logging", "pathlib", "socket", "sqlite3", "urllib"}
    )
    assert forbidden_calls == set()


def test_successful_sink_racing_close_is_reported_as_terminal_success():
    coordinator = make_coordinator()
    _, view = capture(coordinator)
    claim = coordinator.claim_synthetic(
        event_id=view.event_id,
        target_device=LOCAL_DEVICE,
        claim_context=CLAIM_CONTEXT,
    )
    sink_entered = Event()
    release_sink = Event()
    observed: list[bytes] = []

    def sink(secret: memoryview) -> None:
        observed.append(bytes(secret))
        sink_entered.set()
        assert release_sink.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            coordinator.use_and_ack,
            claim,
            CLAIM_CONTEXT,
            sink,
        )
        assert sink_entered.wait(timeout=2)
        coordinator.close()
        release_sink.set()
        assert future.result(timeout=2) is None

    assert observed == [b"731992"]
    assert coordinator.closed


def test_otp_v010_clock_regression_wipes_and_permanently_closes_slice():
    clock = FakeClock()
    coordinator = make_coordinator(clock=clock)
    _, view = capture(coordinator)
    pending_nonce = coordinator._pending[view.event_id].nonce
    clock.now -= 1.0

    with pytest.raises(StoreClosed, match="clock"):
        coordinator.snapshot()
    assert coordinator.closed
    assert coordinator._pending == {}
    assert pending_nonce == bytearray(b"\0" * len(pending_nonce))

    rejected = bytearray(b"842061")
    with pytest.raises(StoreClosed):
        coordinator.capture_synthetic(
            rejected,
            target_device=LOCAL_DEVICE,
            explicit_user_action=True,
        )
    assert rejected == bytearray(b"\0" * 6)


def test_checked_clock_linearizes_source_reads_across_threads():
    first_entered = Event()
    second_entered = Event()
    release_first = Event()
    state_lock = Lock()
    calls = 0

    def source() -> float:
        nonlocal calls
        with state_lock:
            calls += 1
            call = calls
        if call == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        elif call == 2:
            second_entered.set()
        return float(call)

    checked = otp_coordinator._CheckedClock(source)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(checked)
        assert first_entered.wait(timeout=2)
        second = executor.submit(checked)
        assert not second_entered.wait(timeout=0.1)
        release_first.set()
        assert first.result(timeout=2) == 1.0
        assert second.result(timeout=2) == 2.0
    assert second_entered.is_set()


def test_deadline_maintenance_uses_one_clock_sample_and_wipes_pending_nonce():
    class AdvancingPerReadClock:
        def __init__(self):
            self._values = iter((100.0, 100.0, 104.999, 105.0))
            self._lock = Lock()

        def __call__(self) -> float:
            with self._lock:
                return next(self._values)

    coordinator = make_coordinator(clock=AdvancingPerReadClock())
    _, view = capture(coordinator, ttl_seconds=5.0)
    pending_nonce = coordinator._pending[view.event_id].nonce

    assert coordinator.next_deadline_monotonic() == 105.0
    assert view.event_id in coordinator._pending
    assert any(pending_nonce)

    assert coordinator.next_deadline_monotonic() is None
    assert coordinator._pending == {}
    assert pending_nonce == bytearray(b"\0" * len(pending_nonce))
