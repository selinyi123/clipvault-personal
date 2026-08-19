from __future__ import annotations

import ast
import inspect
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from clipvault.otp import (
    CapacityExceeded,
    ClaimContextMismatch,
    EventState,
    InvalidOtp,
    InvalidTransition,
    OtpNotFound,
    OtpClaimContext,
    OtpRelayStore,
    OtpSinkKind,
    OtpUseFailed,
    ReplayRejected,
    SenderMismatch,
    SessionMismatch,
    StoreClosed,
    TargetMismatch,
    TargetRevoked,
)
from clipvault.otp import relay as otp_relay


EPOCH_A = "11111111-1111-4111-8111-111111111111"
EPOCH_B = "22222222-2222-4222-8222-222222222222"
EVENT_1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EVENT_2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EVENT_3 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
EVENT_4 = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
NONCE_1 = b"0123456789abcdef"
NONCE_2 = b"fedcba9876543210"
NONCE_3 = b"third-nonce-0001"
NONCE_4 = b"fourth-nonce-001"
TARGET_A = "device:10000000-0000-4000-8000-000000000001"
TARGET_B = "device:10000000-0000-4000-8000-000000000002"
SENDER_A = "device:20000000-0000-4000-8000-000000000001"
SENDER_B = "device:20000000-0000-4000-8000-000000000002"
CONTEXT_A = OtpClaimContext(
    OtpSinkKind.WINDOWS_TSF,
    "30000000-0000-4000-8000-000000000001",
)
CONTEXT_B = OtpClaimContext(
    OtpSinkKind.WINDOWS_TSF,
    "30000000-0000-4000-8000-000000000002",
)


class FakeClock:
    def __init__(self, now: float = 100.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_store(**overrides) -> OtpRelayStore:
    options = {
        "session_epoch": EPOCH_A,
        "clock": FakeClock(),
        "claim_token_factory": lambda: "claim-token",
    }
    options.update(overrides)
    return OtpRelayStore(**options)


def add_event(
    store: OtpRelayStore,
    *,
    event_id: str = EVENT_1,
    target_device: str = TARGET_A,
    nonce: bytes = NONCE_1,
    text: bytes = b"731992",
    sender_device: str = SENDER_A,
    sequence: int = 1,
    expires_at_monotonic: float | None = None,
    authenticated_session_epoch: str | None = None,
) -> bytearray:
    source = bytearray(text)
    if expires_at_monotonic is None:
        expires_at_monotonic = float(store._clock()) + 60.0
    store.add(
        authenticated_session_epoch=(
            store.session_epoch
            if authenticated_session_epoch is None
            else authenticated_session_epoch
        ),
        authenticated_sender_device=sender_device,
        authenticated_sequence=sequence,
        authenticated_expires_at_monotonic=expires_at_monotonic,
        event_id=event_id,
        target_device=target_device,
        nonce=nonce,
        code=source,
    )
    return source


def claim_event(
    store: OtpRelayStore,
    *,
    event_id: str = EVENT_1,
    sender_device: str | None = None,
    sequence: int | None = None,
    expires_at_monotonic: float | None = None,
    claim_context: OtpClaimContext = CONTEXT_A,
):
    event = store._events[event_id]
    return store.claim(
        authenticated_sender_device=(
            event.sender_device if sender_device is None else sender_device
        ),
        authenticated_sequence=(event.sequence if sequence is None else sequence),
        authenticated_expires_at_monotonic=(
            event.expires_at
            if expires_at_monotonic is None
            else expires_at_monotonic
        ),
        event_id=event_id,
        target_device=event.target_device,
        claim_context=claim_context,
        nonce=NONCE_1,
    )


def test_use_secret_is_single_use_and_ack_removes_metadata():
    store = make_store()
    source = add_event(store)
    assert source == bytearray(b"\0" * 6)
    claim = claim_event(store)

    observed = []

    def use_and_return(secret: memoryview) -> str:
        observed.append(bytes(secret))
        return "sink result must not escape"

    assert store.use_secret(claim, CONTEXT_A, use_and_return) is None
    assert observed == [b"731992"]
    with pytest.raises(InvalidTransition):
        store.use_secret(claim, CONTEXT_A, lambda secret: bytes(secret))

    store.ack(claim)
    assert store.snapshot(target_device=TARGET_A) == ()
    with pytest.raises(OtpNotFound):
        store.ack(claim)


def test_temporary_view_and_underlying_buffer_are_invalidated_before_return():
    store = make_store()
    add_event(store)
    claim = claim_event(store)
    escaped: dict[str, object] = {}

    def observe(secret: memoryview) -> None:
        escaped["view"] = secret
        escaped["buffer"] = secret.obj
        assert bytes(secret) == b"731992"

    store.use_secret(claim, CONTEXT_A, observe)

    with pytest.raises(ValueError):
        bytes(escaped["view"])
    assert escaped["buffer"] == bytearray(b"\0" * 6)


def test_callback_failure_is_sanitized_after_wiping_and_consuming_the_event():
    store = make_store()
    add_event(store)
    claim = claim_event(store)
    escaped = {}

    def fail(secret: memoryview) -> None:
        escaped["buffer"] = secret.obj
        raise RuntimeError(f"sink leaked {bytes(secret).decode('ascii')}")

    with pytest.raises(OtpUseFailed, match="OTP sink failed") as caught:
        store.use_secret(claim, CONTEXT_A, fail)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "731992" not in str(caught.value)
    assert escaped["buffer"] == bytearray(b"\0" * 6)
    with pytest.raises(InvalidTransition):
        store.use_secret(claim, CONTEXT_A, lambda secret: None)
    store.dismiss(claim)


def test_add_clears_owned_bytearray_on_validation_and_runtime_failures():
    store = make_store(capacity=1, replay_capacity=2)
    add_event(store)

    invalid_metadata = bytearray(b"111111")
    with pytest.raises(InvalidOtp):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id="free text event id",
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=invalid_metadata,
        )
    assert invalid_metadata == bytearray(b"\0" * 6)

    duplicate = bytearray(b"222222")
    with pytest.raises(ReplayRejected):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_1,
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=duplicate,
        )
    assert duplicate == bytearray(b"\0" * 6)

    over_capacity = bytearray(b"333333")
    with pytest.raises(CapacityExceeded):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_2,
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=over_capacity,
        )
    assert over_capacity == bytearray(b"\0" * 6)

    with pytest.raises(InvalidOtp, match="owned bytearray"):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_3,
            target_device=TARGET_A,
            nonce=b"third-nonce-value",
            code=b"444444",
        )


