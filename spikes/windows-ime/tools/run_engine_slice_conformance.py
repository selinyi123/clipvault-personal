#!/usr/bin/env python3
"""Run the local Windows Engine Protocol V2 conformance slice."""

from __future__ import annotations

import unittest

from engine_slice import (
    ConnectionHandshake,
    CompositionSegment,
    EditorOutcome,
    EngineState,
    ErrorCode,
    ErrorResponse,
    ExternalHost,
    FramedProtocolError,
    HostReply,
    InputContext,
    ProjectionDisposition,
    Request,
    RequestKind,
    TsfClientProjection,
    TsfResponseLedger,
    decode_framed_payload,
    encode_framed_payload,
    utf16_units,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class EngineSliceConformanceTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.host = ExternalHost("epoch-a")
        self.client = TsfClientProjection()
        self.client.observe_host("epoch-a")

    def start(
        self,
        session_id: str,
        context: InputContext | None = None,
        *,
        host: ExternalHost | None = None,
        client: TsfClientProjection | None = None,
    ) -> HostReply:
        selected_host = host or self.host
        selected_client = client or self.client
        selected_context = context or InputContext()
        selected_client.begin_session(session_id, selected_context)
        request = Request(
            RequestKind.START,
            selected_host.host_instance_id,
            session_id,
            1,
            context=selected_context,
        )
        reply = selected_host.dispatch(request)
        disposition = selected_client.apply(
            reply,
            lambda _text: self.fail("start response must not edit text"),
        )
        self.assertEqual(ProjectionDisposition.APPLIED, disposition)
        return reply

    def compose(
        self,
        session_id: str,
        *,
        request_seq: int = 2,
        expected_revision: int = 0,
        key: str = "n",
        host: ExternalHost | None = None,
        client: TsfClientProjection | None = None,
    ) -> HostReply:
        selected_host = host or self.host
        selected_client = client or self.client
        reply = selected_host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            selected_host.host_instance_id,
            session_id,
            request_seq,
            expected_revision=expected_revision,
            key=key,
        ))
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            selected_client.apply(
                reply,
                lambda _text: self.fail("composition response must not edit text"),
            ),
        )
        return reply

    def test_framed_protobuf_bounds_fail_closed(self) -> None:
        payload = b"synthetic-protobuf"
        self.assertEqual(payload, decode_framed_payload(encode_framed_payload(payload)))
        for malformed in (
            b"",
            b"\x00\x00\x00",
            b"\x00\x00\x00\x00",
            b"\x00\x00\x00\x05abc",
            b"\x00\x00\x00\x03abcd",
            b"\x00\x10\x00\x01",
        ):
            with self.subTest(frame=malformed):
                with self.assertRaises(FramedProtocolError):
                    decode_framed_payload(malformed)

    def test_connection_requires_fresh_client_then_host_hello(self) -> None:
        first = ConnectionHandshake()
        first.accept("client_to_host", "ClientHello")
        first.accept("host_to_client", "HostHello")
        self.assertTrue(first.ready)
        first.accept_application("client_to_host", "StartSessionRequest")
        first.accept_application("host_to_client", "EngineState")
        with self.assertRaises(FramedProtocolError):
            first.accept("client_to_host", "ClientHello")

        restarted = ConnectionHandshake()
        with self.assertRaises(FramedProtocolError):
            restarted.accept("host_to_client", "HostHello")
        fresh = ConnectionHandshake()
        fresh.accept("client_to_host", "ClientHello")
        fresh.accept("host_to_client", "HostHello")
        self.assertTrue(fresh.ready)

    def test_eng2_v001_candidate_selection_commits_once_and_clears(self) -> None:
        """ENG2-V001: stable opaque selection ID and one successful commit."""
        start_reply = self.start("session-v001")
        self.assertEqual(0, self.host.session_snapshot("session-v001").revision)

        identical_start = self.host.dispatch(Request(
            RequestKind.START,
            "epoch-a",
            "session-v001",
            1,
            context=InputContext(),
        ))
        self.assertTrue(identical_start.cached)
        self.assertEqual(start_reply.wire_bytes, identical_start.wire_bytes)
        self.assertEqual(
            ProjectionDisposition.DUPLICATE,
            self.client.apply(
                identical_start,
                lambda _text: self.fail("duplicate start must not edit text"),
            ),
        )
        conflicting_start = self.host.dispatch(Request(
            RequestKind.START,
            "epoch-a",
            "session-v001",
            1,
            context=InputContext(field_kind="email"),
        ))
        self.assertEqual(ErrorCode.OUT_OF_ORDER_REQUEST, conflicting_start.response.code)
        self.assertFalse(conflicting_start.cached)

        process_reply = self.compose("session-v001")
        state = process_reply.response
        self.assertIsInstance(state, EngineState)
        candidate_id = state.candidates[0].candidate_id
        self.assertRegex(candidate_id, r"^c_[0-9a-f]{16}$")
        self.assertNotIn("拟", candidate_id)
        duplicate_process = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-v001",
            2,
            expected_revision=0,
            key="n",
        ))
        self.assertTrue(duplicate_process.cached)
        self.assertEqual(process_reply.wire_bytes, duplicate_process.wire_bytes)
        self.assertEqual(candidate_id, duplicate_process.response.candidates[0].candidate_id)

        commits: list[str] = []
        select_reply = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v001",
            3,
            expected_revision=1,
            candidate_id=candidate_id,
        ))
        disposition = self.client.apply(
            select_reply,
            lambda text: commits.append(text) or EditorOutcome.APPLIED,
        )
        self.assertEqual(ProjectionDisposition.APPLIED, disposition)
        self.assertEqual(["拟😀"], commits)
        self.assertEqual("", self.client.view_snapshot("session-v001").preedit)
        self.assertEqual((), self.client.view_snapshot("session-v001").candidate_ids)
        self.assertEqual((), self.host.session_snapshot("session-v001").candidate_ids)

    def test_eng2_v002_stale_revision_and_expired_candidate_have_no_effect(self) -> None:
        """ENG2-V002: stale revision/ID fail closed; paging remains vector-covered."""
        self.start("session-v002")
        process_reply = self.compose("session-v002")
        candidate_id = process_reply.response.candidates[0].candidate_id
        commits: list[str] = []

        stale = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v002",
            3,
            expected_revision=0,
            candidate_id=candidate_id,
        ))
        self.assertIsInstance(stale.response, ErrorResponse)
        self.assertEqual(ErrorCode.STALE_REVISION, stale.response.code)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(stale, lambda text: commits.append(text) or EditorOutcome.APPLIED),
        )
        self.assertEqual(1, self.host.session_snapshot("session-v002").revision)

        selected = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v002",
            4,
            expected_revision=1,
            candidate_id=candidate_id,
        ))
        self.client.apply(selected, lambda text: commits.append(text) or EditorOutcome.APPLIED)
        expired = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v002",
            5,
            expected_revision=2,
            candidate_id=candidate_id,
        ))
        self.assertEqual(ErrorCode.INVALID_CANDIDATE, expired.response.code)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(expired, lambda text: commits.append(text) or EditorOutcome.APPLIED),
        )
        self.assertEqual(["拟😀"], commits)

        new_composition = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-v002",
            6,
            expected_revision=2,
            key="n",
        ))
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            self.client.apply(
                new_composition,
                lambda _text: self.fail("composition must not commit"),
            ),
        )
        new_candidate_id = new_composition.response.candidates[0].candidate_id
        self.assertNotEqual(candidate_id, new_candidate_id)
        old_composition_selection = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v002",
            7,
            expected_revision=3,
            candidate_id=candidate_id,
        ))
        self.assertEqual(
            ErrorCode.INVALID_CANDIDATE,
            old_composition_selection.response.code,
        )
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(
                old_composition_selection,
                lambda _text: self.fail("stale composition ID must not commit"),
            ),
        )

    def test_live_invalid_next_sequence_is_cached_and_does_not_deadlock(self) -> None:
        self.start("session-invalid-next")
        unknown_key = Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-invalid-next",
            2,
            expected_revision=0,
            key="unknown",
        )
        first_unknown = self.host.dispatch(unknown_key)
        duplicate_unknown = self.host.dispatch(unknown_key)
        self.assertEqual(ErrorCode.INVALID_ARGUMENT, first_unknown.response.code)
        self.assertTrue(duplicate_unknown.cached)
        self.assertEqual(first_unknown.wire_bytes, duplicate_unknown.wire_bytes)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(first_unknown, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual(
            ProjectionDisposition.DUPLICATE,
            self.client.apply(duplicate_unknown, lambda _text: EditorOutcome.APPLIED),
        )

        valid_after_error = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-invalid-next",
            3,
            expected_revision=0,
            key="z",
        ))
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            self.client.apply(
                valid_after_error,
                lambda _text: self.fail("composition must not commit"),
            ),
        )

        invalid_end = Request(
            RequestKind.END,
            "epoch-a",
            "session-invalid-next",
            4,
            expected_revision=1,
        )
        first_invalid_end = self.host.dispatch(invalid_end)
        duplicate_invalid_end = self.host.dispatch(invalid_end)
        self.assertEqual(ErrorCode.INVALID_ARGUMENT, first_invalid_end.response.code)
        self.assertTrue(duplicate_invalid_end.cached)
        self.assertEqual(first_invalid_end.wire_bytes, duplicate_invalid_end.wire_bytes)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(first_invalid_end, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual(
            ProjectionDisposition.DUPLICATE,
            self.client.apply(duplicate_invalid_end, lambda _text: EditorOutcome.APPLIED),
        )

        valid_after_invalid_end = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-invalid-next",
            5,
            expected_revision=1,
            key="n",
        ))
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            self.client.apply(
                valid_after_invalid_end,
                lambda _text: self.fail("composition must not commit"),
            ),
        )

    def test_fresh_malformed_start_retires_and_blocks_corrected_same_sequence(self) -> None:
        fresh_host = ExternalHost("epoch-fresh-malformed")
        fresh_client = TsfClientProjection()
        fresh_client.observe_host("epoch-fresh-malformed")
        fresh_client.begin_session("fresh-malformed", InputContext())
        fresh_malformed = fresh_host.dispatch(Request(
            RequestKind.START,
            "epoch-fresh-malformed",
            "fresh-malformed",
            1,
            context=None,
        ))
        self.assertEqual(ErrorCode.INVALID_ARGUMENT, fresh_malformed.response.code)
        self.assertTrue(fresh_malformed.response.invalidates_session)
        self.assertIsNone(fresh_host.session_snapshot("fresh-malformed"))
        self.assertEqual(
            ProjectionDisposition.ERROR,
            fresh_client.apply(fresh_malformed, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual((), fresh_client.ledger.metadata())

        corrected_same_seq = fresh_host.dispatch(Request(
            RequestKind.START,
            "epoch-fresh-malformed",
            "fresh-malformed",
            1,
            context=InputContext(),
        ))
        self.assertEqual(ErrorCode.OUT_OF_ORDER_REQUEST, corrected_same_seq.response.code)
        self.assertTrue(corrected_same_seq.response.invalidates_session)
        self.assertIsNone(fresh_host.session_snapshot("fresh-malformed"))

    def test_malformed_start_on_live_session_consumes_and_caches_next_sequence(self) -> None:

        self.start("session-malformed-start")
        malformed = Request(
            RequestKind.START,
            "epoch-a",
            "session-malformed-start",
            2,
            expected_revision=0,
            context=InputContext(),
        )
        first = self.host.dispatch(malformed)
        duplicate = self.host.dispatch(malformed)
        self.assertEqual(ErrorCode.INVALID_ARGUMENT, first.response.code)
        self.assertTrue(duplicate.cached)
        self.assertEqual(first.wire_bytes, duplicate.wire_bytes)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(first, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual(
            ProjectionDisposition.DUPLICATE,
            self.client.apply(duplicate, lambda _text: EditorOutcome.APPLIED),
        )
        continued = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-malformed-start",
            3,
            expected_revision=0,
            key="z",
        ))
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            self.client.apply(
                continued,
                lambda _text: self.fail("composition must not commit"),
            ),
        )

    def test_wrong_host_collision_retires_and_wipes_live_session(self) -> None:
        self.start("session-host-collision")
        self.compose("session-host-collision")
        stale = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "old-epoch",
            "session-host-collision",
            3,
            expected_revision=1,
            key="z",
        ))
        self.assertEqual(ErrorCode.STALE_SESSION, stale.response.code)
        self.assertTrue(stale.response.invalidates_session)
        self.assertIsNone(self.host.session_snapshot("session-host-collision"))
        self.assertEqual(
            ProjectionDisposition.ERROR,
            self.client.apply(stale, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual((), self.client.ledger.metadata())

    def test_forward_request_gap_retires_and_wipes_both_sides(self) -> None:
        self.start("session-forward-gap")
        self.compose("session-forward-gap")
        gap = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "session-forward-gap",
            4,
            expected_revision=1,
            key="z",
        ))
        self.assertEqual(ErrorCode.OUT_OF_ORDER_REQUEST, gap.response.code)
        self.assertTrue(gap.response.invalidates_session)
        self.assertIsNone(self.host.session_snapshot("session-forward-gap"))
        self.assertEqual(
            ProjectionDisposition.RETIRED,
            self.client.apply(gap, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual((), self.client.ledger.metadata())

    def test_expired_consumed_retry_retires_both_sides(self) -> None:
        clock = FakeClock()
        host = ExternalHost(
            "epoch-expired-retry",
            monotonic=clock,
            retry_deadline_seconds=1.0,
        )
        client = TsfClientProjection()
        client.observe_host("epoch-expired-retry")
        self.start("session-expired-retry", host=host, client=client)
        request = Request(
            RequestKind.PROCESS_KEY,
            "epoch-expired-retry",
            "session-expired-retry",
            2,
            expected_revision=0,
            key="z",
        )
        lost_reply = host.dispatch(request)
        self.assertIsInstance(lost_reply.response, EngineState)
        clock.advance(2.0)
        host.expire_response_state()
        retry = host.dispatch(request)
        self.assertEqual(ErrorCode.OUT_OF_ORDER_REQUEST, retry.response.code)
        self.assertTrue(retry.response.invalidates_session)
        self.assertIsNone(host.session_snapshot("session-expired-retry"))
        self.assertEqual(
            ProjectionDisposition.ERROR,
            client.apply(retry, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertEqual((), client.ledger.metadata())

        applied_clock = FakeClock()
        applied_host = ExternalHost(
            "epoch-expired-applied",
            monotonic=applied_clock,
            retry_deadline_seconds=1.0,
        )
        applied_client = TsfClientProjection()
        applied_client.observe_host("epoch-expired-applied")
        self.start("session-expired-applied", host=applied_host, client=applied_client)
        applied_request = Request(
            RequestKind.PROCESS_KEY,
            "epoch-expired-applied",
            "session-expired-applied",
            2,
            expected_revision=0,
            key="z",
        )
        applied_reply = applied_host.dispatch(applied_request)
        self.assertEqual(
            ProjectionDisposition.APPLIED,
            applied_client.apply(
                applied_reply,
                lambda _text: self.fail("composition must not commit"),
            ),
        )
        applied_clock.advance(2.0)
        applied_host.expire_response_state()
        applied_retry = applied_host.dispatch(applied_request)
        self.assertTrue(applied_retry.response.invalidates_session)
        self.assertEqual(
            ProjectionDisposition.RETIRED,
            applied_client.apply(applied_retry, lambda _text: EditorOutcome.APPLIED),
        )
        self.assertIsNone(applied_client.view_snapshot("session-expired-applied"))

    def test_acknowledgement_rejects_unissued_sequence(self) -> None:
        self.start("session-ack-bound")
        self.compose("session-ack-bound")
        before = self.host.session_snapshot("session-ack-bound").cached_response_count
        self.assertFalse(self.host.acknowledge_response(
            "epoch-a",
            "session-ack-bound",
            999,
            locally_authenticated=True,
        ))
        self.assertEqual(
            before,
            self.host.session_snapshot("session-ack-bound").cached_response_count,
        )

        restarted_host = ExternalHost("epoch-ack-old")
        old_client = TsfClientProjection()
        old_client.observe_host("epoch-ack-old")
        self.start("reused-session-id", host=restarted_host, client=old_client)
        restarted_host.restart("epoch-ack-new")
        new_client = TsfClientProjection()
        new_client.observe_host("epoch-ack-new")
        self.start("reused-session-id", host=restarted_host, client=new_client)
        new_cache_count = restarted_host.session_snapshot(
            "reused-session-id"
        ).cached_response_count
        self.assertFalse(restarted_host.acknowledge_response(
            "epoch-ack-old",
            "reused-session-id",
            1,
            locally_authenticated=True,
        ))
        self.assertEqual(
            new_cache_count,
            restarted_host.session_snapshot("reused-session-id").cached_response_count,
        )

    def test_eng2_v003_duplicate_commit_response_projects_at_most_once(self) -> None:
        """ENG2-V003: reserve ack before mutation and suppress cached response."""
        self.start("session-v003")
        candidate_id = self.compose("session-v003").response.candidates[0].candidate_id
        request = Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v003",
            3,
            expected_revision=1,
            candidate_id=candidate_id,
        )
        first = self.host.dispatch(request)
        duplicate = self.host.dispatch(request)
        self.assertTrue(duplicate.cached)
        self.assertEqual(first.wire_bytes, duplicate.wire_bytes)

        commits: list[str] = []

        def editor(text: str) -> EditorOutcome:
            self.assertEqual(3, self.client.ledger.highest_reserved("session-v003"))
            commits.append(text)
            return EditorOutcome.APPLIED

        self.assertEqual(ProjectionDisposition.APPLIED, self.client.apply(first, editor))
        self.assertEqual(ProjectionDisposition.DUPLICATE, self.client.apply(duplicate, editor))
        self.assertEqual(["拟😀"], commits)
        self.assertEqual(1, len(self.client.ledger.metadata()))
        self.assertNotIn("拟", repr(self.client.ledger.metadata()))

        bounded = TsfResponseLedger(max_live_sessions=1)
        bounded.observe_host("epoch-bound")
        bounded.begin_session("only-live-session")
        with self.assertRaises(RuntimeError):
            bounded.begin_session("overflow-session")

        gap_client = TsfClientProjection()
        gap_client.observe_host("epoch-gap")
        gap_client.begin_session("gap-session", InputContext())
        gap_state = EngineState(
            host_instance_id="epoch-gap",
            session_id="gap-session",
            ack_request_seq=2,
            revision=0,
        )
        self.assertEqual(
            ProjectionDisposition.RETIRED,
            gap_client.apply(
                HostReply(gap_state, b"synthetic-gap"),
                lambda _text: self.fail("gapped response must not edit text"),
            ),
        )
        self.assertEqual((), gap_client.ledger.metadata())

    def test_eng2_v004_ambiguous_editor_result_retires_without_replay(self) -> None:
        """ENG2-V004: ambiguous edit retires the typed session."""
        self.start("session-v004")
        candidate_id = self.compose("session-v004").response.candidates[0].candidate_id
        request = Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-v004",
            3,
            expected_revision=1,
            candidate_id=candidate_id,
        )
        first = self.host.dispatch(request)
        duplicate = self.host.dispatch(request)
        editor_calls: list[str] = []

        def ambiguous_editor(text: str) -> EditorOutcome:
            editor_calls.append(text)
            return EditorOutcome.AMBIGUOUS

        self.assertEqual(
            ProjectionDisposition.RETIRED,
            self.client.apply(first, ambiguous_editor),
        )
        self.assertIsNone(self.client.view_snapshot("session-v004"))
        self.assertEqual(
            ProjectionDisposition.RETIRED,
            self.client.apply(duplicate, ambiguous_editor),
        )
        self.assertEqual(["拟😀"], editor_calls)
        with self.assertRaises(ValueError):
            self.client.begin_session("session-v004", InputContext())

    def test_eng2_v005_host_restart_invalidates_old_session_and_candidate(self) -> None:
        """ENG2-V005: a fresh Host epoch clears all client/Host state."""
        self.start("session-before-restart")
        old_reply = self.compose("session-before-restart")
        old_candidate_id = old_reply.response.candidates[0].candidate_id

        self.host.restart("epoch-b")
        self.client.observe_host("epoch-b")
        self.assertEqual((), self.client.ledger.metadata())
        self.assertIsNone(self.client.view_snapshot("session-before-restart"))
        self.assertEqual(
            ProjectionDisposition.REJECTED,
            self.client.apply(old_reply, lambda _text: EditorOutcome.APPLIED),
        )

        stale = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-a",
            "session-before-restart",
            3,
            expected_revision=1,
            candidate_id=old_candidate_id,
        ))
        self.assertEqual(ErrorCode.STALE_SESSION, stale.response.code)

        self.start("session-after-restart")
        invalid_old_id = self.host.dispatch(Request(
            RequestKind.SELECT_CANDIDATE,
            "epoch-b",
            "session-after-restart",
            2,
            expected_revision=0,
            candidate_id=old_candidate_id,
        ))
        self.assertEqual(ErrorCode.INVALID_CANDIDATE, invalid_old_id.response.code)

    def test_eng2_v006_utf16_boundaries_are_validated_fail_closed(self) -> None:
        """ENG2-V006: UTF-16 caret/segments never split a surrogate pair."""
        self.start("session-v006")
        reply = self.compose("session-v006")
        state = reply.response
        self.assertEqual(4, utf16_units(state.preedit))
        self.assertEqual(4, state.caret_utf16)
        self.assertEqual((CompositionSegment(0, 4),), state.segments)

        malformed = EngineState(
            host_instance_id="epoch-a",
            session_id="session-v006",
            ack_request_seq=3,
            revision=2,
            preedit="ni😀",
            caret_utf16=3,
            segments=(CompositionSegment(0, 3), CompositionSegment(3, 4)),
        )
        disposition = self.client.apply(
            HostReply(malformed, b"synthetic-malformed-frame"),
            lambda _text: self.fail("malformed state must not edit text"),
        )
        self.assertEqual(ProjectionDisposition.RETIRED, disposition)

    def test_eng2_v007_password_and_incognito_suppress_clipvault_surface(self) -> None:
        """ENG2-V007: private contexts override permissive caller flags."""
        contexts = {
            "password-session": InputContext(
                field_kind="password",
                learning_allowed=True,
                clipvault_allowed=True,
            ),
            "incognito-session": InputContext(
                incognito=True,
                learning_allowed=True,
                clipvault_allowed=True,
            ),
        }
        for session_id, context in contexts.items():
            with self.subTest(session_id=session_id):
                self.start(session_id, context)
                snapshot = self.host.session_snapshot(session_id)
                self.assertFalse(snapshot.learning_allowed)
                self.assertFalse(snapshot.clipvault_allowed)
                self.assertEqual(
                    (),
                    self.host.visible_clipvault_candidate_ids(
                        session_id,
                        ("cv_opaque_1",),
                    ),
                )
                self.assertEqual(
                    (),
                    self.client.visible_clipvault_candidate_ids(
                        session_id,
                        ("cv_opaque_1",),
                    ),
                )

        private_reply = self.host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-a",
            "password-session",
            2,
            expected_revision=0,
            key="n",
        ))
        self.assertNotIn("ni😀", repr(private_reply))
        self.assertNotIn("拟😀", repr(private_reply))
        self.assertNotIn("ni😀", repr(self.host.diagnostics()))

    def test_eng2_v008_end_is_idempotent_and_cleanup_is_bounded(self) -> None:
        """ENG2-V008: EndSession has no revision gate and leaves no content cache."""
        clock = FakeClock()
        host = ExternalHost(
            "epoch-cleanup",
            monotonic=clock,
            retry_deadline_seconds=2.0,
            max_end_tombstones=1,
        )
        client = TsfClientProjection()
        client.observe_host("epoch-cleanup")
        self.start("cleanup-one", host=host, client=client)
        self.compose("cleanup-one", host=host, client=client)
        self.assertEqual(2, host.session_snapshot("cleanup-one").cached_response_count)
        self.assertFalse(host.acknowledge_response(
            "epoch-cleanup",
            "cleanup-one",
            2,
            locally_authenticated=False,
        ))
        self.assertEqual(2, host.session_snapshot("cleanup-one").cached_response_count)
        self.assertTrue(host.acknowledge_response(
            "epoch-cleanup",
            "cleanup-one",
            2,
            locally_authenticated=True,
        ))
        self.assertEqual(0, host.session_snapshot("cleanup-one").cached_response_count)

        later_state = host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-cleanup",
            "cleanup-one",
            3,
            expected_revision=1,
            key="z",
        ))
        client.apply(later_state, lambda _text: self.fail("composition must not commit"))
        end_request = Request(
            RequestKind.END,
            "epoch-cleanup",
            "cleanup-one",
            4,
        )
        self.assertIsNone(end_request.expected_revision)
        first_end = host.dispatch(end_request)
        duplicate_end = host.dispatch(end_request)
        self.assertTrue(duplicate_end.cached)
        self.assertEqual(first_end.wire_bytes, duplicate_end.wire_bytes)
        self.assertIsNone(host.session_snapshot("cleanup-one"))
        self.assertEqual(
            ProjectionDisposition.SESSION_ENDED,
            client.apply(first_end, lambda _text: self.fail("end must not edit")),
        )
        self.assertEqual(
            ProjectionDisposition.RETIRED,
            client.apply(duplicate_end, lambda _text: self.fail("duplicate end must not edit")),
        )
        self.assertNotIn("ni😀", repr(host.end_tombstone_metadata()))
        self.assertEqual(1, host.diagnostics()["end_tombstone_count"])

        second_client = TsfClientProjection()
        second_client.observe_host("epoch-cleanup")
        self.start("cleanup-two", host=host, client=second_client)
        second_end = host.dispatch(Request(
            RequestKind.END,
            "epoch-cleanup",
            "cleanup-two",
            2,
        ))
        second_client.apply(second_end, lambda _text: self.fail("end must not edit"))
        self.assertEqual((('cleanup-two', 2),), host.end_tombstone_metadata())

        third_client = TsfClientProjection()
        third_client.observe_host("epoch-cleanup")
        self.start("deadline-session", host=host, client=third_client)
        self.assertEqual(1, host.session_snapshot("deadline-session").cached_response_count)
        clock.advance(3.0)
        host.expire_response_state()
        self.assertEqual(0, host.session_snapshot("deadline-session").cached_response_count)
        self.assertEqual(0, host.diagnostics()["end_tombstone_count"])

        capacity_host = ExternalHost(
            "epoch-capacity",
            max_cached_responses_per_session=1,
        )
        capacity_client = TsfClientProjection()
        capacity_client.observe_host("epoch-capacity")
        self.start("capacity-session", host=capacity_host, client=capacity_client)
        unavailable = capacity_host.dispatch(Request(
            RequestKind.PROCESS_KEY,
            "epoch-capacity",
            "capacity-session",
            2,
            expected_revision=0,
            key="n",
        ))
        self.assertEqual(ErrorCode.UNAVAILABLE, unavailable.response.code)
        self.assertTrue(unavailable.response.invalidates_session)
        self.assertEqual(
            ProjectionDisposition.ERROR,
            capacity_client.apply(
                unavailable,
                lambda _text: self.fail("capacity failure must not edit text"),
            ),
        )
        self.assertIsNone(capacity_host.session_snapshot("capacity-session"))
        self.assertEqual((), capacity_client.ledger.metadata())


if __name__ == "__main__":
    unittest.main(verbosity=2)
