"""Multi-format roster input: PDF, CSV and XLSX normalize to the same roster.

All data is INVENTED (tests/fixture_builder.py: SECTIONED in three formats).
"""

import inspect
import json
import socket
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import replace

import pytest

from fixture_builder import (
    CSV_COLUMNS, CSV_FIXTURE, FIXTURE_DIR, NOTES_SHEET, SECTIONED, XLSX_FIXTURE, FakeRow, build_image_only_pdf,
    build_sectioned_csv, build_sectioned_xlsx, build_xlsx, sectioned_csv_rows, sectioned_table_rows, to_csv,
)
from roster_tool.errors import (
    FormatMismatchError, MalformedInputError, RosterInputError, RosterStructureError, UnsupportedInputError,
)
from roster_tool.event_csv_generator import generate_csv
from roster_tool.help_sheet_generator import generate_help_sheet
from roster_tool.inputs import SUPPORTED_EXTENSIONS, SUPPORTED_LABEL, detect_format, xlsx_input
from roster_tool.pdf_extractor import NoTextLayerError
from roster_tool.pipeline import process_pdf, process_roster
from roster_tool.server import RosterToolServer

PDF = (FIXTURE_DIR / SECTIONED.filename).read_bytes()
CSV = (FIXTURE_DIR / CSV_FIXTURE).read_bytes()
XLSX = (FIXTURE_DIR / XLSX_FIXTURE).read_bytes()
SOURCES = {"pdf": PDF, "csv": CSV, "xlsx": XLSX}
EXPECTED_CURRENT = [(r.w_number, r.first, r.last, "current") for r in SECTIONED.current]
EXPECTED_WAIT = [(r.w_number, r.first, r.last, "waitlist") for r in SECTIONED.waitlist]


def key(students):
    return [(s.w_number, s.first_name, s.last_name, s.status) for s in students]


def students_of(data, hint):
    roster = process_roster(data, hint).roster
    return key(roster.current_students), key(roster.waitlist_students)


@pytest.fixture(scope="module")
def results():
    return {fmt: process_roster(data, fmt) for fmt, data in SOURCES.items()}


# --- Each format, and their equivalence --------------------------------------------

def test_committed_fixtures_are_reproducible():
    assert build_sectioned_csv(SECTIONED) == CSV
    assert build_sectioned_xlsx(SECTIONED) == XLSX


@pytest.mark.parametrize("fmt", ["pdf", "csv", "xlsx"])
def test_each_format_parses_cleanly(results, fmt):
    res = results[fmt]
    assert res.source_format == fmt
    assert res.roster.course_title == SECTIONED.course_title
    assert res.roster.section == SECTIONED.expected_section
    assert key(res.roster.current_students) == EXPECTED_CURRENT
    assert key(res.roster.waitlist_students) == EXPECTED_WAIT
    assert res.issues == []
    assert (res.page_count is not None) == (fmt == "pdf")


def test_equivalent_files_normalize_to_identical_rosters(results):
    def shape(roster):
        strip = lambda s: {k: v for k, v in s.to_dict().items() if k != "source_page"}
        return (roster.course_title, roster.section,
                [strip(s) for s in roster.current_students], [strip(s) for s in roster.waitlist_students])
    assert shape(results["pdf"].roster) == shape(results["csv"].roster) == shape(results["xlsx"].roster)


def test_pdf_path_is_unchanged_by_the_new_input_layer():
    assert process_roster(PDF, "pdf").roster.to_dict() == process_pdf(PDF).roster.to_dict()


@pytest.mark.parametrize("fmt", ["pdf", "csv", "xlsx"])
def test_drops_and_permission_numbers_excluded(results, fmt):
    text = json.dumps(results[fmt].roster.to_dict(), ensure_ascii=False)
    for row in SECTIONED.drops:
        assert row.w_number[1:] not in text and row.last not in text
    for code, _ in SECTIONED.permission:
        assert code not in text
    assert len(results[fmt].roster.students) == 14 + 3


