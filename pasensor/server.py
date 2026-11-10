"""Local read-only dashboard server.

Security posture
    * Binds to 127.0.0.1 only. There is no option to listen on the network.
    * GET and HEAD only. No endpoint changes sensor state.
    * The Host header must name the loopback address, which blocks DNS
      rebinding from a web page in the same browser.
    * No CORS headers are sent, so other origins cannot read the API.
    * A strict Content-Security-Policy allows only this server's own files.
    * Static files come from a fixed table. Request paths never reach the
      file system.
"""
from __future__ import annotations

import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__

WEB_ROOT = Path(__file__).resolve().parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _clean(value):
    """Replace NaN and infinity with None so the payload is strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _clean(value.item())
        except (ValueError, AttributeError):
            return None
    return value


def make_handler(state, info: dict, allowed_hosts: set):

    class Handler(BaseHTTPRequestHandler):
        server_version = f"pasensor/{__version__}"
        sys_version = ""

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload, code: int = 200) -> None:
            self._send(code, json.dumps(_clean(payload), allow_nan=False).encode("utf-8"), "application/json")

        def do_GET(self) -> None:
            try:
                if self.headers.get("Host", "") not in allowed_hosts:
                    self._json({"error": "this service answers only on the loopback address"}, 421)
                    return
                path = self.path.split("?", 1)[0]
                if path in STATIC:
                    name, content_type = STATIC[path]
                    self._send(200, (WEB_ROOT / name).read_bytes(), content_type)
                elif path == "/api/status":
                    self._json(state.snapshot())
                elif path == "/api/history":
                    self._json({"history": state.history()})
                elif path == "/api/events":
                    self._json({"events": state.events()})
                elif path == "/api/info":
                    self._json(info)
                else:
                    self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:  # never let a handler error kill the server thread
                try:
                    self._json({"error": f"internal error: {type(error).__name__}"}, 500)
                except OSError:
                    pass

        do_HEAD = do_GET

        def _refuse(self) -> None:
            self._json({"error": "read-only service"}, 405)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse

        def log_message(self, *_args) -> None:
            pass

    return Handler


def make_server(state, info: dict, port: int) -> ThreadingHTTPServer:
    allowed_hosts: set = set()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(state, info, allowed_hosts))
    actual = server.server_address[1]          # differs from `port` when port 0 asks for any free port
    allowed_hosts.update({f"127.0.0.1:{actual}", f"localhost:{actual}"})
    server.daemon_threads = True
    return server