def test_add_rejects_stale_authenticated_epoch_and_wipes_source():
    store = make_store(session_epoch=EPOCH_A)
    source = bytearray(b"731992")

    with pytest.raises(SessionMismatch, match="another session"):
        store.add(
            authenticated_session_epoch=EPOCH_B,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_1,
            target_device=TARGET_A,
            nonce=NONCE_1,
            code=source,
        )

    assert source == bytearray(b"\0" * 6)
    assert store.snapshot(target_device=TARGET_A) == ()


@pytest.mark.parametrize(
    "failing_symbol",
    ["_StoredEvent", "_ReplayMarker", "OtpEventView"],
)
def test_add_constructor_failure_rolls_back_and_wipes_private_copy(
    monkeypatch,
    failing_symbol: str,
):
    store = make_store()
    real_stored = otp_relay._StoredEvent
    captured = {}

    def capture_stored(**kwargs):
        captured["buffer"] = kwargs["code"]
        if failing_symbol == "_StoredEvent":
            raise MemoryError("injected constructor failure")
        return real_stored(**kwargs)

    def fail_constructor(**kwargs):
        raise MemoryError("injected constructor failure")

    source = bytearray(b"731992")
    with monkeypatch.context() as patcher:
        patcher.setattr(otp_relay, "_StoredEvent", capture_stored)
        if failing_symbol != "_StoredEvent":
            patcher.setattr(otp_relay, failing_symbol, fail_constructor)
        with pytest.raises(MemoryError, match="constructor failure"):
            store.add(
                authenticated_session_epoch=store.session_epoch,
                authenticated_sender_device=SENDER_A,
                authenticated_sequence=1,
                authenticated_expires_at_monotonic=float(store._clock()) + 60,
                event_id=EVENT_1,
                target_device=TARGET_A,
                nonce=NONCE_1,
                code=source,
            )

    assert source == bytearray(b"\0" * 6)
    assert captured["buffer"] == bytearray(b"\0" * 6)
    assert store._events == {}
    assert store._markers == {}
    assert store._event_index == {}
    assert store._nonce_index == {}
    assert store._highest_sequence_by_sender == {}
    add_event(store)