def test_w_number_rule_is_identical_across_formats(results):
    # PDF prints 7 digits, CSV stores them as text, XLSX as numbers: all become W + digits.
    for res in results.values():
        assert all(s.w_number.startswith("W") and len(s.w_number) == 8 for s in res.roster.students)
    assert {s.w_number for s in results["xlsx"].roster.students} == {s.w_number for s in results["pdf"].roster.students}


def test_names_preserved_across_formats(results):
    for res in results.values():
        by_w = {s.w_number: s for s in res.roster.students}
        assert (by_w["W3000003"].last_name, by_w["W3000003"].first_name) == ("de la Cruz", "Isabella M.")
        assert by_w["W3000002"].last_name == "Bautista-Reyes"
        assert by_w["W3000010"].last_name == "O'Neil"
        assert (by_w["W3000011"].last_name, by_w["W3000011"].first_name) == ("Peña", "José Luis")
        assert by_w["W3000001"].first_name == "Esperanza L"  # middle initial kept, pronouns gone
        assert all("(" not in s.display_name for s in res.roster.students)


def test_formats_detected_from_content_without_a_hint():
    for fmt, data in SOURCES.items():
        assert detect_format(data).key == fmt
        assert process_roster(data).source_format == fmt


# --- CSV specifics --------------------------------------------------------------------

@pytest.mark.parametrize("encode", [
    lambda text: b"\xef\xbb\xbf" + text.encode("utf-8"),   # Excel "CSV UTF-8"
    lambda text: text.encode("cp1252"),                       # Excel "CSV" on Windows
    lambda text: text.encode("utf-16"),                       # UTF-16 with BOM
], ids=["utf8-bom", "cp1252", "utf16"])
def test_csv_encodings_keep_accented_names(encode):
    text = CSV.decode("utf-8")
    assert students_of(encode(text), "csv") == (EXPECTED_CURRENT, EXPECTED_WAIT)


def test_quoted_csv_fields():
    rows = [["Student ID", "Last Name", "First Name", "Status"],
            ["3000101", "Smith, Jr.", 'Mary "Mae" Ann', "Enrolled"],
            ["3000102", "O'Brien-Núñez", "Zoë", "Enrolled"]]
    data = to_csv(rows)
    assert b'"Smith, Jr."' in data and b'"Mary ""Mae"" Ann"' in data
    current, _ = students_of(data, "csv")
    assert current == [("W3000101", 'Mary "Mae" Ann', "Smith, Jr.", "current"),
                       ("W3000102", "Zoë", "O'Brien-Núñez", "current")]


@pytest.mark.parametrize("columns", [
    tuple(reversed(CSV_COLUMNS)),
    ("Status", "Class Nbr", "Name", "Units", "Student ID", "Course", "Pronouns"),
    ("Email", "Student ID", "Last Name", "First Name", "Status", "Course", "Class Nbr"),  # separate name columns
])
def test_csv_column_order_and_layout_do_not_matter(columns):
    data = to_csv(sectioned_csv_rows(SECTIONED, columns))
    res = process_roster(data, "csv")
    assert (key(res.roster.current_students), key(res.roster.waitlist_students)) == (EXPECTED_CURRENT, EXPECTED_WAIT)
    assert (res.roster.course_title, res.roster.section) == (SECTIONED.course_title, "20001")


def test_csv_middle_name_column_is_kept_with_first_name():
    rows = [["ID", "First Name", "MI", "Last Name"], ["3000201", "Maria", "L", "Garcia-Lopez"]]
    assert students_of(to_csv(rows), "csv")[0] == [("W3000201", "Maria L", "Garcia-Lopez", "current")]


def test_semicolon_delimited_csv():
    assert students_of(to_csv(sectioned_csv_rows(SECTIONED), delimiter=";"), "csv") == (EXPECTED_CURRENT, EXPECTED_WAIT)


