"""HTTP server (stdlib, D-006). Binds to 127.0.0.1 and rejects any non-loopback
client at the handler level (defence in depth alongside the bind address).
Bearer-token auth for paired devices lands in S006.
"""

import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClipVault/0.1"

        def _is_loopback(self) -> bool:
            return self.client_address[0] in _LOOPBACK

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

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def _guard(self) -> bool:
            if not self._is_loopback():
                self._send_json(403, {"error": {"code": "forbidden",
                                                 "message": "loopback only"}})
                return False
            return True

        def log_message(self, fmt, *args):  # route through our logger, no content
            log.info("%s %s", self.command, urlparse(self.path).path)

        def do_GET(self):
            if not self._guard():
                return
            parsed = urlparse(self.path)
            route = parsed.path
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
            else:
                m = _ACTIONS_RE.match(route)
                if m:
                    self._send_json(*api.clip_actions(m.group(1)))
                else:
                    self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_POST(self):
            if not self._guard():
                return
            route = urlparse(self.path).path
            if route == "/api/clips":
                self._send_json(*api.create_clip(self._body()))
                return
            if route == "/api/memory":
                self._send_json(*api.create_memory(self._body()))
                return
            m = _RELEASE_RE.match(route)
            if m:
                self._send_json(*api.release_clip(m.group(1)))
                return
            m = _PROMOTE_RE.match(route)
            if m:
                self._send_json(*api.promote_clip(m.group(1), self._body()))
                return
            m = _MEMORY_USE_RE.match(route)
            if m:
                self._send_json(*api.use_memory(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": route}})

        def do_DELETE(self):
            if not self._guard():
                return
            m = _MEMORY_ID_RE.match(urlparse(self.path).path)
            if m:
                self._send_json(*api.delete_memory(m.group(1)))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": self.path}})

        def do_PATCH(self):
            if not self._guard():
                return
            m = _CLIP_ID_RE.match(urlparse(self.path).path)
            if m:
                self._send_json(*api.patch_clip(m.group(1), self._body()))
                return
            self._send_json(404, {"error": {"code": "not_found", "message": self.path}})

    return Handler


def build_server(api: Api, host: str = "127.0.0.1", port: int = 8787) -> HTTPServer:
    # Plain (single-threaded) HTTPServer: requests are handled on the serving
    # thread, so the SQLite connection owned by `api` is never crossed between
    # threads. Self-use traffic is trivial; serial handling is plenty.
    # Self-use safety: never bind to a non-loopback address regardless of config
    # until paired-device auth exists (S006).
    bind_host = host if host in _LOOPBACK else "127.0.0.1"
    return HTTPServer((bind_host, port), make_handler(api))


def serve(config, stop: threading.Event) -> None:
    """Own the DB connection inside this (serving) thread, then loop.
    Called as the target of the api daemon thread in main.py."""
    from clipvault.service import ClipVaultService
    from clipvault.store import db

    conn = db.connect(config.db_path)
    db.migrate(conn)  # idempotent; makes this thread self-sufficient regardless of caller order
    api = Api(ClipVaultService(conn, config))
    httpd = build_server(api, config.host, config.port)
    httpd.timeout = 0.5
    log.info("api listening on http://127.0.0.1:%d/", httpd.server_address[1])
    while not stop.is_set():
        httpd.handle_request()
    httpd.server_close()
    conn.close()