def test_add_map_insertion_failure_rolls_back_and_wipes_private_copy(monkeypatch):
    class FailOnInsert(dict):
        def __setitem__(self, key, value):
            raise MemoryError("injected map failure")

    store = make_store()
    store._markers = FailOnInsert()
    real_stored = otp_relay._StoredEvent
    captured = {}

    def capture_stored(**kwargs):
        captured["buffer"] = kwargs["code"]
        return real_stored(**kwargs)

    monkeypatch.setattr(otp_relay, "_StoredEvent", capture_stored)
    source = bytearray(b"731992")
    with pytest.raises(MemoryError, match="map failure"):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_1,
            target_device=TARGET_A,
            nonce=NONCE_1,
            code=source,
        )

    assert source == bytearray(b"\0" * 6)
    assert captured["buffer"] == bytearray(b"\0" * 6)
    assert store._events == {}
    assert store._markers == {}
    assert store._event_index == {}
    assert store._nonce_index == {}
    assert store._highest_sequence_by_sender == {}
    store._markers = {}
    add_event(store)


def test_target_and_nonce_are_required_without_changing_pending_state():
    store = make_store()
    add_event(store)

    with pytest.raises(TargetMismatch):
        store.claim(
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=store._events[EVENT_1].expires_at,
            event_id=EVENT_1,
            target_device=TARGET_B,
            claim_context=CONTEXT_A,
            nonce=NONCE_1,
        )
    with pytest.raises(ReplayRejected):
        store.claim(
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=store._events[EVENT_1].expires_at,
            event_id=EVENT_1,
            target_device=TARGET_A,
            claim_context=CONTEXT_A,
            nonce=NONCE_2,
        )

    [view] = store.snapshot(target_device=TARGET_A)
    assert view.state is EventState.PENDING


@pytest.mark.parametrize(
    ("event_id", "target_device"),
    [
        ("not-a-uuid", TARGET_A),
        (EVENT_1.upper(), TARGET_A),
        ("11111111-1111-1111-8111-111111111111", TARGET_A),
        (EVENT_1, "731992"),
        (EVENT_1, "OTP:731992"),
        (EVENT_1, "desktop-a"),
        (EVENT_1, "device:11111111-1111-1111-8111-111111111111"),
        (EVENT_1, "desktop with spaces"),
        (EVENT_1, "\u684c\u9762-a"),
    ],
)
def test_event_and_target_metadata_must_be_canonical_opaque_tokens(
    event_id: str,
    target_device: str,
):
    store = make_store()
    source = bytearray(b"731992")
    with pytest.raises(InvalidOtp):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=event_id,
            target_device=target_device,
            nonce=NONCE_1,
            code=source,
        )
    assert source == bytearray(b"\0" * 6)


def test_ttl_blocks_use_without_an_external_sweep_and_exposes_host_deadline():
    clock = FakeClock()
    store = make_store(
        clock=clock,
        max_ttl_seconds=10,
    )
    add_event(store, expires_at_monotonic=105.0)
    stored_buffer = store._events[EVENT_1].code
    claim = claim_event(store)
    assert store.next_deadline_monotonic() == 105.0

    clock.advance(5)
    called = False

    def should_not_run(secret: memoryview) -> None:
        nonlocal called
        called = True

    with pytest.raises(OtpNotFound):
        store.use_secret(claim, CONTEXT_A, should_not_run)
    assert not called
    assert stored_buffer == bytearray(b"\0" * 6)
    assert store.next_deadline_monotonic() is None


def test_host_deadline_tracks_the_earliest_live_event():
    clock = FakeClock()
    store = make_store(
        clock=clock,
        max_ttl_seconds=10,
    )
    add_event(
        store,
        event_id=EVENT_1,
        nonce=NONCE_1,
        sequence=1,
        expires_at_monotonic=105.0,
    )
    add_event(
        store,
        event_id=EVENT_2,
        nonce=NONCE_2,
        sequence=2,
        expires_at_monotonic=109.0,
    )
    assert store.next_deadline_monotonic() == 105.0

    clock.advance(5)
    assert store.expire() == 1
    assert store.next_deadline_monotonic() == 109.0


