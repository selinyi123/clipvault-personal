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
_LOOPBACK = ("127.0.0.1", "::1")
_MAX_JSON_BODY = 1_048_576
_MAX_PAIR_BODY = 4_096
_MAX_SYNC_PUSH_BODY = 4 * 1_048_576


def _remote_allowed(route: str) -> bool:
    """Routes a paired LAN device may reach (auth enforced in the handler)."""
    return route == "/api/pair" or route.startswith("/api/sync/")


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ClipVault/{__version__}"

        def _is_loopback(self) -> bool:
            return self.client_address[0] in _LOOPBACK

        def _bearer(self) -> str | None:
            h = self.headers.get("Authorization", "")
            return h[7:].strip() if h.startswith("Bearer ") else None

        def _send_json(self, code: int, obj) -> None:
            payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_file(self, path: Path, content_type: str) -> None:
            if not path.exists():
                self._send_json(404, {"error": {"code": "not_found", "message": path.name}})
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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
                self.close_connection = True
                self._send_json(413, {"error": {"code": "payload_too_large", "message": "request body too large"}})
                return None
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"error": {"code": "bad_request", "message": "invalid json"}})
                return None

        def _guard(self, route: str) -> bool:
            # Sync/pair are auth-gated in the handler; everything else is loopback-only.
            if _remote_allowed(route) or self._is_loopback():
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
                body = self._body()
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
                self._send_json(*api.use_memory(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_DELETE(self):
            route = urlparse(self.path).path
            if not self._guard(route):
                return
            m = _MEMORY_ID_RE.match(route)
            if m:
                self._send_json(*api.delete_memory(m.group(1)))
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


def build_server(api: Api, host: str = "127.0.0.1", port: int = 8787) -> HTTPServer:
    # Binds the configured host so paired LAN devices can sync (SYNC-2). The
    # management API stays loopback-only via the per-route handler guard, so
    # exposing the socket does not expose the unauthenticated endpoints.
    return HTTPServer((host, port), make_handler(api))


def serve(config, stop: threading.Event, pairing=None) -> None:
    """Own the DB connection inside this (serving) thread, then loop."""
    from clipvault.service import ClipVaultService
    from clipvault.store import db

    conn = db.connect(config.db_path)
    db.migrate(conn)  # idempotent; self-sufficient regardless of caller order
    api = Api(ClipVaultService(conn, config), pairing=pairing)
    httpd = build_server(api, config.host, config.port)
    httpd.timeout = 0.5
    log.info("api listening on %s:%d", config.host, httpd.server_address[1])
    while not stop.is_set():
        httpd.handle_request()
    httpd.server_close()
    conn.close()
