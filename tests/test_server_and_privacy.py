"""Local server, security and privacy behaviour."""

import base64
import json
import logging
import socket
import tempfile
import threading
import urllib.error
import urllib.request

import pytest

from fixture_builder import FIXTURE_DIR, MULTIPAGE, SMALL, build_pdf
from roster_tool.event_csv_generator import generate_csv
from roster_tool.help_sheet_generator import generate_help_sheet
from roster_tool.pdf_extractor import extract_pdf
from roster_tool.pipeline import process_pdf
from roster_tool.server import LOOPBACK, MAX_JSON_BYTES, RosterToolServer


@pytest.fixture(scope="module")
def server():
    srv = RosterToolServer()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, path, data=None, ctype="application/json", token=True, headers=None):
    req = urllib.request.Request(srv.origin + path, data=data, method="POST" if data is not None else "GET")
    req.add_header("Content-Type", ctype)
    if token:
        req.add_header("X-Roster-Token", srv.token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def post_json(srv, path, payload, **kw):
    status, body, headers = call(srv, path, json.dumps(payload).encode(), **kw)
    return status, json.loads(body)


# --- Local-only networking ------------------------------------------------------------

def test_binds_only_to_loopback(server):
    assert server.server_address[0] == "127.0.0.1" == LOOPBACK


def test_cannot_be_reached_on_other_interfaces(server):
    # Find a non-loopback address of this machine, if any, and confirm the port is closed there.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # TEST-NET address; UDP connect sends no packets
        lan_ip = probe.getsockname()[0]
        probe.close()
    except OSError:
        pytest.skip("no non-loopback interface")
    if lan_ip.startswith("127."):
        pytest.skip("no non-loopback interface")
    with pytest.raises(OSError):
        socket.create_connection((lan_ip, server.port), timeout=1).close()


def test_no_outbound_network_during_full_workflow(monkeypatch):
    """Extract, parse, CSV and help sheet with every non-loopback connection blocked."""
    attempts = []
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(host)
            raise OSError("outbound network blocked by test")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    result = process_pdf((FIXTURE_DIR / MULTIPAGE.filename).read_bytes())
    generate_csv(result.roster)
    generate_help_sheet(result.roster, "Jane", ["Mon", "Wed"])
    assert attempts == []


# --- Request protection ---------------------------------------------------------------

def test_index_served_with_token_and_csp(server):
    status, body, headers = call(server, "/")
    assert status == 200
    assert server.token.encode() in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_no_external_resources_in_ui(server):
    for path in ("/", "/static/app.js", "/static/styles.css"):
        _, body, _ = call(server, path)
        # The SVG namespace is an identifier inside the inline favicon, not a request.
        text = body.decode().replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in text and "https://" not in text and "//cdn" not in text, path


@pytest.mark.parametrize("path", ["/static/../server.py", "/../../etc/passwd", "/static/%2e%2e/server.py", "/static/fonts/x.ttf"])
def test_no_path_traversal_or_unlisted_files(server, path):
    assert call(server, path)[0] == 404


def test_wrong_host_header_rejected(server):
    status, _, _ = call(server, "/", headers={"Host": f"attacker.example:{server.port}"})
    assert status == 403


def test_api_requires_token(server):
    pdf = build_pdf(SMALL)
    assert call(server, "/api/roster", pdf, "application/pdf", token=False)[0] == 403
    assert call(server, "/api/roster", pdf, "application/pdf", token=False, headers={"X-Roster-Token": "nope"})[0] == 403


def test_api_rejects_cross_origin(server):
    pdf = build_pdf(SMALL)
    assert call(server, "/api/roster", pdf, "application/pdf", headers={"Origin": "https://evil.example"})[0] == 403
    assert call(server, "/api/roster", pdf, "application/pdf", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_oversized_json_rejected(server):
    status, _, _ = call(server, "/api/validate", b"{" + b" " * (MAX_JSON_BYTES + 10) + b"}")
    assert status == 413


def test_malformed_requests_get_clean_errors(server):
    assert call(server, "/api/validate", b"not json")[0] == 400
    assert post_json(server, "/api/validate", {"roster": {"students": "x"}})[0] == 400
    assert call(server, "/api/nope", b"{}")[0] == 404


@pytest.mark.parametrize("data,message", [
    (b"plain text", "not a PDF"),
    (b"%PDF-1.7\n%%EOF garbage", "damaged"),
])
def test_malformed_pdf_upload(server, data, message):
    status, body, _ = call(server, "/api/roster", data, "application/pdf")
    assert status == 422 and message in json.loads(body)["error"]


# --- Full API workflow --------------------------------------------------------------------

def test_full_workflow_over_api(server):
    status, body, _ = call(server, "/api/roster", (FIXTURE_DIR / SMALL.filename).read_bytes(), "application/pdf")
    assert status == 200
    data = json.loads(body)
    roster = data["roster"]
    assert len(roster["students"]) == 16 and data["issues"] == []
    assert {"pdf_open", "text_extract", "roster_parse", "normalize", "validate", "total"} <= set(data["timings_ms"])

    status, csv_data = post_json(server, "/api/csv", {"roster": roster})
    assert status == 200
    assert csv_data["csv"].startswith("Email,First Name,Last Name,SSO ID,Student ID\r\nW1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567\r\n")
    assert csv_data["filename"] == "event_invites_PHIL_320_10141.csv"

    status, sheet = post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": "Jane", "days": ["Tuesday", "Thursday"]})
    assert status == 200 and sheet["page_count"] == 1
    assert base64.b64decode(sheet["pdf_base64"]).startswith(b"%PDF")


def test_edits_are_revalidated_and_block_output(server):
    _, body, _ = call(server, "/api/roster", build_pdf(SMALL), "application/pdf")
    roster = json.loads(body)["roster"]
    roster["students"][3]["w_number"] = roster["students"][0]["w_number"]
    status, data = post_json(server, "/api/validate", {"roster": roster})
    assert status == 200 and any(i["code"] == "duplicate_w_number" for i in data["issues"])
    status, data = post_json(server, "/api/csv", {"roster": roster})
    assert status == 422 and data["issues"]
    status, data = post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": "Jane", "days": ["Mon"]})
    assert status == 422


def test_help_sheet_input_errors(server):
    _, body, _ = call(server, "/api/roster", build_pdf(SMALL), "application/pdf")
    roster = json.loads(body)["roster"]
    assert post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": "", "days": ["Mon"]})[0] == 422
    assert post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": "Jane", "days": []})[0] == 422


def test_meetings_location_is_for_the_help_sheet_only(server):
    _, body, _ = call(server, "/api/roster", build_pdf(SMALL), "application/pdf")
    roster = json.loads(body)["roster"]
    _, plain = post_json(server, "/api/csv", {"roster": roster})
    # Header-only values never change, block or join the Event CSV, whatever they hold.
    for extra in ({"meetings_location": "Science 415", "professor": "Staff", "term": "Fall 2026"},
                  {"meetings_location": "x" * 5000}, {"meetings_location": 42}, {"meetings_location": None}):
        edited = {**roster, **extra}
        status, data = post_json(server, "/api/csv", {"roster": edited})
        assert status == 200 and (data["csv"], data["filename"]) == (plain["csv"], plain["filename"])
        status, data = post_json(server, "/api/validate", {"roster": edited})
        assert status == 200 and data["issues"] == []
    assert plain["csv"].splitlines()[0] == "Email,First Name,Last Name,SSO ID,Student ID"

    def header(payload):
        status, sheet = post_json(server, "/api/help-sheet", {"roster": roster, "days": ["Mon"], **payload})
        assert status == 200
        return [line.text for line in extract_pdf(base64.b64decode(sheet["pdf_base64"])).pages[0].lines[:5]]

    entered = header({"tutor_name": "Jane", "meetings_location": "Library 210"})
    assert "Tutor: Jane" in entered and "Meetings Location: Library 210" in entered
    assert "Meetings Location: —" in header({"tutor_name": "Jane", "meetings_location": ""})  # optional
    assert "Meetings Location: —" in header({"tutor_name": "Jane"})  # SMALL's roster gives none
    # The tutor stays required for the help sheet, with or without a location.
    status, data = post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": " ", "meetings_location": "Library 210", "days": ["Mon"]})
    assert status == 422 and "tutor" in data["error"].lower()


# --- Privacy: logs and temporary files -----------------------------------------------------

def test_logs_never_contain_student_data(server, caplog):
    caplog.set_level(logging.DEBUG)
    _, body, _ = call(server, "/api/roster", (FIXTURE_DIR / SMALL.filename).read_bytes(), "application/pdf")
    roster = json.loads(body)["roster"]
    post_json(server, "/api/csv", {"roster": roster})
    post_json(server, "/api/help-sheet", {"roster": roster, "tutor_name": "Jane Tutorname", "days": ["Mon"]})
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "/api/roster" in logged  # requests are logged...
    for s in SMALL.rows:  # ...but never names or W numbers
        assert s.w_number not in logged and s.last not in logged
    assert "Tutorname" not in logged


def test_no_temporary_or_stray_files(tmp_path, monkeypatch):
    """The whole workflow runs in memory: nothing written to the temp dir or working dir."""
    temp_dir = tmp_path / "tmp"
    work_dir = tmp_path / "work"
    temp_dir.mkdir()
    work_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.chdir(work_dir)
    result = process_pdf((FIXTURE_DIR / MULTIPAGE.filename).read_bytes())
    generate_csv(result.roster)
    generate_help_sheet(result.roster, "Jane", ["Mon", "Wed", "Fri"])
    assert list(temp_dir.iterdir()) == []
    assert list(work_dir.iterdir()) == []