def test_clock_regression_wipes_active_state_and_permanently_closes_store():
    clock = FakeClock()
    store = make_store(clock=clock)
    add_event(store)
    stored_buffer = store._events[EVENT_1].code
    clock.now -= 1

    with pytest.raises(StoreClosed, match="clock regressed"):
        store.snapshot(target_device=TARGET_A)
    assert store.closed
    assert stored_buffer == bytearray(b"\0" * 6)
    assert store.clear_all() == 0

    rejected = bytearray(b"842061")
    with pytest.raises(StoreClosed):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_2,
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=rejected,
        )
    assert rejected == bytearray(b"\0" * 6)
    with pytest.raises(StoreClosed):
        store.claim(
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=160.0,
            event_id=EVENT_1,
            target_device=TARGET_A,
            claim_context=CONTEXT_A,
            nonce=NONCE_1,
        )


def test_revoke_target_and_clear_all_wipe_without_dropping_replay_markers():
    store = make_store()
    add_event(store, event_id=EVENT_1, target_device=TARGET_A, nonce=NONCE_1)
    add_event(
        store,
        event_id=EVENT_2,
        target_device=TARGET_B,
        nonce=NONCE_2,
        sequence=2,
    )
    revoked_buffer = store._events[EVENT_1].code
    cleared_buffer = store._events[EVENT_2].code

    assert store.revoke_target(TARGET_A) == 1
    assert revoked_buffer == bytearray(b"\0" * 6)
    assert store.snapshot(target_device=TARGET_A) == ()
    assert len(store.snapshot(target_device=TARGET_B)) == 1

    revoked = bytearray(b"111111")
    with pytest.raises(TargetRevoked):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=3,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_3,
            target_device=TARGET_A,
            nonce=b"another-nonce-01",
            code=revoked,
        )
    assert revoked == bytearray(b"\0" * 6)

    assert store.clear_all() == 1
    assert cleared_buffer == bytearray(b"\0" * 6)
    replay = bytearray(b"222222")
    with pytest.raises(ReplayRejected):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=3,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_2,
            target_device=TARGET_B,
            nonce=b"another-nonce-02",
            code=replay,
        )
    assert replay == bytearray(b"\0" * 6)


def test_per_target_capacity_preserves_room_for_another_target():
    store = make_store(
        capacity=3,
        replay_capacity=6,
        per_target_capacity=1,
        per_target_replay_capacity=2,
    )
    add_event(store, event_id=EVENT_1, target_device=TARGET_A, nonce=NONCE_1)

    same_target = bytearray(b"111111")
    with pytest.raises(CapacityExceeded, match="target OTP capacity"):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=2,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_2,
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=same_target,
        )
    assert same_target == bytearray(b"\0" * 6)

    add_event(
        store,
        event_id=EVENT_3,
        target_device=TARGET_B,
        nonce=NONCE_2,
        sequence=2,
    )
    assert len(store.snapshot(target_device=TARGET_B)) == 1


def test_replay_identity_is_bound_to_the_current_session_epoch():
    store_a = make_store(session_epoch=EPOCH_A)
    add_event(store_a)
    claim_a = claim_event(store_a)
    observed = []
    assert store_a.use_secret(
        claim_a,
        CONTEXT_A,
        lambda secret: observed.append(bytes(secret)),
    ) is None
    assert observed == [b"731992"]
    store_a.ack(claim_a)

    replay = bytearray(b"731992")
    with pytest.raises(ReplayRejected):
        store_a.add(
            authenticated_session_epoch=store_a.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=float(store_a._clock()) + 60,
            event_id=EVENT_1,
            target_device=TARGET_A,
            nonce=NONCE_1,
            code=replay,
        )
    assert replay == bytearray(b"\0" * 6)

    store_b = make_store(session_epoch=EPOCH_B)
    add_event(store_b)
    [view_b] = store_b.snapshot(target_device=TARGET_A)
    assert view_b.session_epoch == EPOCH_B
    with pytest.raises(SessionMismatch):
        store_b.dismiss(claim_a)


