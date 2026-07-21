"""Deterministic gates for the stdlib HTTP request lifecycle.

These tests deliberately keep the deadline algorithm separate from wall-clock
timing.  One small real-socket test covers the user-visible 500 response and
server survival; deadline and shutdown behavior use fake clocks/readers.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
from email.message import Message

import pytest

from clipvault.api import server as api_server


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeSocket:
    def __init__(self) -> None:
        self.shutdown_calls: list[int] = []
        self.timeout = 10.0
        self.timeout_calls: list[float | None] = []

    def shutdown(self, how: int) -> None:
        self.shutdown_calls.append(how)

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, value: float | None) -> None:
        self.timeout = value
        self.timeout_calls.append(value)


class _TrickleReader:
    """Return one small chunk per call and advance a fake monotonic clock."""

    def __init__(
        self,
        clock: _FakeClock,
        *,
        chunks: int = 20,
        seconds_per_chunk: float = 0.26,
    ) -> None:
        self.clock = clock
        self.remaining = chunks
        self.seconds_per_chunk = seconds_per_chunk
        self.calls = 0

    def _next(self, size: int) -> bytes:
        self.calls += 1
        self.clock.advance(self.seconds_per_chunk)
        if self.remaining <= 0:
            return b""
        self.remaining -= 1
        return b"x" * min(1, size)

    def read1(self, size: int) -> bytes:
        return self._next(size)

    def read(self, size: int) -> bytes:
        return self._next(size)


class _NoReadExpected:
    def read1(self, size: int) -> bytes:  # pragma: no cover - failure path
        raise AssertionError("body reader ran after stop was requested")

    def read(self, size: int) -> bytes:  # pragma: no cover - failure path
        raise AssertionError("body reader ran after stop was requested")


def _bare_handler(api, deadline, reader):
    handler_type = api_server.make_handler(
        api,
        read_timeout_s=deadline.timeout_s,
        stop_event=getattr(deadline, "stop_event", None),
    )
    handler = handler_type.__new__(handler_type)
    handler.client_address = ("127.0.0.1", 12345)
    handler.command = "POST"
    handler.path = "/api/pair"
    handler.headers = {"Host": "127.0.0.1", "Content-Length": "0"}
    handler.connection = deadline.connection
    handler.rfile = reader
    handler.close_connection = False
    handler._response_started = False
    handler._request_deadline = deadline
    sent = []
    handler._send_json = lambda code, body: sent.append((code, body))
    return handler, sent


def test_absolute_deadline_is_not_renewed_by_continuous_small_reads():
    clock = _FakeClock()
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        1.0,
        None,
        monotonic=clock,
    )
    reader = _TrickleReader(clock, chunks=20, seconds_per_chunk=0.26)
    handler, sent = _bare_handler(object(), deadline, reader)

    deadline.start()
    try:
        body = handler._read_request_bytes(20)
    finally:
        deadline.finish()

    assert body is None
    assert deadline.reason == "deadline"
    assert reader.calls == 4
    assert clock.value >= 1.0
    assert connection.shutdown_calls == [socket.SHUT_RD]
    assert sent == [
        (
            408,
            {
                "error": {
                    "code": "request_timeout",
                    "message": "request input timed out",
                }
            },
        )
    ]


def test_length_derived_body_budget_is_set_once_and_remains_fixed():
    clock = _FakeClock()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        1.0,
        None,
        monotonic=clock,
    )

    deadline.start()
    try:
        clock.advance(0.9)
        deadline.begin_body(128 * 1024)
        clock.advance(2.9)
        assert deadline.check() is None

        # A second call cannot renew the length-derived deadline.
        deadline.begin_body(7 * 1024 * 1024)
        clock.advance(0.2)
        assert deadline.check() == "deadline"
    finally:
        deadline.finish()


def test_body_budget_cannot_revive_an_expired_header_deadline():
    clock = _FakeClock()
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        1.0,
        None,
        monotonic=clock,
    )

    deadline.start()
    try:
        clock.advance(1.0)
        assert deadline.begin_body(7 * 1024 * 1024) == "deadline"
        assert deadline.reason == "deadline"
        assert connection.shutdown_calls
    finally:
        deadline.finish()


def test_default_maximum_body_budget_is_fixed_at_120_seconds():
    clock = _FakeClock()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=clock,
    )

    deadline.start()
    try:
        assert deadline.begin_body(7 * 1024 * 1024) is None
        clock.advance(119.9)
        assert deadline.check() is None
        clock.advance(0.1)
        assert deadline.check() == "deadline"
    finally:
        deadline.finish()


def test_completed_ingress_disarms_deadline_during_slow_business():
    clock = _FakeClock()
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        1.0,
        None,
        monotonic=clock,
    )

    deadline.start()
    try:
        assert deadline.complete_ingress() is None
        clock.advance(60.0)

        # Business work and response serialization are not request ingress.
        assert deadline.check() is None
        assert deadline.reason is None
        assert connection.shutdown_calls == []
    finally:
        deadline.finish()


def test_runtime_stop_after_ingress_uses_full_duplex_shutdown():
    clock = _FakeClock()
    stop = threading.Event()
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        1.0,
        stop,
        monotonic=clock,
    )

    deadline.start()
    try:
        assert deadline.complete_ingress() is None
        stop.set()

        assert deadline.check() == "stopping"
        assert connection.shutdown_calls == [socket.SHUT_RDWR]
    finally:
        deadline.finish()


def test_runtime_stop_releases_blocked_response_before_watchdog_finishes(
    monkeypatch,
):
    stop = threading.Event()
    ingress_complete = threading.Event()
    response_released = threading.Event()
    watchdogs = []
    results: list[bool] = []
    errors: list[BaseException] = []

    class BlockingResponseSocket(_FakeSocket):
        def shutdown(self, how: int) -> None:
            super().shutdown(how)
            if how == socket.SHUT_RDWR:
                response_released.set()

    connection = BlockingResponseSocket()

    def blocked_response(handler) -> None:
        watchdogs.append(handler._request_deadline)
        results.append(handler._finish_request_ingress())
        ingress_complete.set()
        results.append(response_released.wait(0.5))

    monkeypatch.setattr(
        api_server.BaseHTTPRequestHandler,
        "handle_one_request",
        blocked_response,
    )
    handler_type = api_server.make_handler(
        object(),
        read_timeout_s=1.0,
        stop_event=stop,
    )
    handler = handler_type.__new__(handler_type)
    handler.connection = connection

    def run_handler() -> None:
        try:
            handler.handle_one_request()
        except BaseException as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    thread = threading.Thread(target=run_handler)
    thread.start()
    assert ingress_complete.wait(0.5)

    stop.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert results == [True, True]
    assert connection.shutdown_calls == [socket.SHUT_RDWR]
    assert handler._request_deadline is None
    assert len(watchdogs) == 1
    assert watchdogs[0]._finished.is_set()
    assert not watchdogs[0]._thread.is_alive()


def test_unauthorized_small_push_sends_401_before_bounded_body_drain():
    events: list[str] = []

    class UnauthorizedApi:
        @staticmethod
        def auth_ok(_token):
            return False

    class Reader:
        data = bytearray(b"{}")

        def read1(self, size: int) -> bytes:
            events.append("drain")
            chunk = bytes(self.data[:size])
            del self.data[:size]
            return chunk

        read = read1

    class Writer:
        @staticmethod
        def flush() -> None:
            events.append("flush")

    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    reader = Reader()
    handler, sent = _bare_handler(UnauthorizedApi(), deadline, reader)
    handler.path = "/api/sync/push"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Type": "application/json",
        "Content-Length": "2",
    }
    handler.wfile = Writer()

    def record_response(code, body):
        events.append("response")
        sent.append((code, body))

    handler._send_json = record_response

    handler.do_POST()

    assert sent[0][0] == 401
    assert events == ["response", "flush", "drain"]
    assert reader.data == b""
    assert connection.timeout_calls[0] == pytest.approx(
        api_server._AUTH_REJECT_DRAIN_BUDGET_S
    )
    assert connection.timeout_calls[-1] == 10.0
    assert handler.close_connection is True


def test_unauthorized_partial_small_push_keeps_401_and_restores_timeout():
    class UnauthorizedApi:
        @staticmethod
        def auth_ok(_token):
            return False

    class TimeoutReader:
        @staticmethod
        def read1(_size: int) -> bytes:
            raise TimeoutError("injected bounded drain timeout")

        read = read1

    class Writer:
        @staticmethod
        def flush() -> None:
            pass

    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(
        UnauthorizedApi(),
        deadline,
        TimeoutReader(),
    )
    handler.path = "/api/sync/push"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Type": "application/json",
        "Content-Length": "2",
    }
    handler.wfile = Writer()

    handler.do_POST()

    assert [code for code, _body in sent] == [401]
    assert connection.timeout_calls[0] == pytest.approx(
        api_server._AUTH_REJECT_DRAIN_BUDGET_S
    )
    assert connection.timeout_calls[-1] == 10.0
    assert handler.close_connection is True


def test_unauthorized_reject_drain_has_one_absolute_time_budget(monkeypatch):
    clock = _FakeClock()

    class UnauthorizedApi:
        @staticmethod
        def auth_ok(_token):
            return False

    class TrickleReader:
        def __init__(self) -> None:
            self.calls = 0

        def read1(self, _size: int) -> bytes:
            self.calls += 1
            clock.advance(0.06)
            return b"x"

        read = read1

    class Writer:
        @staticmethod
        def flush() -> None:
            pass

    monkeypatch.setattr(api_server.time, "monotonic", clock)
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        10.0,
        None,
        monotonic=clock,
    )
    reader = TrickleReader()
    handler, sent = _bare_handler(UnauthorizedApi(), deadline, reader)
    handler.path = "/api/sync/push"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Type": "application/json",
        "Content-Length": "10",
    }
    handler.wfile = Writer()

    handler.do_POST()

    assert [code for code, _body in sent] == [401]
    assert reader.calls == 2
    assert clock.value == pytest.approx(0.12)
    assert connection.timeout_calls[0] == pytest.approx(0.1)
    assert connection.timeout_calls[1] == pytest.approx(0.04)
    assert connection.timeout_calls[-1] == 10.0
    assert handler.close_connection is True


@pytest.mark.parametrize(
    "framing",
    ["transfer_encoding", "duplicate_length", "oversized"],
)
def test_unauthorized_ambiguous_or_unsupported_body_is_not_drained(framing):
    class UnauthorizedApi:
        @staticmethod
        def auth_ok(_token):
            return False

    class Writer:
        @staticmethod
        def flush() -> None:
            pass

    headers = Message()
    headers.add_header("Host", "127.0.0.1")
    headers.add_header("Content-Type", "application/json")
    if framing == "transfer_encoding":
        headers.add_header("Content-Length", "2")
        headers.add_header("Transfer-Encoding", "chunked")
    elif framing == "duplicate_length":
        headers.add_header("Content-Length", "2")
        headers.add_header("Content-Length", "2")
    else:
        headers.add_header(
            "Content-Length",
            str(api_server._MAX_SYNC_PUSH_BODY + 1),
        )

    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(
        UnauthorizedApi(),
        deadline,
        _NoReadExpected(),
    )
    handler.path = "/api/sync/push"
    handler.headers = headers
    handler.wfile = Writer()

    handler.do_POST()

    assert [code for code, _body in sent] == [401]
    assert connection.timeout_calls == []
    assert handler.close_connection is True


def test_stop_during_body_drain_prevents_bodyless_route_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    stop = threading.Event()
    stop.set()
    api = DeleteApi()
    connection = _FakeSocket()
    deadline = api_server._RequestDeadline(
        connection,
        10.0,
        stop,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": "4",
    }

    handler.do_DELETE()

    assert deadline.reason == "stopping"
    assert api.deleted == []
    assert sent == []
    assert connection.shutdown_calls


def test_invalid_bodyless_route_length_returns_error_without_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": "not-an-integer",
    }

    handler.do_DELETE()

    assert api.deleted == []
    assert sent == [
        (
            400,
            {
                "error": {
                    "code": "bad_request",
                    "message": "invalid Content-Length",
                }
            },
        )
    ]
    assert handler.close_connection is True


def test_oversized_bodyless_route_returns_413_without_read_or_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": str(api_server._MAX_JSON_BODY + 1),
    }

    handler.do_DELETE()

    assert api.deleted == []
    assert sent == [
        (
            413,
            {
                "error": {
                    "code": "payload_too_large",
                    "message": "request body too large",
                }
            },
        )
    ]
    assert handler.close_connection is True


def test_chunked_bodyless_route_is_rejected_without_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": "0",
        "Transfer-Encoding": "chunked",
    }

    handler.do_DELETE()

    assert api.deleted == []
    assert sent == [
        (
            400,
            {
                "error": {
                    "code": "bad_request",
                    "message": "Transfer-Encoding is not supported",
                }
            },
        )
    ]
    assert handler.close_connection is True


def test_chunked_json_route_is_rejected_before_api_call():
    class PairApi:
        def __init__(self) -> None:
            self.calls = 0

        def pair(self, body):
            self.calls += 1
            return 200, {"paired": True}

    api = PairApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "POST"
    handler.path = "/api/pair"
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": "0",
        "Transfer-Encoding": "chunked",
    }

    handler.do_POST()

    assert api.calls == 0
    assert sent[0][0] == 400
    assert sent[0][1]["error"]["code"] == "bad_request"
    assert handler.close_connection is True


def test_duplicate_transfer_encoding_cannot_hide_chunked_framing():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    headers = Message()
    headers.add_header("Host", "127.0.0.1")
    headers.add_header("Content-Length", "0")
    headers.add_header("Transfer-Encoding", "")
    headers.add_header("Transfer-Encoding", "chunked")
    handler.headers = headers

    handler.do_DELETE()

    assert api.deleted == []
    assert sent[0][0] == 400
    assert handler.close_connection is True


def test_duplicate_content_length_is_rejected_before_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    headers = Message()
    headers.add_header("Host", "127.0.0.1")
    headers.add_header("Content-Length", "0")
    headers.add_header("Content-Length", "4")
    handler.headers = headers

    handler.do_DELETE()

    assert api.deleted == []
    assert sent == [
        (
            400,
            {
                "error": {
                    "code": "bad_request",
                    "message": "ambiguous Content-Length",
                }
            },
        )
    ]
    assert handler.close_connection is True


def test_get_with_transfer_encoding_is_rejected_before_hidden_write():
    class PairCodeApi:
        def __init__(self) -> None:
            self.calls = 0

        def mint_pair_code(self):
            self.calls += 1
            return 200, {"code": "12345678"}

    api = PairCodeApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "GET"
    handler.path = "/api/pair/code"
    handler.headers = {
        "Host": "127.0.0.1",
        "Transfer-Encoding": "chunked",
    }

    handler.do_GET()

    assert api.calls == 0
    assert sent[0][0] == 400
    assert handler.close_connection is True


def test_empty_content_length_is_rejected_before_side_effect():
    class DeleteApi:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete_memory(self, item_id: str):
            self.deleted.append(item_id)
            return 200, {"id": item_id, "deleted": True}

    api = DeleteApi()
    deadline = api_server._RequestDeadline(
        _FakeSocket(),
        10.0,
        None,
        monotonic=_FakeClock(),
    )
    handler, sent = _bare_handler(api, deadline, _NoReadExpected())
    handler.command = "DELETE"
    handler.path = "/api/memory/ABC123"
    headers = Message()
    headers.add_header("Host", "127.0.0.1")
    headers.add_header("Content-Length", "")
    handler.headers = headers

    handler.do_DELETE()

    assert api.deleted == []
    assert sent[0][0] == 400
    assert sent[0][1]["error"]["code"] == "bad_request"
    assert handler.close_connection is True


def _serve_until(httpd, stop: threading.Event) -> None:
    httpd.timeout = 0.05
    try:
        while not stop.is_set():
            httpd.handle_request()
    finally:
        httpd.server_close()


def test_unhandled_api_error_returns_safe_500_and_server_continues(caplog):
    sensitive_marker = "PRIVATE-CLIP-CONTENT-MUST-NOT-LEAK"

    class FlakyApi:
        def __init__(self) -> None:
            self.calls = 0

        def health(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(sensitive_marker)
            return 200, {"status": "ok"}

    stop = threading.Event()
    api = FlakyApi()
    httpd = api_server.build_server(
        api,
        "127.0.0.1",
        0,
        stop_event=stop,
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    caplog.set_level(logging.ERROR, logger="clipvault.api")
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        payload = json.loads(response.read())
        assert response.status == 500
        assert payload == {
            "error": {
                "code": "internal_error",
                "message": "internal server error",
            }
        }
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("Connection") == "close"
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"status": "ok"}
        conn.close()
    finally:
        stop.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert sensitive_marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_slow_api_failure_after_ingress_still_returns_safe_500(caplog):
    sensitive_marker = "SLOW-PRIVATE-ERROR-MUST-NOT-LEAK"

    class SlowApi:
        def health(self):
            threading.Event().wait(0.25)
            raise RuntimeError(sensitive_marker)

    stop = threading.Event()
    httpd = api_server.build_server(
        SlowApi(),
        "127.0.0.1",
        0,
        read_timeout_s=0.1,
        stop_event=stop,
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    caplog.set_level(logging.ERROR, logger="clipvault.api")
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/health")
        response = conn.getresponse()
        assert response.status == 500
        assert json.loads(response.read())["error"]["code"] == "internal_error"
        conn.close()
    finally:
        stop.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert sensitive_marker not in caplog.text
    assert "RuntimeError" in caplog.text


def test_slow_incomplete_headers_are_bounded_and_server_recovers(caplog):
    class HealthApi:
        def health(self):
            return 200, {"status": "ok"}

    stop = threading.Event()
    httpd = api_server.build_server(
        HealthApi(),
        "127.0.0.1",
        0,
        read_timeout_s=0.05,
        stop_event=stop,
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    caplog.set_level(logging.ERROR, logger="clipvault.api")
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.settimeout(1)
            conn.sendall(
                b"GET /api/health HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"X-Slow: "
            )
            # Header parsing may close silently or return a small error, but it
            # must release the only serving thread without an ERROR traceback.
            conn.recv(4096)

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/health")
        health = conn.getresponse()
        assert health.status == 200
        health.read()
        conn.close()
    finally:
        stop.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert caplog.text == ""


def test_malformed_request_target_still_returns_safe_500(caplog):
    sensitive_marker = "PRIVATE-INVALID-TARGET"

    class HealthApi:
        def health(self):
            return 200, {"status": "ok"}

    stop = threading.Event()
    httpd = api_server.build_server(
        HealthApi(),
        "127.0.0.1",
        0,
        stop_event=stop,
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=_serve_until, args=(httpd, stop), daemon=True)
    caplog.set_level(logging.ERROR, logger="clipvault.api")
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.settimeout(2)
            conn.sendall(
                f"GET http://[{sensitive_marker} HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                "Connection: close\r\n\r\n".encode("ascii")
            )
            chunks = []
            while chunk := conn.recv(4096):
                chunks.append(chunk)
            response = b"".join(chunks)
        assert b" 500 " in response.split(b"\r\n", 1)[0]
        assert b"internal_error" in response

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/health")
        health = conn.getresponse()
        assert health.status == 200
        health.read()
        conn.close()
    finally:
        stop.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert sensitive_marker not in caplog.text
    assert "ValueError" in caplog.text


def test_safe_http_server_handle_error_does_not_print_traceback(
    caplog,
    capsys,
):
    sensitive_marker = "PRIVATE-ERROR-DETAIL-MUST-NOT-LEAK"
    httpd = api_server.build_server(object(), "127.0.0.1", 0)
    caplog.set_level(logging.ERROR, logger="clipvault.api")
    try:
        assert isinstance(httpd, api_server._SafeHTTPServer)
        try:
            raise RuntimeError(sensitive_marker)
        except RuntimeError:
            httpd.handle_error(None, ("127.0.0.1", 12345))
    finally:
        httpd.server_close()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert sensitive_marker not in caplog.text
    assert "RuntimeError" in caplog.text