def test_sectioned_layout_also_works_as_csv():
    rows = [[("" if v is None else str(v)) for v in row] for row in sectioned_table_rows(SECTIONED)]
    res = process_roster(to_csv(rows), "csv")
    assert (key(res.roster.current_students), key(res.roster.waitlist_students)) == (EXPECTED_CURRENT, EXPECTED_WAIT)
    assert res.roster.section == "20001" and res.issues == []


@pytest.mark.parametrize("rows,needle", [
    ([["Name", "Status"], ["Doe, Jane", "Enrolled"]], "header row"),                      # no ID column
    ([["Student ID", "Status"], ["3000001", "Enrolled"]], "header row"),                  # no name column
    ([["3000001", "Doe, Jane"], ["3000002", "Roe, Ann"]], "header row"),                  # no header at all
    ([["Student ID", "First Name", "First Name", "Last Name"], ["1", "a", "b", "c"]], "more than one column"),
    ([["Student ID", "ID", "Name"], ["3000001", "3000001", "Doe, Jane"]], "more than one column"),
])
def test_missing_or_ambiguous_csv_headers_rejected(rows, needle):
    with pytest.raises(RosterStructureError) as exc:
        process_roster(to_csv(rows), "csv")
    assert needle in exc.value.user_message


@pytest.mark.parametrize("status", ["Audit", ""])
def test_unknown_or_missing_status_rejected(status):
    rows = [["Student ID", "Name", "Status"], ["3000001", "Doe, Jane", "Enrolled"], ["3000002", "Roe, Ann", status]]
    with pytest.raises(RosterStructureError) as exc:
        process_roster(to_csv(rows), "csv")
    assert "row(s) 3" in exc.value.user_message


def test_csv_with_more_than_one_section_rejected():
    rows = [["Student ID", "Name", "Class Nbr"], ["3000001", "Doe, Jane", "20001"], ["3000002", "Roe, Ann", "20002"]]
    with pytest.raises(RosterStructureError, match="more than one"):
        process_roster(to_csv(rows), "csv")


def test_malformed_csv_quoting_rejected():
    with pytest.raises(MalformedInputError, match="malformed"):
        process_roster(b'Student ID,Name\r\n3000001,"Doe, Jane\r\n', "csv")


def test_problem_rows_are_kept_not_discarded():
    rows = [["Student ID", "Name", "Status"],
            ["", "Doe, Jane", "Enrolled"],                 # missing ID
            ["3000002", "", "Enrolled"],                   # missing name
            ["W12345", "Roe, Ann", "Enrolled"],            # malformed ID, kept verbatim
            ["3000004", "Isabella de la Cruz", "Enrolled"]]  # no comma: split flagged, not trusted
    res = process_roster(to_csv(rows), "csv")
    codes = {i.code for i in res.issues}
    assert len(res.roster.students) == 4
    assert {"missing_w_number", "missing_first_name", "malformed_w_number", "needs_review"} <= codes
    assert res.roster.students[2].w_number == "W12345"


# --- XLSX specifics -------------------------------------------------------------------

ROSTER = sectioned_table_rows(SECTIONED)
FLAT = sectioned_csv_rows(SECTIONED)


@pytest.mark.parametrize("sheets", [
    [("Roster", ROSTER), ("Notes", NOTES_SHEET)],
    [("Notes", NOTES_SHEET), ("Fall 2026", ROSTER)],          # the only candidate, whatever its name
    [("Old copy", FLAT), ("Roster", ROSTER)],                 # several candidates: the one named "Roster"
    [("Flat export", FLAT)],                                  # flat status-column layout
], ids=["roster-first", "only-candidate", "named-roster-wins", "flat-layout"])
def test_xlsx_sheet_selection_is_deterministic(sheets):
    assert students_of(build_xlsx(sheets), "xlsx") == (EXPECTED_CURRENT, EXPECTED_WAIT)


def test_xlsx_ambiguous_sheets_rejected():
    with pytest.raises(RosterStructureError) as exc:
        process_roster(build_xlsx([("Section A", ROSTER), ("Section B", FLAT)]), "xlsx")
    assert "Section A" in exc.value.user_message and "Section B" in exc.value.user_message