def test_replay_window_sweep_never_resets_sender_sequence_or_original_expiry():
    clock = FakeClock()
    store = make_store(
        clock=clock,
        max_ttl_seconds=10,
        replay_window_seconds=10,
    )
    original_expiry = 105.0
    add_event(
        store,
        sequence=7,
        expires_at_monotonic=original_expiry,
    )
    claim = claim_event(store)
    store.use_secret(claim, CONTEXT_A, lambda secret: None)
    store.ack(claim)

    clock.advance(11)
    store.expire()
    assert store._markers == {}
    assert store._event_index == {}
    assert store._nonce_index == {}
    assert store._highest_sequence_by_sender == {SENDER_A: 7}

    exact_replay = bytearray(b"731992")
    with pytest.raises(InvalidOtp, match="envelope expired"):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=7,
            authenticated_expires_at_monotonic=original_expiry,
            event_id=EVENT_1,
            target_device=TARGET_A,
            nonce=NONCE_1,
            code=exact_replay,
        )
    assert exact_replay == bytearray(b"\0" * 6)

    rewritten_replay = bytearray(b"731992")
    with pytest.raises(ReplayRejected):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=7,
            authenticated_expires_at_monotonic=clock.now + 5,
            event_id=EVENT_2,
            target_device=TARGET_A,
            nonce=NONCE_2,
            code=rewritten_replay,
        )
    assert rewritten_replay == bytearray(b"\0" * 6)


def test_sender_sequence_is_strict_positive_monotonic_and_sender_isolated():
    store = make_store()
    add_event(store, sender_device=SENDER_A, sequence=10)
    add_event(
        store,
        event_id=EVENT_2,
        sender_device=SENDER_B,
        sequence=10,
        nonce=NONCE_2,
    )
    views = store.snapshot(target_device=TARGET_A)
    assert {(view.sender_device, view.sequence) for view in views} == {
        (SENDER_A, 10),
        (SENDER_B, 10),
    }

    duplicate = bytearray(b"111111")
    with pytest.raises(ReplayRejected):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=10,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_3,
            target_device=TARGET_A,
            nonce=NONCE_3,
            code=duplicate,
        )
    assert duplicate == bytearray(b"\0" * 6)

    old = bytearray(b"222222")
    with pytest.raises(ReplayRejected):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=9,
            authenticated_expires_at_monotonic=float(store._clock()) + 60,
            event_id=EVENT_4,
            target_device=TARGET_A,
            nonce=NONCE_4,
            code=old,
        )
    assert old == bytearray(b"\0" * 6)

    for invalid_sequence in (0, -1, True, 1.0, 2**63):
        rejected = bytearray(b"333333")
        with pytest.raises(InvalidOtp, match="invalid sequence"):
            store.add(
                authenticated_session_epoch=store.session_epoch,
                authenticated_sender_device=SENDER_A,
                authenticated_sequence=invalid_sequence,
                authenticated_expires_at_monotonic=float(store._clock()) + 60,
                event_id=EVENT_3,
                target_device=TARGET_A,
                nonce=NONCE_3,
                code=rejected,
            )
        assert rejected == bytearray(b"\0" * 6)

    for sender in (
        "731992",
        "OTP:731992",
        "phone-a",
        "phone with spaces",
        "device:11111111-1111-1111-8111-111111111111",
    ):
        invalid_sender = bytearray(b"444444")
        with pytest.raises(InvalidOtp, match="invalid sender"):
            store.add(
                authenticated_session_epoch=store.session_epoch,
                authenticated_sender_device=sender,
                authenticated_sequence=11,
                authenticated_expires_at_monotonic=float(store._clock()) + 60,
                event_id=EVENT_3,
                target_device=TARGET_A,
                nonce=NONCE_3,
                code=invalid_sender,
            )
        assert invalid_sender == bytearray(b"\0" * 6)


def test_claim_context_is_strongly_typed_and_canonical():
    with pytest.raises(InvalidOtp, match="sink kind"):
        OtpClaimContext(
            "windows_tsf",
            "30000000-0000-4000-8000-000000000001",
        )
    with pytest.raises(InvalidOtp, match="context token"):
        OtpClaimContext(OtpSinkKind.WINDOWS_TSF, "OTP:731992")


def test_claim_and_marker_bind_the_full_authenticated_identity():
    store = make_store()
    expiry = float(store._clock()) + 60
    add_event(
        store,
        sender_device=SENDER_A,
        sequence=4,
        expires_at_monotonic=expiry,
    )
    [marker] = store._markers.values()
    assert (
        marker.event_id,
        marker.sender_device,
        marker.target_device,
        marker.sequence,
        marker.event_expires_at,
    ) == (EVENT_1, SENDER_A, TARGET_A, 4, expiry)
    assert marker.nonce_digest == otp_relay.OtpRelayStore._nonce_digest(NONCE_1)

    with pytest.raises(SenderMismatch):
        claim_event(store, sender_device=SENDER_B)
    with pytest.raises(ReplayRejected):
        claim_event(store, sequence=3)
    with pytest.raises(ReplayRejected):
        claim_event(store, expires_at_monotonic=expiry - 1)

    claim = claim_event(store)
    assert (claim.sender_device, claim.sequence, claim.expires_at_monotonic) == (
        SENDER_A,
        4,
        expiry,
    )


