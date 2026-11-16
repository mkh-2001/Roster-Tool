"""Local-only HTTP server connecting the UI to the Python processing.

Security model (single user, single machine):
* Binds to 127.0.0.1 only (never 0.0.0.0) on a random free port.
* Every API call must carry a per-launch random token (X-Roster-Token). Other
  websites open in the same browser cannot read or forge it, and a custom
  header forces a CORS preflight, which this server never approves.
* Host header must be 127.0.0.1:<port> / localhost:<port> (blocks DNS rebinding).
* Static files come from a fixed whitelist (no path traversal possible).
* Uploads are processed in memory; nothing is written to disk.
* Logs contain method, route, status and duration only - never request or
  response bodies, so no student names or W numbers reach the logs.
* No outbound network requests anywhere in the application.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import re
import secrets
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, resources
from .errors import RosterInputError
from .event_csv_generator import HEADER, csv_filename, generate_csv
from .help_sheet_generator import HelpSheetInputError, generate_help_sheet
from .inputs import SUPPORTED_EXTENSIONS, SUPPORTED_LABEL
from .models import Roster, output_stem
from .pipeline import process_roster
from .validation import OutputBlockedError, validate_roster

LOOPBACK = "127.0.0.1"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
# Fallback format hints for clients that send a meaningful Content-Type
# instead of the X-Roster-File-Type header.
CONTENT_TYPE_HINTS = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}
MAX_DRAIN_BYTES = 64 * 1024 * 1024  # oversized bodies up to this size are read and discarded
STATIC_DIR = resources.STATIC_DIR
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
TOKEN_PLACEHOLDER = "__ROSTER_TOKEN__"
ACCEPT_PLACEHOLDER = "__ROSTER_ACCEPT__"   # e.g. ".pdf,.csv,.xlsx" (from the input format registry)
TYPES_PLACEHOLDER = "__ROSTER_TYPES__"     # e.g. "PDF, CSV or Excel (.xlsx)"
VERSION_PLACEHOLDER = "__ROSTER_VERSION__"
CLIENT_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
QUIET_ROUTES = {"/api/heartbeat"}  # sent every 30 s; not worth a log line each time

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; frame-src blob:; object-src blob:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

log = logging.getLogger("roster_tool.server")


class RosterToolServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, lifecycle=None):
        # The host is fixed: there is deliberately no way to bind elsewhere.
        super().__init__((LOOPBACK, port), RequestHandler)
        self.token = secrets.token_urlsafe(32)
        self.port = self.server_address[1]
        self.origin = f"http://{LOOPBACK}:{self.port}"
        self.allowed_hosts = {f"{LOOPBACK}:{self.port}", f"localhost:{self.port}"}
        # Optional lifecycle.SessionMonitor (set by the desktop launcher): told
        # which browser tabs are open, and asked to exit on "Quit".
        self.lifecycle = lifecycle

    def server_bind(self) -> None:
        # The standard HTTPServer.server_bind() also does a reverse-DNS lookup
        # (socket.getfqdn) just to fill in `server_name`, which is never used
        # here. In the packaged app that lookup blocked startup for ~35 s when
        # launched outside a terminal (e.g. by double-click), and it is a
        # needless resolver query. Bind without it.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def url(self) -> str:
        return f"{self.origin}/"


class RequestHandler(BaseHTTPRequestHandler):
    server: RosterToolServer
    server_version = "RosterTool"
    sys_version = ""
    timeout = 60

    # -- logging (no bodies, no query strings) -------------------------------

    def log_message(self, format, *args):  # noqa: A002 - signature from base class
        pass

    def log_request(self, code="-", size="-"):
        elapsed = (time.perf_counter() - getattr(self, "_t0", time.perf_counter())) * 1000
        if self._route() in QUIET_ROUTES and str(code).startswith("2"):
            return
        log.info("%s %s -> %s (%.0f ms)", self.command, self._route(), code, elapsed)

    def _route(self) -> str:
        return self.path.split("?", 1)[0][:100]

    # -- responses ------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, **extra) -> None:
        self._json(status, {"error": message, **extra})

    def _discard_body(self) -> None:
        """Read and drop the request body of a request we are about to reject.

        A request turned away by the checks below is answered before its body
        has been read. Closing the connection with that body still unread makes
        Windows reset it, and the reset throws away the response the client has
        not read yet - so the caller sees a dropped connection instead of our
        message (Linux and macOS happen to hand over the response first).
        Draining a bounded amount first keeps the error readable everywhere.
        """
        try:
            remaining = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            remaining = 0
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower() or not 0 < remaining <= MAX_DRAIN_BYTES:
            self.close_connection = True  # nothing to drain, or more than we are willing to read
            return
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            remaining -= len(chunk)

    def _reject(self, status: int, message: str) -> None:
        """Answer a request whose body was never read."""
        self._discard_body()
        self._error(status, message)

    # -- request checks -------------------------------------------------------

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self._reject(403, "Forbidden")
            return False
        return True

    def _api_auth_ok(self) -> bool:
        token = self.headers.get("X-Roster-Token", "")
        origin = self.headers.get("Origin")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if (
            not hmac.compare_digest(token.encode(), self.server.token.encode())
            or (origin is not None and origin != self.server.origin)
            or (fetch_site is not None and fetch_site not in ("same-origin", "none"))
        ):
            self._reject(403, "Forbidden")
            return False
        return True

    def _read_body(self, limit: int) -> bytes | None:
        # Without a length there is no safe way to drain, so these close the connection.
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._reject(411, "Length required")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reject(411, "Length required")
            return None
        if length < 0 or length > limit:
            # Drain (bounded) so the client receives the 413 message instead of a broken pipe.
            self._discard_body()
            self.close_connection = True
            self._error(413, f"File is too large (limit {limit // (1024 * 1024)} MB).")
            return None
        return self.rfile.read(length)

    def _read_json(self) -> dict | None:
        body = self._read_body(MAX_JSON_BYTES)
        if body is None:
            return None
        try:
            data = json.loads(body.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            return data
        except (ValueError, UnicodeDecodeError):
            self._error(400, "Invalid request.")
            return None

    # -- routing --------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        self._t0 = time.perf_counter()
        if not self._host_ok():
            return
        entry = STATIC_FILES.get(self._route())
        if entry is None:
            self._error(404, "Not found")
            return
        filename, content_type = entry
        body = (STATIC_DIR / filename).read_bytes()
        extra = {}
        if filename == "index.html":
            body = (body.replace(TOKEN_PLACEHOLDER.encode(), self.server.token.encode())
                        .replace(ACCEPT_PLACEHOLDER.encode(), ",".join(SUPPORTED_EXTENSIONS).encode())
                        .replace(TYPES_PLACEHOLDER.encode(), SUPPORTED_LABEL.encode())
                        .replace(VERSION_PLACEHOLDER.encode(), __version__.encode()))
            extra["Content-Security-Policy"] = CSP
        self._send(200, body, content_type, extra)

    def do_POST(self):  # noqa: N802
        self._t0 = time.perf_counter()
        if not self._host_ok() or not self._api_auth_ok():
            return
        handler = {
            "/api/roster": self._api_roster,
            "/api/validate": self._api_validate,
            "/api/csv": self._api_csv,
            "/api/help-sheet": self._api_help_sheet,
            "/api/heartbeat": self._api_heartbeat,
            "/api/goodbye": self._api_goodbye,
            "/api/shutdown": self._api_shutdown,
        }.get(self._route())
        if handler is None:
            self._reject(404, "Not found")
            return
        try:
            handler()
        except Exception as exc:  # never leak details (or student data) to logs or UI
            log.error("unhandled %s in %s", type(exc).__name__, self._route())
            self._error(500, "Something went wrong while processing this request.")

    # -- API ------------------------------------------------------------------

    # -- app lifecycle (which tabs are open; Quit) ------------------------------

    def _client_id(self) -> str | None:
        data = self._read_json()
        if data is None:
            return None
        client = data.get("client")
        if not isinstance(client, str) or not CLIENT_ID.fullmatch(client):
            self._error(400, "Invalid request.")
            return None
        return client

    def _api_heartbeat(self) -> None:
        client = self._client_id()
        if client is None:
            return
        if self.server.lifecycle is not None:
            self.server.lifecycle.seen(client)
        self._json(200, {"ok": True})

    def _api_goodbye(self) -> None:
        client = self._client_id()
        if client is None:
            return
        if self.server.lifecycle is not None:
            self.server.lifecycle.goodbye(client)
        self._json(200, {"ok": True})

    def _api_shutdown(self) -> None:
        if self._read_json() is None:
            return
        if self.server.lifecycle is not None:
            # Only sets a flag; the monitor stops the app on its next tick,
            # well after this response has been sent.
            self.server.lifecycle.request_exit("Quit button")
            self._json(200, {"ok": True})
        else:
            self._json(200, {"ok": True})  # answer first, then stop
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _api_roster(self) -> None:
        body = self._read_body(MAX_UPLOAD_BYTES)
        if body is None:
            return
        # Only the file's extension is sent (never its name); it must agree with the content.
        hint = self.headers.get("X-Roster-File-Type") or CONTENT_TYPE_HINTS.get(
            self.headers.get("Content-Type", "").split(";")[0].strip().lower())
        try:
            result = process_roster(body, hint)
        except RosterInputError as exc:
            self._error(422, exc.user_message)
            return
        log.info("processed %s roster in %.1f ms", result.source_format, result.timings_ms["total"])
        self._json(200, {
            "roster": result.roster.to_dict(),
            "issues": [i.to_dict() for i in result.issues],
            "page_count": result.page_count,
            "source_format": result.source_format,
            "timings_ms": {k: round(v, 2) for k, v in result.timings_ms.items()},
        })

    def _roster_from(self, data: dict) -> Roster | None:
        try:
            return Roster.from_dict(data.get("roster"))
        except ValueError:
            self._error(400, "Invalid roster data.")
            return None

    def _api_validate(self) -> None:
        data = self._read_json()
        roster = data is not None and self._roster_from(data)
        if not roster:
            return
        self._json(200, {"roster": roster.to_dict(), "issues": [i.to_dict() for i in validate_roster(roster)]})

    def _api_csv(self) -> None:
        data = self._read_json()
        roster = data is not None and self._roster_from(data)
        if not roster:
            return
        try:
            text, rows = generate_csv(roster)
        except OutputBlockedError as exc:
            self._error(422, str(exc), issues=[i.to_dict() for i in exc.issues])
            return
        self._json(200, {"filename": csv_filename(roster), "csv": text, "header": list(HEADER), "rows": rows})

    def _api_help_sheet(self) -> None:
        data = self._read_json()
        roster = data is not None and self._roster_from(data)
        if not roster:
            return
        tutor = data.get("tutor_name")
        if not isinstance(tutor, str):
            tutor = ""
        location = data.get("meetings_location")  # Help Sheet only; never part of the Event CSV
        if not isinstance(location, str):
            location = None  # not sent: use the roster's
        try:
            result = generate_help_sheet(roster, tutor, data.get("days"), meetings_location=location)
        except HelpSheetInputError as exc:
            self._error(422, str(exc))
            return
        except OutputBlockedError as exc:
            self._error(422, str(exc), issues=[i.to_dict() for i in exc.issues])
            return
        self._json(200, {
            "filename": f"help_sheet_{output_stem(roster)}.pdf",
            "page_count": result.page_count,
            "warnings": result.warnings,
            "pdf_base64": base64.b64encode(result.pdf).decode("ascii"),
        })