def test_xlsx_hidden_sheets_are_ignored():
    data = build_xlsx([("Roster", ROSTER), ("Backup", FLAT)], hidden=("Backup",))
    assert students_of(data, "xlsx") == (EXPECTED_CURRENT, EXPECTED_WAIT)
    with pytest.raises(RosterStructureError, match="hidden sheets are ignored"):
        process_roster(build_xlsx([("Notes", NOTES_SHEET), ("Roster", ROSTER)], hidden=("Roster",)), "xlsx")


def test_xlsx_without_a_roster_sheet_rejected():
    with pytest.raises(RosterStructureError, match="No worksheet"):
        process_roster(build_xlsx([("Notes", NOTES_SHEET)]), "xlsx")


def test_xlsx_formulas_are_not_evaluated():
    rows = [["Student ID", "Name"], ["=3000000+1", "Doe, Jane"]]  # no cached value saved
    res = process_roster(build_xlsx([("Roster", rows)]), "xlsx")
    assert res.roster.students[0].w_number == ""  # not computed, not guessed
    assert "missing_w_number" in {i.code for i in res.issues}


def test_xlsx_lost_leading_zero_is_flagged_not_padded():
    rows = [["Student ID", "Name"], [123456, "Doe, Jane"]]  # 0123456 typed into a number cell
    res = process_roster(build_xlsx([("Roster", rows)]), "xlsx")
    assert res.roster.students[0].w_number == "123456"
    assert "malformed_w_number" in {i.code for i in res.issues}


def test_xlsx_decompression_size_guard(monkeypatch):
    monkeypatch.setattr(xlsx_input, "MAX_UNCOMPRESSED_BYTES", 1000)
    with pytest.raises(MalformedInputError, match="unusually large"):
        process_roster(XLSX, "xlsx")


# --- Detection, malformed and mismatched files ------------------------------------------

def test_supported_types():
    assert SUPPORTED_EXTENSIONS == (".pdf", ".csv", ".xlsx")
    assert SUPPORTED_LABEL == "PDF, CSV or Excel (.xlsx)"


@pytest.mark.parametrize("data,hint", [
    (CSV, "pdf"), (CSV, "xlsx"), (XLSX, "csv"), (XLSX, "pdf"), (PDF, "csv"), (PDF, "xlsx"),
])
def test_extension_and_content_must_agree(data, hint):
    with pytest.raises(FormatMismatchError) as exc:
        process_roster(data, hint)
    assert f".{hint}" in exc.value.user_message


@pytest.mark.parametrize("hint,needle", [("xls", ".xlsx"), ("txt", "not supported"), ("png", "OCR"), ("docx", "not supported")])
def test_unsupported_extensions_rejected(hint, needle):
    with pytest.raises(UnsupportedInputError) as exc:
        process_roster(CSV, hint)
    assert needle in exc.value.user_message


def _docx_like() -> bytes:
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


@pytest.mark.parametrize("data,error,needle", [
    (b"", UnsupportedInputError, "empty"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, UnsupportedInputError, ".xls"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, UnsupportedInputError, "OCR"),
    (_docx_like(), UnsupportedInputError, "not an Excel"),
    (XLSX[:300], MalformedInputError, "damaged"),
    (bytes(range(256)) * 4, UnsupportedInputError, "not a supported"),
])
def test_unreadable_or_unsupported_contents_rejected(data, error, needle):
    with pytest.raises(error) as exc:
        process_roster(data)
    assert needle in exc.value.user_message


def test_image_only_pdf_still_rejected_without_ocr():
    with pytest.raises(NoTextLayerError) as exc:
        process_roster(build_image_only_pdf(), "pdf")
    assert isinstance(exc.value, RosterInputError) and "scan" in exc.value.user_message


# --- Filename rule ----------------------------------------------------------------------

