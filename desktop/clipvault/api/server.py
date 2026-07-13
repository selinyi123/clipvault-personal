"""HTTP server (stdlib, D-006 + SYNC-2/D-007).

Two trust zones, enforced at the handler regardless of bind address:
  - management + Web UI routes: loopback-only (127.0.0.1), no auth.
  - /api/pair and /api/sync/*: reachable from the LAN, protected by a one-time
    pairing code / bearer token respectively (so paired Android devices can sync).
Plain single-threaded HTTPServer: the SQLite connection lives on the serving
thread and is never crossed (S004 lesson).
"""

import json
import logging
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from clipvault import __version__
from clipvault.api.handlers import Api

log = logging.getLogger("clipvault.api")

WEBUI_DIR = Path(__file__).parent / "webui"
_CLIP_ID_RE = re.compile(r"^/api/clips/([0-9A-Za-z]+)$")
_RELEASE_RE = re.compile(r"^/api/clips/([0-9A-Za-z]+)/release$")
_PROMOTE_RE = re.compile(r"^/api/clips/([0-9A-Za-z]+)/promote$")
_ACTIONS_RE = re.compile(r"^/api/clips/([0-9A-Za-z]+)/actions$")
_MEMORY_ID_RE = re.compile(r"^/api/memory/([0-9A-Za-z]+)$")
_MEMORY_USE_RE = re.compile(r"^/api/memory/([0-9A-Za-z]+)/use$")
_PEER_ID_RE = re.compile(r"^/api/peers/([0-9A-Za-z_-]+)$")  # device ids contain hyphens
_LOOPBACK = ("127.0.0.1", "::1")
# Cap for JSON bodies carrying clip content. A valid 1 MiB clip can require six
# wire bytes per input byte when JSON escapes control characters (for example
# NUL), plus its request/event envelope. Seven MiB covers that proven worst
# case; the real per-clip limit is still enforced in the service layer (422).
_MAX_CONTENT_JSON_BODY = 7 * 1_048_576
_MAX_JSON_BODY = 2 * 1_048_576
_MAX_PAIR_BODY = 4_096
_MAX_SYNC_PUSH_BODY = _MAX_CONTENT_JSON_BODY
_MAX_REJECT_DRAIN = 64 * 1024
_JSON_CONTENT_TYPES = ("application/json", "application/problem+json")
_DEFAULT_SOCKET_READ_TIMEOUT_S = 10.0
_MIN_BODY_READ_RATE_BYTES_S = 64 * 1024
_MAX_BODY_READ_TIMEOUT_S = 120.0
_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "connect-src 'self'; "
    "img-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)


class _RequestDeadline:
    """Interrupt slow ingress under one header and one fixed body deadline.

    The watchdog touches only the socket and Events; API/service/SQLite work
    remains confined to the single HTTP serving thread. A validated body length
    may replace the header budget once; byte progress never extends either
    deadline, so a peer cannot monopolise the thread by dripping bytes.
    """

    def __init__(
        self,
        connection: socket.socket,
        timeout_s: float,
        stop_event: threading.Event | None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        poll_s: float = 0.05,
    ) -> None:
        self.connection = connection
        self.timeout_s = timeout_s
        self.stop_event = stop_event
        self.monotonic = monotonic
        self.poll_s = poll_s
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None
        self._deadline = 0.0
        self._body_started = False
        self._thread: threading.Thread | None = None

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def _wait(self, timeout_s: float) -> bool:
        """Wait for completion; a separate method keeps clock tests deterministic."""

        return self._finished.wait(timeout_s)

    def _abort(self, reason: str) -> None:
        with self._lock:
            if self._finished.is_set() or self._reason is not None:
                return
            self._reason = reason
        self._shutdown_read()

    def _shutdown_read(self) -> None:
        try:
            # Stop inbound reads while preserving the best-effort ability to
            # return a small 408 response on the write side.
            self.connection.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def check(self) -> str | None:
        """Return/trigger the current abort reason without extending time."""

        reason = self.reason
        if reason is not None or self._finished.is_set():
            return reason
        if self.stop_event is not None and self.stop_event.is_set():
            self._abort("stopping")
        elif self._deadline and self.monotonic() >= self._deadline:
            self._abort("deadline")
        return self.reason

    def _run(self) -> None:
        while not self._finished.is_set():
            if self.check() is not None:
                return
            remaining = self._deadline - self.monotonic()
            if self._wait(min(self.poll_s, remaining)):
                return

    def start(self) -> None:
        self._deadline = self.monotonic() + self.timeout_s
        self._thread = threading.Thread(
            target=self._run,
            name="clipvault-api-request-deadline",
            daemon=True,
        )
        self._thread.start()

    def begin_body(self, length: int) -> str | None:
        """Set one length-derived body deadline; byte progress cannot renew it."""

        if length <= 0:
            return self.check()
        should_abort = False
        with self._lock:
            if self._finished.is_set() or self._reason is not None:
                return self._reason
            now = self.monotonic()
            if self.stop_event is not None and self.stop_event.is_set():
                self._reason = "stopping"
                should_abort = True
            elif self._deadline and now >= self._deadline:
                self._reason = "deadline"
                should_abort = True
            elif not self._body_started:
                self._body_started = True
                proportional = self.timeout_s + (
                    length / _MIN_BODY_READ_RATE_BYTES_S
                )
                body_timeout = max(
                    self.timeout_s,
                    min(_MAX_BODY_READ_TIMEOUT_S, proportional),
                )
                self._deadline = now + body_timeout
            reason = self._reason
        if should_abort:
            self._shutdown_read()
        return reason

    def finish(self) -> None:
        with self._lock:
            self._finished.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(self.poll_s * 2, 0.1))


