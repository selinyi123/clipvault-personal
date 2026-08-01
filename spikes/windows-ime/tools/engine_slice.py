#!/usr/bin/env python3
"""Transport-neutral Windows IME conformance slice.

This is deliberately not a TSF, COM, Named Pipe, protobuf, or librime runtime.
It models the state that a future native TSF client and external Host must keep
so those implementations can be checked against deterministic local behavior.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Iterable, Mapping


PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 1_048_576


class FramedProtocolError(ValueError):
    """A fail-closed local transport framing or handshake violation."""


class ConnectionHandshake:
    """Per-connection ClientHello -> HostHello gate for protobuf frames."""

    def __init__(self) -> None:
        self._state = "expect-client-hello"

    @property
    def ready(self) -> bool:
        return self._state == "ready"

    def accept(self, direction: str, message_type: str) -> None:
        expected = {
            "expect-client-hello": ("client_to_host", "ClientHello"),
            "expect-host-hello": ("host_to_client", "HostHello"),
        }.get(self._state)
        if expected is None or (direction, message_type) != expected:
            self._state = "closed"
            raise FramedProtocolError("invalid per-connection hello sequence")
        self._state = (
            "expect-host-hello"
            if self._state == "expect-client-hello"
            else "ready"
        )

    def accept_application(self, direction: str, message_type: str) -> None:
        if (
            self._state != "ready"
            or direction not in {"client_to_host", "host_to_client"}
            or message_type in {"ClientHello", "HostHello"}
        ):
            self._state = "closed"
            raise FramedProtocolError("application frame outside a ready connection")


def encode_framed_payload(payload: bytes) -> bytes:
    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise FramedProtocolError("protobuf payload size is outside the frozen bound")
    return struct.pack(">I", len(payload)) + payload


def decode_framed_payload(frame: bytes) -> bytes:
    if len(frame) < 4:
        raise FramedProtocolError("truncated frame length prefix")
    length = struct.unpack(">I", frame[:4])[0]
    if length == 0 or length > MAX_FRAME_BYTES:
        raise FramedProtocolError("protobuf payload size is outside the frozen bound")
    if len(frame) != 4 + length:
        raise FramedProtocolError("truncated or trailing framed payload")
    return frame[4:]


class RequestKind(str, Enum):
    START = "StartSessionRequest"
    PROCESS_KEY = "ProcessKeyRequest"
    SELECT_CANDIDATE = "SelectCandidateRequest"
    END = "EndSessionRequest"


class ErrorCode(str, Enum):
    STALE_SESSION = "ERROR_CODE_STALE_SESSION"
    SESSION_NOT_FOUND = "ERROR_CODE_SESSION_NOT_FOUND"
    STALE_REVISION = "ERROR_CODE_STALE_REVISION"
    OUT_OF_ORDER_REQUEST = "ERROR_CODE_OUT_OF_ORDER_REQUEST"
    INVALID_CANDIDATE = "ERROR_CODE_INVALID_CANDIDATE"
    INVALID_ARGUMENT = "ERROR_CODE_INVALID_ARGUMENT"
    UNAVAILABLE = "ERROR_CODE_UNAVAILABLE"


class EditorOutcome(str, Enum):
    APPLIED = "applied"
    AMBIGUOUS = "ambiguous"


class ProjectionDisposition(str, Enum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    ERROR = "error"
    SESSION_ENDED = "session-ended"
    RETIRED = "retired"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class InputContext:
    field_kind: str = "text"
    incognito: bool = False
    learning_allowed: bool = True
    clipvault_allowed: bool = True

    def privacy_enforced(self) -> "InputContext":
        if self.field_kind == "password" or self.incognito:
            return replace(
                self,
                learning_allowed=False,
                clipvault_allowed=False,
            )
        return self


@dataclass(frozen=True, slots=True)
class Request:
    kind: RequestKind
    host_instance_id: str
    session_id: str
    request_seq: int
    expected_revision: int | None = None
    key: str | None = field(default=None, repr=False)
    candidate_id: str | None = None
    context: InputContext | None = None


@dataclass(frozen=True, slots=True)
class CompositionSegment:
    start_utf16: int
    end_utf16: int
    kind: str = "RAW"


@dataclass(frozen=True, slots=True)
class EngineCandidate:
    candidate_id: str
    text: str = field(repr=False)
    source: str = "ENGINE"


@dataclass(frozen=True, slots=True)
class EngineState:
    host_instance_id: str
    session_id: str
    ack_request_seq: int
    revision: int
    preedit: str = field(default="", repr=False)
    caret_utf16: int = 0
    segments: tuple[CompositionSegment, ...] = ()
    candidates: tuple[EngineCandidate, ...] = ()
    commit_text: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class SessionEnded:
    host_instance_id: str
    session_id: str
    ack_request_seq: int


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    code: ErrorCode
    current_host_instance_id: str
    session_id: str
    ack_request_seq: int
    current_revision: int
    invalidates_session: bool


Response = EngineState | SessionEnded | ErrorResponse


@dataclass(frozen=True, slots=True)
class HostReply:
    response: Response
    wire_bytes: bytes = field(repr=False)
    cached: bool = False


@dataclass(frozen=True, slots=True)
class SyntheticCandidate:
    logical_id: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SyntheticEngineStep:
    preedit: str = field(repr=False)
    candidates: tuple[SyntheticCandidate, ...]


DEFAULT_ENGINE_SCRIPT: Mapping[str, SyntheticEngineStep] = {
    "n": SyntheticEngineStep(
        preedit="ni😀",
        candidates=(
            SyntheticCandidate("ni-primary", "拟😀"),
            SyntheticCandidate("ni-secondary", "例😀"),
        ),
    ),
    "z": SyntheticEngineStep(
        preedit="z",
        candidates=(SyntheticCandidate("z-primary", "z"),),
    ),
}


@dataclass(slots=True, repr=False)
class _CachedExchange:
    request_mac: bytearray = field(repr=False)
    wire_bytes: bytearray = field(repr=False)
    expires_at: float

    def matches(self, request_mac: bytearray) -> bool:
        return hmac.compare_digest(self.request_mac, request_mac)

    def wipe(self) -> None:
        self.request_mac[:] = b"\x00" * len(self.request_mac)
        self.wire_bytes[:] = b"\x00" * len(self.wire_bytes)


@dataclass(slots=True, repr=False)
class _HostSession:
    context: InputContext
    revision: int = 0
    last_request_seq: int = 1
    composition_generation: int = 0
    preedit: str = field(default="", repr=False)
    candidates: dict[str, EngineCandidate] = field(default_factory=dict, repr=False)
    cache: "OrderedDict[int, _CachedExchange]" = field(
        default_factory=OrderedDict,
        repr=False,
    )

    def wipe(self) -> None:
        self.preedit = ""
        self.candidates.clear()
        for exchange in self.cache.values():
            exchange.wipe()
        self.cache.clear()


@dataclass(slots=True, repr=False)
class _EndTombstone:
    request_seq: int
    response_bytes: bytearray = field(repr=False)
    expires_at: float

    def wipe(self) -> None:
        self.response_bytes[:] = b"\x00" * len(self.response_bytes)


@dataclass(slots=True, repr=False)
class _StartFailureTombstone:
    request_seq: int
    request_mac: bytearray = field(repr=False)
    response_bytes: bytearray = field(repr=False)
    expires_at: float

    def matches(self, request_mac: bytearray) -> bool:
        return hmac.compare_digest(self.request_mac, request_mac)

    def wipe(self) -> None:
        self.request_mac[:] = b"\x00" * len(self.request_mac)
        self.response_bytes[:] = b"\x00" * len(self.response_bytes)


@dataclass(frozen=True, slots=True)
class HostSessionSnapshot:
    revision: int
    last_request_seq: int
    candidate_ids: tuple[str, ...]
    learning_allowed: bool
    clipvault_allowed: bool
    cached_response_count: int


def utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_boundaries(value: str) -> set[int]:
    boundaries = {0}
    offset = 0
    for character in value:
        offset += 2 if ord(character) > 0xFFFF else 1
        boundaries.add(offset)
    return boundaries


def _request_mac(request: Request, mac_key: bytearray) -> bytearray:
    context = request.context
    payload = {
        "kind": request.kind.value,
        "host_instance_id": request.host_instance_id,
        "session_id": request.session_id,
        "request_seq": request.request_seq,
        "expected_revision": request.expected_revision,
        "key": request.key,
        "candidate_id": request.candidate_id,
        "context": None if context is None else {
            "field_kind": context.field_kind,
            "incognito": context.incognito,
            "learning_allowed": context.learning_allowed,
            "clipvault_allowed": context.clipvault_allowed,
        },
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return bytearray(hmac.new(mac_key, encoded, hashlib.sha256).digest())


def _response_record(response: Response) -> dict[str, object]:
    if isinstance(response, EngineState):
        payload: dict[str, object] = {
            "host_instance_id": response.host_instance_id,
            "session_id": response.session_id,
            "ack_request_seq": response.ack_request_seq,
            "revision": response.revision,
            "preedit": response.preedit,
            "caret_utf16": response.caret_utf16,
            "segments": [
                {
                    "start_utf16": segment.start_utf16,
                    "end_utf16": segment.end_utf16,
                    "kind": segment.kind,
                }
                for segment in response.segments
            ],
            "candidates": [
                {
                    "candidate_id": candidate.candidate_id,
                    "text": candidate.text,
                    "source": candidate.source,
                }
                for candidate in response.candidates
            ],
        }
        if response.commit_text is not None:
            payload["commit_text"] = response.commit_text
        return {"type": "EngineState", "payload": payload}
    if isinstance(response, SessionEnded):
        return {
            "type": "SessionEnded",
            "payload": {
                "host_instance_id": response.host_instance_id,
                "session_id": response.session_id,
                "ack_request_seq": response.ack_request_seq,
            },
        }
    return {
        "type": "ErrorResponse",
        "payload": {
            "code": response.code.value,
            "current_host_instance_id": response.current_host_instance_id,
            "session_id": response.session_id,
            "ack_request_seq": response.ack_request_seq,
            "current_revision": response.current_revision,
            "invalidates_session": response.invalidates_session,
        },
    }


def _encode_response(response: Response) -> bytes:
    record = {
        "protocol_version": PROTOCOL_VERSION,
        **_response_record(response),
    }
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_response(wire_bytes: bytes) -> Response:
    record = json.loads(wire_bytes.decode("utf-8"))
    payload = record["payload"]
    if record["type"] == "EngineState":
        return EngineState(
            host_instance_id=payload["host_instance_id"],
            session_id=payload["session_id"],
            ack_request_seq=payload["ack_request_seq"],
            revision=payload["revision"],
            preedit=payload["preedit"],
            caret_utf16=payload["caret_utf16"],
            segments=tuple(CompositionSegment(**item) for item in payload["segments"]),
            candidates=tuple(EngineCandidate(**item) for item in payload["candidates"]),
            commit_text=payload.get("commit_text"),
        )
    if record["type"] == "SessionEnded":
        return SessionEnded(**payload)
    return ErrorResponse(code=ErrorCode(payload["code"]), **{
        key: value for key, value in payload.items() if key != "code"
    })


class ExternalHost:
    """Local external-Host state model with no transport or persistence."""

    def __init__(
        self,
        host_instance_id: str,
        *,
        engine_script: Mapping[str, SyntheticEngineStep] = DEFAULT_ENGINE_SCRIPT,
        monotonic: Callable[[], float] = time.monotonic,
        retry_deadline_seconds: float = 2.0,
        max_cached_responses_per_session: int = 16,
        max_end_tombstones: int = 16,
    ) -> None:
        if not host_instance_id:
            raise ValueError("host_instance_id must be non-empty")
        if retry_deadline_seconds <= 0:
            raise ValueError("retry_deadline_seconds must be positive")
        if max_cached_responses_per_session <= 0 or max_end_tombstones <= 0:
            raise ValueError("cache bounds must be positive")
        self.host_instance_id = host_instance_id
        self._engine_script = engine_script
        self._monotonic = monotonic
        self._retry_deadline_seconds = retry_deadline_seconds
        self._max_cached = max_cached_responses_per_session
        self._max_tombstones = max_end_tombstones
        # Per-process random key prevents low-entropy key events from being
        # brute-forced from retained retry metadata. It is rotated on restart.
        self._request_mac_key = bytearray(secrets.token_bytes(32))
        self._sessions: dict[str, _HostSession] = {}
        self._end_tombstones: "OrderedDict[str, _EndTombstone]" = OrderedDict()
        self._start_failure_tombstones: "OrderedDict[str, _StartFailureTombstone]" = (
            OrderedDict()
        )

    def restart(self, new_host_instance_id: str) -> None:
        if not new_host_instance_id or new_host_instance_id == self.host_instance_id:
            raise ValueError("restart requires a fresh non-empty host_instance_id")
        for session in self._sessions.values():
            session.wipe()
        self._sessions.clear()
        for tombstone in self._end_tombstones.values():
            tombstone.wipe()
        self._end_tombstones.clear()
        for tombstone in self._start_failure_tombstones.values():
            tombstone.wipe()
        self._start_failure_tombstones.clear()
        self._request_mac_key[:] = b"\x00" * len(self._request_mac_key)
        self._request_mac_key = bytearray(secrets.token_bytes(32))
        self.host_instance_id = new_host_instance_id

    def dispatch(self, request: Request) -> HostReply:
        self.expire_response_state()
        if request.request_seq <= 0 or not request.session_id:
            return self._error(request, ErrorCode.INVALID_ARGUMENT, 0, False)
        if request.host_instance_id != self.host_instance_id:
            live = self._sessions.get(request.session_id)
            if live is not None:
                return self._retire_with_error(
                    request,
                    live,
                    ErrorCode.STALE_SESSION,
                )
            return self._error(request, ErrorCode.STALE_SESSION, 0, True)

        session = self._sessions.get(request.session_id)
        if session is None:
            if request.kind is RequestKind.START:
                return self._start(request)
            if request.kind is RequestKind.END:
                duplicate = self._duplicate_end(request)
                if duplicate is not None:
                    return duplicate
            return self._error(request, ErrorCode.SESSION_NOT_FOUND, 0, True)

        request_mac = _request_mac(request, self._request_mac_key)
        cached = session.cache.get(request.request_seq)
        if cached is not None:
            if cached.matches(request_mac):
                wire_bytes = bytes(cached.wire_bytes)
                request_mac[:] = b"\x00" * len(request_mac)
                return HostReply(_decode_response(wire_bytes), wire_bytes, cached=True)
            request_mac[:] = b"\x00" * len(request_mac)
            return self._error(
                request,
                ErrorCode.OUT_OF_ORDER_REQUEST,
                session.revision,
                False,
            )
        if request.request_seq <= session.last_request_seq:
            request_mac[:] = b"\x00" * len(request_mac)
            return self._retire_with_error(
                request,
                session,
                ErrorCode.OUT_OF_ORDER_REQUEST,
            )
        if request.request_seq != session.last_request_seq + 1:
            request_mac[:] = b"\x00" * len(request_mac)
            return self._retire_with_error(
                request,
                session,
                ErrorCode.OUT_OF_ORDER_REQUEST,
            )
        if len(session.cache) >= self._max_cached:
            request_mac[:] = b"\x00" * len(request_mac)
            response = self._error(
                request,
                ErrorCode.UNAVAILABLE,
                session.revision,
                True,
            )
            session.wipe()
            del self._sessions[request.session_id]
            return response
        if request.kind is RequestKind.START:
            return self._consume_invalid(request, session, request_mac)
        if request.kind is RequestKind.END:
            if request.expected_revision is not None:
                return self._consume_invalid(request, session, request_mac)
            request_mac[:] = b"\x00" * len(request_mac)
            return self._end(request, session)
        if request.expected_revision != session.revision:
            session.last_request_seq = request.request_seq
            response = self._error_response(
                request,
                ErrorCode.STALE_REVISION,
                session.revision,
                False,
            )
            return self._cache_reply(session, request, response, request_mac)
        if request.kind is RequestKind.PROCESS_KEY:
            return self._process_key(request, session, request_mac)
        if request.kind is RequestKind.SELECT_CANDIDATE:
            return self._select_candidate(request, session, request_mac)
        return self._consume_invalid(request, session, request_mac)

    def acknowledge_response(
        self,
        host_instance_id: str,
        session_id: str,
        ack_request_seq: int,
        *,
        locally_authenticated: bool,
    ) -> bool:
        """Model the cleanup effect, not production pipe authentication."""
        if (
            not locally_authenticated
            or host_instance_id != self.host_instance_id
        ):
            return False
        session = self._sessions.get(session_id)
        if (
            session is None
            or ack_request_seq <= 0
            or ack_request_seq > session.last_request_seq
        ):
            return False
        removed = False
        for request_seq in list(session.cache):
            if request_seq <= ack_request_seq:
                session.cache.pop(request_seq).wipe()
                removed = True
        return removed

    def expire_response_state(self, now: float | None = None) -> None:
        deadline_now = self._monotonic() if now is None else now
        for session in self._sessions.values():
            for request_seq, exchange in list(session.cache.items()):
                if exchange.expires_at <= deadline_now:
                    session.cache.pop(request_seq).wipe()
        for session_id, tombstone in list(self._end_tombstones.items()):
            if tombstone.expires_at <= deadline_now:
                self._end_tombstones.pop(session_id).wipe()
        for session_id, tombstone in list(self._start_failure_tombstones.items()):
            if tombstone.expires_at <= deadline_now:
                self._start_failure_tombstones.pop(session_id).wipe()

    def session_snapshot(self, session_id: str) -> HostSessionSnapshot | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return HostSessionSnapshot(
            revision=session.revision,
            last_request_seq=session.last_request_seq,
            candidate_ids=tuple(session.candidates),
            learning_allowed=session.context.learning_allowed,
            clipvault_allowed=session.context.clipvault_allowed,
            cached_response_count=len(session.cache),
        )

    def visible_clipvault_candidate_ids(
        self,
        session_id: str,
        opaque_snapshot_ids: Iterable[str],
    ) -> tuple[str, ...]:
        session = self._sessions.get(session_id)
        if session is None or not session.context.clipvault_allowed:
            return ()
        return tuple(opaque_snapshot_ids)

    def diagnostics(self) -> dict[str, int | str]:
        return {
            "host_instance_id": self.host_instance_id,
            "live_session_count": len(self._sessions),
            "cached_response_count": sum(
                len(session.cache) for session in self._sessions.values()
            ),
            "end_tombstone_count": len(self._end_tombstones),
            "start_failure_tombstone_count": len(self._start_failure_tombstones),
        }

    def end_tombstone_metadata(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (session_id, tombstone.request_seq)
            for session_id, tombstone in self._end_tombstones.items()
        )

    def _start(self, request: Request) -> HostReply:
        retired = self._start_failure_tombstones.get(request.session_id)
        if retired is not None:
            request_mac = _request_mac(request, self._request_mac_key)
            if (
                request.request_seq == retired.request_seq
                and retired.matches(request_mac)
            ):
                wire_bytes = bytes(retired.response_bytes)
                request_mac[:] = b"\x00" * len(request_mac)
                return HostReply(_decode_response(wire_bytes), wire_bytes, cached=True)
            request_mac[:] = b"\x00" * len(request_mac)
            return self._error(
                request,
                ErrorCode.OUT_OF_ORDER_REQUEST,
                0,
                True,
            )
        if (
            request.request_seq != 1
            or request.context is None
            or request.expected_revision is not None
            or request.key is not None
            or request.candidate_id is not None
        ):
            return self._reject_fresh_start(request)
        existing = self._sessions.get(request.session_id)
        request_mac = _request_mac(request, self._request_mac_key)
        if existing is not None:
            cached = existing.cache.get(1)
            if cached is not None and cached.matches(request_mac):
                wire_bytes = bytes(cached.wire_bytes)
                request_mac[:] = b"\x00" * len(request_mac)
                return HostReply(_decode_response(wire_bytes), wire_bytes, cached=True)
            request_mac[:] = b"\x00" * len(request_mac)
            return self._error(request, ErrorCode.OUT_OF_ORDER_REQUEST, existing.revision, False)
        if request.session_id in self._end_tombstones:
            request_mac[:] = b"\x00" * len(request_mac)
            return self._error(request, ErrorCode.SESSION_NOT_FOUND, 0, True)

        session = _HostSession(context=request.context.privacy_enforced())
        self._sessions[request.session_id] = session
        response = EngineState(
            host_instance_id=self.host_instance_id,
            session_id=request.session_id,
            ack_request_seq=1,
            revision=0,
        )
        return self._cache_reply(session, request, response, request_mac)

    def _reject_fresh_start(self, request: Request) -> HostReply:
        request_mac = _request_mac(request, self._request_mac_key)
        response = self._error_response(
            request,
            ErrorCode.INVALID_ARGUMENT,
            0,
            True,
        )
        wire_bytes = _encode_response(response)
        self._start_failure_tombstones[request.session_id] = _StartFailureTombstone(
            request_seq=request.request_seq,
            request_mac=request_mac,
            response_bytes=bytearray(wire_bytes),
            expires_at=self._monotonic() + self._retry_deadline_seconds,
        )
        while len(self._start_failure_tombstones) > self._max_tombstones:
            self._start_failure_tombstones.popitem(last=False)[1].wipe()
        return HostReply(response, wire_bytes)

    def _process_key(
        self,
        request: Request,
        session: _HostSession,
        request_mac: bytearray,
    ) -> HostReply:
        if request.key is None or request.context is not None or request.candidate_id is not None:
            return self._consume_invalid(request, session, request_mac)
        step = self._engine_script.get(request.key)
        if step is None:
            return self._consume_invalid(request, session, request_mac)
        if not session.preedit:
            session.composition_generation += 1
        session.revision += 1
        session.last_request_seq = request.request_seq
        session.preedit = step.preedit
        session.candidates = {
            self._stable_candidate_id(
                request.session_id, session.composition_generation, candidate.logical_id
            ):
                EngineCandidate(
                    self._stable_candidate_id(
                        request.session_id, session.composition_generation, candidate.logical_id
                    ),
                    candidate.text,
                )
            for candidate in step.candidates
        }
        response = EngineState(
            host_instance_id=self.host_instance_id,
            session_id=request.session_id,
            ack_request_seq=request.request_seq,
            revision=session.revision,
            preedit=session.preedit,
            caret_utf16=utf16_units(session.preedit),
            segments=(CompositionSegment(0, utf16_units(session.preedit)),),
            candidates=tuple(session.candidates.values()),
        )
        return self._cache_reply(session, request, response, request_mac)

    def _select_candidate(
        self,
        request: Request,
        session: _HostSession,
        request_mac: bytearray,
    ) -> HostReply:
        if request.candidate_id is None or request.key is not None or request.context is not None:
            return self._consume_invalid(request, session, request_mac)
        candidate = session.candidates.get(request.candidate_id)
        session.last_request_seq = request.request_seq
        if candidate is None:
            response = self._error_response(
                request,
                ErrorCode.INVALID_CANDIDATE,
                session.revision,
                False,
            )
            return self._cache_reply(session, request, response, request_mac)
        session.revision += 1
        session.preedit = ""
        session.candidates.clear()
        response = EngineState(
            host_instance_id=self.host_instance_id,
            session_id=request.session_id,
            ack_request_seq=request.request_seq,
            revision=session.revision,
            commit_text=candidate.text,
        )
        return self._cache_reply(session, request, response, request_mac)

    def _end(
        self,
        request: Request,
        session: _HostSession,
    ) -> HostReply:
        response = SessionEnded(
            host_instance_id=self.host_instance_id,
            session_id=request.session_id,
            ack_request_seq=request.request_seq,
        )
        wire_bytes = _encode_response(response)
        session.wipe()
        del self._sessions[request.session_id]
        self._end_tombstones[request.session_id] = _EndTombstone(
            request_seq=request.request_seq,
            response_bytes=bytearray(wire_bytes),
            expires_at=self._monotonic() + self._retry_deadline_seconds,
        )
        while len(self._end_tombstones) > self._max_tombstones:
            self._end_tombstones.popitem(last=False)[1].wipe()
        return HostReply(response, wire_bytes)

    def _duplicate_end(self, request: Request) -> HostReply | None:
        tombstone = self._end_tombstones.get(request.session_id)
        if (
            tombstone is None
            or request.expected_revision is not None
            or request.request_seq != tombstone.request_seq
            or request.kind is not RequestKind.END
            or request.key is not None
            or request.candidate_id is not None
            or request.context is not None
        ):
            return None
        wire_bytes = bytes(tombstone.response_bytes)
        return HostReply(
            _decode_response(wire_bytes),
            wire_bytes,
            cached=True,
        )

    def _cache_reply(
        self,
        session: _HostSession,
        request: Request,
        response: Response,
        request_mac: bytearray,
    ) -> HostReply:
        wire_bytes = _encode_response(response)
        session.cache[request.request_seq] = _CachedExchange(
            request_mac=request_mac,
            wire_bytes=bytearray(wire_bytes),
            expires_at=self._monotonic() + self._retry_deadline_seconds,
        )
        return HostReply(response, wire_bytes)

    def _consume_invalid(
        self,
        request: Request,
        session: _HostSession,
        request_mac: bytearray,
    ) -> HostReply:
        """Consume/cache a valid next sequence even when its payload is invalid."""
        session.last_request_seq = request.request_seq
        response = self._error_response(
            request,
            ErrorCode.INVALID_ARGUMENT,
            session.revision,
            False,
        )
        return self._cache_reply(session, request, response, request_mac)

    def _retire_with_error(
        self,
        request: Request,
        session: _HostSession,
        code: ErrorCode,
    ) -> HostReply:
        response = self._error_response(
            request,
            code,
            session.revision,
            True,
        )
        reply = HostReply(response, _encode_response(response))
        session.wipe()
        if self._sessions.get(request.session_id) is session:
            del self._sessions[request.session_id]
        return reply

    def _error(
        self,
        request: Request,
        code: ErrorCode,
        current_revision: int,
        invalidates_session: bool,
    ) -> HostReply:
        response = self._error_response(
            request,
            code,
            current_revision,
            invalidates_session,
        )
        return HostReply(response, _encode_response(response))

    def _error_response(
        self,
        request: Request,
        code: ErrorCode,
        current_revision: int,
        invalidates_session: bool,
    ) -> ErrorResponse:
        return ErrorResponse(
            code=code,
            current_host_instance_id=self.host_instance_id,
            session_id=request.session_id,
            ack_request_seq=request.request_seq,
            current_revision=current_revision,
            invalidates_session=invalidates_session,
        )

    def _stable_candidate_id(
        self,
        session_id: str,
        composition_generation: int,
        logical_id: str,
    ) -> str:
        material = "\x00".join((
            self.host_instance_id,
            session_id,
            str(composition_generation),
            logical_id,
        ))
        return "c_" + hmac.new(
            self._request_mac_key,
            material.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:16]


@dataclass(slots=True)
class _LedgerEntry:
    host_instance_id: str
    session_id: str
    highest_reserved_ack: int = 0


class TsfResponseLedger:
    """Bounded, typed, content-free applied-response state."""

    def __init__(self, *, max_live_sessions: int = 8, max_tombstones: int = 16) -> None:
        if max_live_sessions <= 0 or max_tombstones <= 0:
            raise ValueError("ledger bounds must be positive")
        self.current_host_instance_id: str | None = None
        self._max_live_sessions = max_live_sessions
        self._max_tombstones = max_tombstones
        self._entries: "OrderedDict[str, _LedgerEntry]" = OrderedDict()
        self._retired: "OrderedDict[tuple[str, str], str]" = OrderedDict()

    def observe_host(self, host_instance_id: str) -> None:
        if not host_instance_id:
            raise ValueError("host_instance_id must be non-empty")
        if host_instance_id != self.current_host_instance_id:
            self._entries.clear()
            self._retired.clear()
            self.current_host_instance_id = host_instance_id

    def begin_session(self, session_id: str) -> None:
        if self.current_host_instance_id is None or not session_id:
            raise ValueError("host and session must be established")
        key = (self.current_host_instance_id, session_id)
        if session_id in self._entries or key in self._retired:
            raise ValueError("session_id must be fresh for the host epoch")
        if len(self._entries) >= self._max_live_sessions:
            raise RuntimeError("TSF ledger live-session bound reached")
        self._entries[session_id] = _LedgerEntry(
            self.current_host_instance_id,
            session_id,
        )

    def reserve(self, host_instance_id: str, session_id: str, ack_request_seq: int) -> bool | None:
        if host_instance_id != self.current_host_instance_id:
            return None
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        if ack_request_seq <= entry.highest_reserved_ack:
            return False
        if ack_request_seq != entry.highest_reserved_ack + 1:
            return None
        entry.highest_reserved_ack = ack_request_seq
        return True

    def retire(self, session_id: str, reason: str) -> None:
        entry = self._entries.pop(session_id, None)
        if entry is None:
            return
        self._retired[(entry.host_instance_id, session_id)] = reason
        while len(self._retired) > self._max_tombstones:
            self._retired.popitem(last=False)

    def is_retired(self, host_instance_id: str, session_id: str) -> bool:
        return (host_instance_id, session_id) in self._retired

    def highest_reserved(self, session_id: str) -> int | None:
        entry = self._entries.get(session_id)
        return None if entry is None else entry.highest_reserved_ack

    def metadata(self) -> tuple[tuple[str, str, int], ...]:
        return tuple(
            (entry.host_instance_id, entry.session_id, entry.highest_reserved_ack)
            for entry in self._entries.values()
        )


@dataclass(slots=True, repr=False)
class _ClientView:
    context: InputContext
    revision: int | None = None
    preedit: str = field(default="", repr=False)
    candidate_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClientViewSnapshot:
    revision: int | None
    preedit: str = field(repr=False)
    candidate_ids: tuple[str, ...]


class TsfClientProjection:
    """Conformance-only TSF-side response projection around the ledger."""

    def __init__(self, *, ledger: TsfResponseLedger | None = None) -> None:
        self.ledger = ledger or TsfResponseLedger()
        self._views: dict[str, _ClientView] = {}

    def observe_host(self, host_instance_id: str) -> None:
        if host_instance_id != self.ledger.current_host_instance_id:
            self._views.clear()
        self.ledger.observe_host(host_instance_id)

    def begin_session(self, session_id: str, context: InputContext) -> None:
        self.ledger.begin_session(session_id)
        self._views[session_id] = _ClientView(context.privacy_enforced())

    def apply(
        self,
        reply: HostReply,
        editor: Callable[[str], EditorOutcome],
    ) -> ProjectionDisposition:
        response = reply.response
        if isinstance(response, ErrorResponse):
            if response.current_host_instance_id != self.ledger.current_host_instance_id:
                return ProjectionDisposition.REJECTED
            reserved = self.ledger.reserve(
                response.current_host_instance_id,
                response.session_id,
                response.ack_request_seq,
            )
            if reserved is False:
                if response.invalidates_session:
                    self._retire(response.session_id, "duplicate-invalidating-error")
                    return ProjectionDisposition.RETIRED
                return ProjectionDisposition.DUPLICATE
            if reserved is None:
                self._retire(response.session_id, "response-sequence-gap")
                return ProjectionDisposition.RETIRED
            if response.invalidates_session:
                self._retire(response.session_id, "host-error")
            return ProjectionDisposition.ERROR

        host_id = response.host_instance_id
        session_id = response.session_id
        view = self._views.get(session_id)
        if host_id != self.ledger.current_host_instance_id or view is None:
            if self.ledger.is_retired(host_id, session_id):
                return ProjectionDisposition.RETIRED
            return ProjectionDisposition.REJECTED

        reserved = self.ledger.reserve(host_id, session_id, response.ack_request_seq)
        if reserved is False:
            return ProjectionDisposition.DUPLICATE
        if reserved is None:
            self._retire(session_id, "response-sequence-gap")
            return ProjectionDisposition.RETIRED

        if isinstance(response, SessionEnded):
            self._retire(session_id, "ended")
            return ProjectionDisposition.SESSION_ENDED

        if not self._valid_state_transition(view, response):
            self._retire(session_id, "invalid-response")
            return ProjectionDisposition.RETIRED

        if response.commit_text is not None:
            try:
                outcome = editor(response.commit_text)
            except Exception:
                outcome = EditorOutcome.AMBIGUOUS
            if outcome is not EditorOutcome.APPLIED:
                self._retire(session_id, "ambiguous-editor-result")
                return ProjectionDisposition.RETIRED

        view.revision = response.revision
        view.preedit = response.preedit
        view.candidate_ids = tuple(
            candidate.candidate_id for candidate in response.candidates
        )
        return ProjectionDisposition.APPLIED

    def visible_clipvault_candidate_ids(
        self,
        session_id: str,
        opaque_snapshot_ids: Iterable[str],
    ) -> tuple[str, ...]:
        view = self._views.get(session_id)
        if view is None or not view.context.clipvault_allowed:
            return ()
        return tuple(opaque_snapshot_ids)

    def view_snapshot(self, session_id: str) -> ClientViewSnapshot | None:
        view = self._views.get(session_id)
        if view is None:
            return None
        return ClientViewSnapshot(view.revision, view.preedit, view.candidate_ids)

    def _retire(self, session_id: str, reason: str) -> None:
        self._views.pop(session_id, None)
        self.ledger.retire(session_id, reason)

    @staticmethod
    def _valid_state_transition(view: _ClientView, response: EngineState) -> bool:
        if view.revision is None:
            if response.revision != 0:
                return False
        elif response.revision != view.revision + 1:
            return False

        boundaries = _utf16_boundaries(response.preedit)
        if response.caret_utf16 not in boundaries:
            return False
        previous_end = 0
        for segment in response.segments:
            if (
                segment.kind not in {"RAW", "CONVERTED", "SELECTED"}
                or segment.start_utf16 != previous_end
                or segment.start_utf16 not in boundaries
                or segment.end_utf16 not in boundaries
                or segment.start_utf16 >= segment.end_utf16
            ):
                return False
            previous_end = segment.end_utf16
        if response.preedit:
            if not response.segments or previous_end != utf16_units(response.preedit):
                return False
        elif response.segments or response.caret_utf16 != 0:
            return False
        return all(candidate.source == "ENGINE" for candidate in response.candidates)