def test_process_roster_never_receives_a_filename():
    assert list(inspect.signature(process_roster).parameters) == ["data", "format_hint"]


@pytest.mark.parametrize("fmt", ["pdf", "csv", "xlsx"])
def test_renaming_a_file_does_not_change_the_roster(tmp_path, fmt):
    names = [f"Print Roster for (99999_0000).{fmt}", f"zz final copy v2.{fmt}", f"ANTH_999_77777.{fmt.upper()}"]
    outputs = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(SOURCES[fmt])
        outputs.append(process_roster(path.read_bytes(), path.suffix).roster.to_dict())
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0]["section"] == "20001"


# --- Downstream behaviour is source-independent -----------------------------------------------

def test_outputs_are_identical_whatever_the_source_format(results):
    csv_texts = {fmt: generate_csv(res.roster)[0] for fmt, res in results.items()}
    # Header information legitimately differs by source (a flat CSV export has
    # no professor, term or meetings); with it equalized, the help sheets must
    # be byte-identical, so the layout never depends on the source format.
    reference = results["pdf"].roster
    sheets = {}
    for fmt, res in results.items():
        roster = replace(res.roster, professor=reference.professor, term=reference.term,
                         meetings_location=reference.meetings_location)
        sheets[fmt] = generate_help_sheet(roster, "Jane Tutor", ["Tue", "Thu"]).pdf
    assert len(set(csv_texts.values())) == 1
    assert csv_texts["xlsx"].split("\r\n")[0] == "Email,First Name,Last Name,SSO ID,Student ID"
    assert len(csv_texts["xlsx"].strip().split("\r\n")) == 1 + 14 + 3
    assert len(set(sheets.values())) == 1  # byte-identical help sheets


# --- Server ---------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    srv = RosterToolServer()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def upload(srv, data, file_type):
    req = urllib.request.Request(srv.origin + "/api/roster", data=data, method="POST")
    req.add_header("X-Roster-Token", srv.token)
    req.add_header("Content-Type", "application/octet-stream")
    if file_type:
        req.add_header("X-Roster-File-Type", file_type)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.mark.parametrize("fmt", ["pdf", "csv", "xlsx"])
def test_upload_each_format_over_api(server, fmt):
    status, body = upload(server, SOURCES[fmt], fmt)
    assert status == 200 and body["source_format"] == fmt and body["issues"] == []
    assert len(body["roster"]["students"]) == 17
    assert (body["page_count"] is None) == (fmt != "pdf")


@pytest.mark.parametrize("data,file_type,needle", [
    (CSV, "pdf", "not a PDF"), (XLSX, "csv", "not a CSV"), (CSV, "xls", ".xlsx"), (b"\x89PNG\r\n\x1a\n", "png", "OCR"),
])
def test_rejected_uploads_over_api(server, data, file_type, needle):
    status, body = upload(server, data, file_type)
    assert status == 422 and needle in body["error"]


def test_ui_lists_supported_types(server):
    with urllib.request.urlopen(server.origin + "/", timeout=10) as resp:
        html = resp.read().decode()
    assert 'accept=".pdf,.csv,.xlsx"' in html
    assert "Upload Roster" in html and "PDF, CSV or Excel (.xlsx)" in html
    assert "__ROSTER_" not in html


# --- Privacy ---------------------------------------------------------------------------------

def test_csv_and_xlsx_processing_is_local_and_in_memory(tmp_path, monkeypatch):
    temp_dir, work_dir = tmp_path / "tmp", tmp_path / "work"
    temp_dir.mkdir()
    work_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.chdir(work_dir)
    attempts = []
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            attempts.append(host)
            raise OSError("blocked")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for fmt in ("csv", "xlsx"):
        res = process_roster(SOURCES[fmt], fmt)
        generate_csv(res.roster)
        generate_help_sheet(res.roster, "Jane", ["Mon"])
    assert attempts == []
    assert list(temp_dir.iterdir()) == [] and list(work_dir.iterdir()) == []