class _SafeHTTPServer(HTTPServer):
    """Suppress stdlib traceback output for handler lifecycle failures."""

    def handle_error(self, request, client_address) -> None:
        exc_type = sys.exc_info()[0]
        error_class = exc_type.__name__ if exc_type is not None else "Exception"
        log.error("api connection failed error=%s", error_class)


def _remote_allowed(route: str) -> bool:
    """Routes a paired LAN device may reach (auth enforced in the handler)."""
    return route == "/api/pair" or route.startswith("/api/sync/")


def _safe_route(raw_target: str) -> str:
    """Extract a query-free route without letting malformed input break logging."""

    try:
        return urlparse(raw_target).path or "unparsed"
    except (TypeError, ValueError):
        return "unparsed"


def make_handler(
    api: Api,
    read_timeout_s: float = _DEFAULT_SOCKET_READ_TIMEOUT_S,
    *,
    stop_event: threading.Event | None = None,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ClipVault/{__version__}"

        def setup(self) -> None:
            super().setup()
            # HTTPServer is intentionally single-threaded so its SQLite
            # connection stays thread-confined. A per-connection timeout keeps
            # one partial LAN request from monopolising that only serving thread.
            self.connection.settimeout(read_timeout_s)
            self._response_started = False
            self._request_deadline: _RequestDeadline | None = None

        def send_response(self, code: int, message: str | None = None) -> None:
            self._response_started = True
            super().send_response(code, message)

        def handle_one_request(self) -> None:
            deadline = _RequestDeadline(
                self.connection, read_timeout_s, stop_event
            )
            self._request_deadline = deadline
            self._response_started = False
            deadline.start()
            try:
                super().handle_one_request()
            except Exception as exc:
                self._handle_unexpected_error(exc)
            finally:
                deadline.finish()
                self._request_deadline = None

        def _handle_unexpected_error(self, exc: Exception) -> None:
            self.close_connection = True
            deadline = self._request_deadline
            if deadline is not None and deadline.check() is not None:
                return
            method = getattr(self, "command", "UNKNOWN")
            path = getattr(self, "path", "")
            route = _safe_route(path)
            log.error(
                "api request failed method=%s route=%s error=%s",
                method,
                route,
                exc.__class__.__name__,
            )
            if self._response_started:
                return
            try:
                self._send_json(500, {
                    "error": {
                        "code": "internal_error",
                        "message": "internal server error",
                    }
                })
            except OSError:
                pass

        def _finish_request_ingress(self) -> bool:
            deadline = self._request_deadline
            if deadline is None:
                return True
            reason = deadline.check()
            if reason is None:
                deadline.finish()
                reason = deadline.reason
            if reason is None:
                return True
            self.close_connection = True
            if reason == "deadline":
                self._send_request_timeout()
            return False

        def _is_loopback(self) -> bool:
            return self.client_address[0] in _LOOPBACK

        def _host_is_local(self) -> bool:
            """DNS-rebinding guard: a site that rebinds its name to 127.0.0.1
            reaches us with a loopback source IP but a foreign Host header.
            Legitimate local access uses a loopback Host."""
            host = self.headers.get("Host", "").strip()
            if host.startswith("[") and "]" in host:        # [::1] or [::1]:port
                name = host[1:host.index("]")]
            elif host.count(":") == 1:                       # host:port
                name = host.rsplit(":", 1)[0]
            else:
                name = host
            return name.lower() in ("127.0.0.1", "localhost", "::1")

        def _referer_ok(self) -> bool:
            """Second DNS-rebinding layer: if a Referer is present it must be
            loopback (a rebinding page's requests carry the attacker's origin).
            Absent is allowed — many legitimate navigations omit Referer, and the
            Host check above is the primary guard."""
            ref = self.headers.get("Referer", "")
            if not ref:
                return True
            return (urlparse(ref).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")

        def _bearer(self) -> str | None:
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h.startswith("Bearer ") else None

        def _send_json(self, code: int, obj) -> None:
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self._send_security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self._send_json(404, {"error": {"code": "not_found", "message": path.name}})
                return
            data = path.read_bytes()
            self.send_response(200)
            self._send_security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_security_headers(self) -> None:
            # The Web UI renders personal clipboard/memory data. Keep the browser
            # locked to first-party static assets/API and prevent embedding.
            self.send_header("Content-Security-Policy", _CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            # Clipboard and memory responses are personal data. Do not leave
            # them in browser or intermediary caches; the local UI is small
            # enough that disabling cache globally is the safer default.
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")

        def _send_request_timeout(self) -> None:
            self.close_connection = True
            if self._response_started:
                return
            try:
                self._send_json(408, {
                    "error": {
                        "code": "request_timeout",
                        "message": "request input timed out",
                    }
                })
            except OSError:
                pass

        def _reject_transfer_encoding(self) -> bool:
            """Reject framing this stdlib server does not decode.

            Treating a chunked write as bodyless would let a route execute its
            side effect while unread bytes remain on the connection.
            """

            get_all = getattr(self.headers, "get_all", None)
            if get_all is None:
                values = (
                    [self.headers.get("Transfer-Encoding")]
                    if "Transfer-Encoding" in self.headers
                    else []
                )
            else:
                values = get_all("Transfer-Encoding") or []
            if not values:
                return False
            self.close_connection = True
            self._send_json(400, {
                "error": {
                    "code": "bad_request",
                    "message": "Transfer-Encoding is not supported",
                }
            })
            return True

        def _content_length(self) -> int | None:
            """Return one valid body length or emit a bounded 400 response."""

            get_all = getattr(self.headers, "get_all", None)
            if get_all is None:
                value = self.headers.get("Content-Length")
                values = [] if value is None else [value]
            else:
                values = get_all("Content-Length") or []
            if len(values) > 1:
                self.close_connection = True
                self._send_json(400, {
                    "error": {
                        "code": "bad_request",
                        "message": "ambiguous Content-Length",
                    }
                })
                return None
            if not values:
                return 0
            value = values[0]
            raw_value = value.strip() if isinstance(value, str) else ""
            if not re.fullmatch(r"[0-9]+", raw_value):
                self.close_connection = True
                self._send_json(400, {
                    "error": {
                        "code": "bad_request",
                        "message": "invalid Content-Length",
                    }
                })
                return None
            try:
                return int(raw_value)
            except ValueError:
                self.close_connection = True
                self._send_json(400, {
                    "error": {
                        "code": "bad_request",
                        "message": "invalid Content-Length",
                    }
                })
                return None

        def _read_request_bytes(self, length: int) -> bytes | None:
            """Read an exact bounded body under the request's fixed deadline."""

            deadline = self._request_deadline
            if length <= 0:
                return b"" if self._finish_request_ingress() else None
            reason = deadline.begin_body(length) if deadline is not None else None
            if reason is not None:
                self.close_connection = True
                if reason == "deadline":
                    self._send_request_timeout()
                return None
            data = bytearray()
            read_chunk = getattr(self.rfile, "read1", self.rfile.read)
            while len(data) < length:
                reason = deadline.check() if deadline is not None else None
                if reason is not None:
                    self.close_connection = True
                    if reason == "deadline":
                        self._send_request_timeout()
                    return None
                try:
                    chunk = read_chunk(min(64 * 1024, length - len(data)))
                except TimeoutError:
                    self._send_request_timeout()
                    return None
                except OSError:
                    reason = deadline.check() if deadline is not None else None
                    self.close_connection = True
                    if reason == "deadline":
                        self._send_request_timeout()
                    return None
                if not chunk:
                    reason = deadline.check() if deadline is not None else None
                    self.close_connection = True
                    if reason == "deadline":
                        self._send_request_timeout()
                    return None
                data.extend(chunk)
                reason = deadline.check() if deadline is not None else None
                if reason is not None:
                    self.close_connection = True
                    if reason == "deadline":
                        self._send_request_timeout()
                    return None
            return bytes(data) if self._finish_request_ingress() else None

        def _body(self, max_bytes: int = _MAX_JSON_BODY) -> dict | None:
            if self._reject_transfer_encoding():
                return None
            length = self._content_length()
            if length is None:
                return None
            if length > max_bytes:
                # Drain small over-limit bodies so Windows clients can receive
                # the 413 response cleanly; never drain unbounded junk.
                if length <= _MAX_REJECT_DRAIN:
                    if self._read_request_bytes(length) is None:
                        return None
                self.close_connection = True
                self._send_json(413, {"error": {"code": "payload_too_large", "message": "request body too large"}})
                return None
            if not length:
                if self._read_request_bytes(0) is None:
                    return None
                return {}
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in _JSON_CONTENT_TYPES:
                if not self._drain(max_bytes):
                    return None
                self._send_json(415, {"error": {"code": "unsupported_media_type", "message": "application/json required"}})
                return None
            try:
                raw = self._read_request_bytes(length)
                if raw is None:
                    return None
                obj = json.loads(raw.decode("utf-8"))
            except TimeoutError:
                self._send_request_timeout()
                return None
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": {"code": "bad_request", "message": "invalid json"}})
                return None
            if not isinstance(obj, dict):
                self._send_json(400, {"error": {"code": "bad_request", "message": "json object required"}})
                return None
            return obj

        def _drain(self, max_bytes: int = _MAX_JSON_BODY) -> bool:
            """Discard an unread request body so leftover bytes do not corrupt the
            next request on a kept-alive connection (bodyless routes ignore it)."""
            if self._reject_transfer_encoding():
                return False
            length = self._content_length()
            if length is None:
                return False
            if length == 0:
                return self._read_request_bytes(0) is not None
            if length > max_bytes:
                self.close_connection = True  # don't read unbounded junk
                self._send_json(413, {
                    "error": {
                        "code": "payload_too_large",
                        "message": "request body too large",
                    }
                })
                return False
            return self._read_request_bytes(length) is not None

        def _accept_bodyless_request(self) -> bool:
            """Validate that a GET has no alternate framing or request body."""

            if self._reject_transfer_encoding():
                return False
            length = self._content_length()
            if length is None:
                return False
            if length:
                self.close_connection = True
                self._send_json(400, {
                    "error": {
                        "code": "bad_request",
                        "message": "request body is not allowed",
                    }
                })
                return False
            return self._finish_request_ingress()

        def _guard(self, route: str) -> bool:
            # Sync/pair are auth-gated in the handler. Everything else is
            # loopback-only AND requires a loopback Host header, so a DNS-rebinding
            # site cannot drive the management API from the user's own browser.
            if _remote_allowed(route):
                return True
            if self._is_loopback() and self._host_is_local() and self._referer_ok():
                return True
            self._send_json(403, {"error": {"code": "forbidden", "message": "loopback only"}})
            return False

        def log_message(self, fmt, *args):  # route through our logger, no content
            method = getattr(self, "command", "UNKNOWN")
            path = getattr(self, "path", "")
            log.info("%s %s", method, _safe_route(path))

        def do_GET(self):
            if not self._accept_bodyless_request():
                return
            parsed = urlparse(self.path)
            route = parsed.path
            if not self._guard(route):
                return
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            if route in ("/", "/index.html"):
                self._send_file(WEBUI_DIR / "index.html", "text/html; charset=utf-8")
            elif route == "/app.js":
                self._send_file(WEBUI_DIR / "app.js", "application/javascript; charset=utf-8")
            elif route == "/style.css":
                self._send_file(WEBUI_DIR / "style.css", "text/css; charset=utf-8")
            elif route == "/api/health":
                self._send_json(*api.health())
            elif route == "/api/clips":
                self._send_json(*api.list_clips(params))
            elif route == "/api/status":
                self._send_json(*api.status())
            elif route == "/api/peers":
                self._send_json(*api.list_peers())
            elif route == "/api/memory":
                self._send_json(*api.list_memory(params))
            elif route == "/api/suggest":
                self._send_json(*api.suggest(params))
            elif route == "/api/pair/code":
                self._send_json(*api.mint_pair_code())
            elif route == "/api/sync/pull":
                self._send_json(*api.sync_pull(self._bearer(), params))
            else:
                m = _ACTIONS_RE.match(route)
                if m:
                    self._send_json(*api.clip_actions(m.group(1)))
                else:
                    self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_POST(self):
            route = urlparse(self.path).path
            if not self._guard(route):
                return
            if route == "/api/clips":
                body = self._body(_MAX_CONTENT_JSON_BODY)
                if body is None:
                    return
                self._send_json(*api.create_clip(body))
                return
            if route == "/api/memory":
                body = self._body()
                if body is None:
                    return
                self._send_json(*api.create_memory(body))
                return
            if route == "/api/pair":
                body = self._body(_MAX_PAIR_BODY)
                if body is None:
                    return
                self._send_json(*api.pair(body))
                return
            if route == "/api/sync/push":
                token = self._bearer()
                if not api.auth_ok(token):
                    # Authentication is decided from headers. Do not block the
                    # single serving thread draining an untrusted body that will
                    # be discarded; the connection is closed after the 401.
                    self.close_connection = True
                    self._send_json(401, {"error": {"code": "unauthorized", "message": "bad token"}})
                    return
                body = self._body(_MAX_SYNC_PUSH_BODY)
                if body is None:
                    return
                self._send_json(*api.sync_push(token, body))
                return
            m = _RELEASE_RE.match(route)
            if m:
                if not self._drain():
                    return
                self._send_json(*api.release_clip(m.group(1)))
                return
            m = _PROMOTE_RE.match(route)
            if m:
                body = self._body()
                if body is None:
                    return
                self._send_json(*api.promote_clip(m.group(1), body))
                return
            m = _MEMORY_USE_RE.match(route)
            if m:
                if not self._drain():
                    return
                self._send_json(*api.use_memory(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_DELETE(self):
            route = urlparse(self.path).path
            if not self._guard(route):
                return
            m = _MEMORY_ID_RE.match(route)
            if m:
                if not self._drain():
                    return
                self._send_json(*api.delete_memory(m.group(1)))
                return
            m = _PEER_ID_RE.match(route)
            if m:
                if not self._drain():
                    return
                self._send_json(*api.unpair(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_PATCH(self):
            route = urlparse(self.path).path
            if not self._guard(route):
                return
            m = _CLIP_ID_RE.match(route)
            if m:
                body = self._body()
                if body is None:
                    return
                self._send_json(*api.patch_clip(m.group(1), body))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

    return Handler


def build_server(
    api: Api,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    read_timeout_s: float = _DEFAULT_SOCKET_READ_TIMEOUT_S,
    stop_event: threading.Event | None = None,
) -> HTTPServer:
    # Binds the configured host so paired LAN devices can sync (SYNC-2). The
    # management API stays loopback-only via the per-route handler guard, so
    # exposing the socket does not expose the unauthenticated endpoints.
    if read_timeout_s <= 0:
        raise ValueError("read_timeout_s must be positive")
    return _SafeHTTPServer(
        (host, port),
        make_handler(api, read_timeout_s, stop_event=stop_event),
    )


def _prepare_database(conn) -> None:
    """Migrate and repair search drift once at the API readiness gate."""

    from clipvault.store import db
    from clipvault.store.clips_repo import ClipsRepo

    db.migrate(conn)
    if ClipsRepo(conn).repair_search_index():
        log.warning("repaired legacy search-index drift")


def serve(
    config,
    stop: threading.Event,
    pairing=None,
    *,
    obsidian_notify=None,
    on_ready=None,
) -> None:
    """Own the DB connection inside this (serving) thread, then loop."""
    from clipvault.service import ClipVaultService
    from clipvault.store import db

    conn = db.connect(config.db_path)
    httpd = None
    try:
        # Idempotent and self-sufficient regardless of caller order.
        # The API readiness gate runs before the runtime starts its clipboard
        # watcher.  Repair legacy schema-8 writer drift once here rather than
        # turning every short-lived capture service into an O(N) index audit.
        _prepare_database(conn)
        api = Api(
            ClipVaultService(conn, config, obsidian_notify=obsidian_notify),
            pairing=pairing,
        )
        httpd = build_server(
            api, config.host, config.port, stop_event=stop
        )
        httpd.timeout = 0.5
        log.info("api listening on %s:%d", config.host, httpd.server_address[1])
        if on_ready is not None:
            on_ready()
        while not stop.is_set():
            httpd.handle_request()
    finally:
        if httpd is not None:
            httpd.server_close()
        conn.close()
