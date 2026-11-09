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
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .event_csv_generator import HEADER, csv_filename, generate_csv
from .help_sheet_generator import HelpSheetInputError, generate_help_sheet
from .models import Roster, output_stem
from .pdf_extractor import MAX_PDF_BYTES, PdfExtractionError
from .pipeline import process_pdf
from .validation import OutputBlockedError, validate_roster

LOOPBACK = "127.0.0.1"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_DRAIN_BYTES = 64 * 1024 * 1024  # oversized bodies up to this size are read and discarded
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
TOKEN_PLACEHOLDER = "__ROSTER_TOKEN__"

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "connect-src 'self'; frame-src blob:; object-src blob:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

log = logging.getLogger("roster_tool.server")


class RosterToolServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0):
        # The host is fixed: there is deliberately no way to bind elsewhere.
        super().__init__((LOOPBACK, port), RequestHandler)
        self.token = secrets.token_urlsafe(32)
        self.port = self.server_address[1]
        self.origin = f"http://{LOOPBACK}:{self.port}"
        self.allowed_hosts = {f"{LOOPBACK}:{self.port}", f"localhost:{self.port}"}

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

    # -- request checks -------------------------------------------------------

    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self._error(403, "Forbidden")
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
            self._error(403, "Forbidden")
            return False
        return True

    def _read_body(self, limit: int) -> bytes | None:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._error(411, "Length required")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "Length required")
            return None
        if length < 0 or length > limit:
            # Drain (bounded) so the client receives the 413 message instead of a broken pipe.
            remaining = length if 0 < length <= MAX_DRAIN_BYTES else 0
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                remaining -= len(chunk)
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
            body = body.replace(TOKEN_PLACEHOLDER.encode(), self.server.token.encode())
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
        }.get(self._route())
        if handler is None:
            self._error(404, "Not found")
            return
        try:
            handler()
        except Exception as exc:  # never leak details (or student data) to logs or UI
            log.error("unhandled %s in %s", type(exc).__name__, self._route())
            self._error(500, "Something went wrong while processing this request.")

    # -- API ------------------------------------------------------------------

    def _api_roster(self) -> None:
        body = self._read_body(MAX_PDF_BYTES)
        if body is None:
            return
        try:
            result = process_pdf(body)
        except PdfExtractionError as exc:
            self._error(422, exc.user_message)
            return
        log.info("processed %d page(s) in %.1f ms", result.page_count, result.timings_ms["total"])
        self._json(200, {
            "roster": result.roster.to_dict(),
            "issues": [i.to_dict() for i in result.issues],
            "page_count": result.page_count,
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
        try:
            result = generate_help_sheet(roster, tutor, data.get("days"))
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