def test_original_expiry_and_bounded_sender_state_fail_closed_and_wipe():
    clock = FakeClock()
    store = make_store(
        clock=clock,
        capacity=3,
        replay_capacity=4,
        sender_capacity=1,
        max_ttl_seconds=10,
        replay_window_seconds=10,
    )

    for invalid_expiry in (clock.now, clock.now - 1, clock.now + 11):
        rejected = bytearray(b"111111")
        with pytest.raises(InvalidOtp):
            store.add(
                authenticated_session_epoch=store.session_epoch,
                authenticated_sender_device=SENDER_A,
                authenticated_sequence=1,
                authenticated_expires_at_monotonic=invalid_expiry,
                event_id=EVENT_1,
                target_device=TARGET_A,
                nonce=NONCE_1,
                code=rejected,
            )
        assert rejected == bytearray(b"\0" * 6)

    add_event(
        store,
        sender_device=SENDER_A,
        sequence=1,
        expires_at_monotonic=105.0,
    )
    other_sender = bytearray(b"222222")
    with pytest.raises(CapacityExceeded, match="sender capacity"):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_B,
            authenticated_sequence=1,
            authenticated_expires_at_monotonic=105.0,
            event_id=EVENT_2,
            target_device=TARGET_B,
            nonce=NONCE_2,
            code=other_sender,
        )
    assert other_sender == bytearray(b"\0" * 6)
    assert store._highest_sequence_by_sender == {SENDER_A: 1}

    assert store.clear_all() == 1
    add_event(
        store,
        event_id=EVENT_2,
        sender_device=SENDER_A,
        sequence=2,
        nonce=NONCE_2,
        expires_at_monotonic=105.0,
    )
    stored = store._events[EVENT_2].code
    store.close()
    assert stored == bytearray(b"\0" * 6)
    assert store._highest_sequence_by_sender == {}

    after_close = bytearray(b"333333")
    with pytest.raises(StoreClosed):
        store.add(
            authenticated_session_epoch=store.session_epoch,
            authenticated_sender_device=SENDER_A,
            authenticated_sequence=3,
            authenticated_expires_at_monotonic=105.0,
            event_id=EVENT_3,
            target_device=TARGET_A,
            nonce=NONCE_3,
            code=after_close,
        )
    assert after_close == bytearray(b"\0" * 6)


def test_only_one_concurrent_claim_succeeds():
    store = make_store()
    add_event(store)
    workers = 8
    barrier = Barrier(workers)

    def attempt_claim():
        barrier.wait()
        try:
            return claim_event(store)
        except InvalidTransition:
            return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda _: attempt_claim(), range(workers)))

    assert sum(result is not None for result in results) == 1


def test_blocked_callback_does_not_block_snapshot_revoke_or_close():
    store = make_store()
    add_event(store, event_id=EVENT_1, target_device=TARGET_A, nonce=NONCE_1)
    add_event(
        store,
        event_id=EVENT_2,
        target_device=TARGET_B,
        nonce=NONCE_2,
        sequence=2,
    )
    stored_buffer = store._events[EVENT_1].code
    claim = claim_event(store)
    entered = Event()
    release = Event()
    escaped = {}

    def guarded_use():
        def callback(secret: memoryview) -> None:
            escaped["lease"] = secret.obj
            entered.set()
            assert release.wait(timeout=2)

        store.use_secret(claim, CONTEXT_A, callback)

    with ThreadPoolExecutor(max_workers=6) as executor:
        use_future = executor.submit(guarded_use)
        try:
            assert entered.wait(timeout=2)
            assert stored_buffer == bytearray(b"\0" * 6)
            [in_use] = executor.submit(
                store.snapshot,
                target_device=TARGET_A,
            ).result(timeout=0.5)
            assert in_use.state is EventState.IN_USE
            assert len(
                executor.submit(
                    store.snapshot,
                    target_device=TARGET_B,
                ).result(timeout=0.5)
            ) == 1
            assert executor.submit(store.revoke_target, TARGET_B).result(
                timeout=0.5
            ) == 1
            assert executor.submit(store.revoke_target, TARGET_A).result(
                timeout=0.5
            ) == 1
            assert executor.submit(store.close).result(timeout=0.5) is None
            assert store.closed
            assert escaped["lease"] == bytearray(b"731992")
        finally:
            release.set()
        assert use_future.result(timeout=2) is None

    assert escaped["lease"] == bytearray(b"\0" * 6)


