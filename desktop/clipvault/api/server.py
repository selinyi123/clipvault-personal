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
import threading
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


def _remote_allowed(route: str) -> bool:
    """Routes a paired LAN device may reach (auth enforced in the handler)."""
    return route == "/api/pair" or route.startswith("/api/sync/")


def make_handler(api: Api, read_timeout_s: float = _DEFAULT_SOCKET_READ_TIMEOUT_S):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ClipVault/{__version__}"

        def setup(self) -> None:
            super().setup()
            # HTTPServer is intentionally single-threaded so its SQLite
            # connection stays thread-confined. A per-connection timeout keeps
            # one partial LAN request from monopolising that only serving thread.
            self.connection.settimeout(read_timeout_s)

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

        def _body(self, max_bytes: int = _MAX_JSON_BODY) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                self._send_json(400, {"error": {"code": "bad_request", "message": "invalid Content-Length"}})
                return None
            if length < 0:
                self._send_json(400, {"error": {"code": "bad_request", "message": "invalid Content-Length"}})
                return None
            if length > max_bytes:
                # Drain small over-limit bodies so Windows clients can receive
                # the 413 response cleanly; never drain unbounded junk.
                if length <= _MAX_REJECT_DRAIN:
                    try:
                        self.rfile.read(length)
                    except Exception:
                        self.close_connection = True
                self.close_connection = True
                self._send_json(413, {"error": {"code": "payload_too_large", "message": "request body too large"}})
                return None
            if not length:
                return {}
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in _JSON_CONTENT_TYPES:
                self._drain(max_bytes)
                self._send_json(415, {"error": {"code": "unsupported_media_type", "message": "application/json required"}})
                return None
            try:
                obj = json.loads(self.rfile.read(length).decode("utf-8"))
            except TimeoutError:
                self.close_connection = True
                try:
                    self._send_json(408, {
                        "error": {
                            "code": "request_timeout",
                            "message": "request body timed out",
                        }
                    })
                except OSError:
                    # A peer that stopped reading may also make the timeout
                    # response unwritable; closing the socket is sufficient.
                    pass
                return None
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": {"code": "bad_request", "message": "invalid json"}})
                return None
            if not isinstance(obj, dict):
                self._send_json(400, {"error": {"code": "bad_request", "message": "json object required"}})
                return None
            return obj

        def _drain(self, max_bytes: int = _MAX_JSON_BODY) -> None:
            """Discard an unread request body so leftover bytes do not corrupt the
            next request on a kept-alive connection (bodyless routes ignore it)."""
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                self.close_connection = True
                return
            if length <= 0:
                return
            if length > max_bytes:
                self.close_connection = True  # don't read unbounded junk
                return
            try:
                self.rfile.read(length)
            except Exception:
                self.close_connection = True

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
            log.info("%s %s", self.command, urlparse(self.path).path)

        def do_GET(self):
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
                self._drain()
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
                self._drain()
                self._send_json(*api.use_memory(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_DELETE(self):
            route = urlparse(self.path).path
            if not self._guard(route):
                return
            m = _MEMORY_ID_RE.match(route)
            if m:
                self._drain()
                self._send_json(*api.delete_memory(m.group(1)))
                return
            m = _PEER_ID_RE.match(route)
            if m:
                self._drain()
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
) -> HTTPServer:
    # Binds the configured host so paired LAN devices can sync (SYNC-2). The
    # management API stays loopback-only via the per-route handler guard, so
    # exposing the socket does not expose the unauthenticated endpoints.
    if read_timeout_s <= 0:
        raise ValueError("read_timeout_s must be positive")
    return HTTPServer((host, port), make_handler(api, read_timeout_s))


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
        db.migrate(conn)  # idempotent; self-sufficient regardless of caller order
        api = Api(
            ClipVaultService(conn, config, obsidian_notify=obsidian_notify),
            pairing=pairing,
        )
        httpd = build_server(api, config.host, config.port)
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