def test_callback_can_reenter_snapshot_dismiss_and_close_without_deadlock():
    store = make_store()
    add_event(store)
    claim = claim_event(store)
    escaped = {}

    def callback(secret: memoryview) -> None:
        escaped["lease"] = secret.obj
        [view] = store.snapshot(target_device=TARGET_A)
        assert view.state is EventState.IN_USE
        store.dismiss(claim)
        store.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(store.use_secret, claim, CONTEXT_A, callback).result(
            timeout=1
        ) is None

    assert store.closed
    assert escaped["lease"] == bytearray(b"\0" * 6)


def test_only_one_concurrent_use_can_enter_the_in_use_state():
    store = make_store()
    add_event(store)
    claim = claim_event(store)
    entered = Event()
    release = Event()
    second_called = False

    def first_callback(secret: memoryview) -> None:
        entered.set()
        assert release.wait(timeout=2)

    def second_callback(secret: memoryview) -> None:
        nonlocal second_called
        second_called = True

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(store.use_secret, claim, CONTEXT_A, first_callback)
        try:
            assert entered.wait(timeout=2)
            second = executor.submit(
                store.use_secret,
                claim,
                CONTEXT_A,
                second_callback,
            )
            with pytest.raises(InvalidTransition):
                second.result(timeout=0.5)
        finally:
            release.set()
        assert first.result(timeout=2) is None

    assert not second_called
    [view] = store.snapshot(target_device=TARGET_A)
    assert view.state is EventState.CONSUMED


def test_expire_can_remove_in_use_metadata_while_callback_is_blocked():
    clock = FakeClock()
    store = make_store(
        clock=clock,
        max_ttl_seconds=10,
    )
    add_event(store, expires_at_monotonic=105.0)
    claim = claim_event(store)
    entered = Event()
    release = Event()
    escaped = {}

    def callback(secret: memoryview) -> None:
        escaped["lease"] = secret.obj
        entered.set()
        assert release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        use_future = executor.submit(store.use_secret, claim, CONTEXT_A, callback)
        try:
            assert entered.wait(timeout=2)
            clock.advance(5)
            assert executor.submit(store.expire).result(timeout=0.5) == 1
        finally:
            release.set()
        assert use_future.result(timeout=2) is None

    assert escaped["lease"] == bytearray(b"\0" * 6)
    assert store.snapshot(target_device=TARGET_A) == ()


def test_competing_ack_and_dismiss_allow_exactly_one_terminal_winner():
    store = make_store()
    add_event(store)
    claim = claim_event(store)
    store.use_secret(claim, CONTEXT_A, lambda secret: None)
    barrier = Barrier(2)

    def terminate(action):
        barrier.wait()
        try:
            action(claim)
            return True
        except OtpNotFound:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(terminate, action)
            for action in (store.ack, store.dismiss)
        ]
        outcomes = [future.result() for future in futures]

    assert sum(outcomes) == 1


def test_repr_and_errors_do_not_include_credential_or_metadata_values():
    store = make_store()
    body = b"731992"
    add_event(store, text=body)
    view = store.snapshot(target_device=TARGET_A)[0]
    claim = claim_event(store)

    rendered = " ".join((repr(view), repr(claim)))
    for sensitive_value in (body.decode("ascii"), EVENT_1, TARGET_A, "claim-token"):
        assert sensitive_value not in rendered
    store.use_secret(claim, CONTEXT_A, lambda secret: None)
    with pytest.raises(InvalidTransition) as caught:
        store.use_secret(claim, CONTEXT_A, lambda secret: None)
    assert body.decode("ascii") not in str(caught.value)


def test_relay_core_imports_only_the_standard_library():
    tree = ast.parse(inspect.getsource(otp_relay))
    roots = set()
    forbidden_builtin_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"open", "print", "input", "exec", "eval", "compile"}
        ):
            forbidden_builtin_calls.add(node.func.id)

    assert roots <= {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "math",
        "re",
        "secrets",
        "threading",
        "time",
        "typing",
        "uuid",
    }
    assert forbidden_builtin_calls == set()
