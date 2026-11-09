"""PDF extraction + roster parsing.

NOTE: every PDF here is a FAKE fixture we generated. Passing these tests does
NOT prove compatibility with the real roster system's PDFs.
"""

import io
import re

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from conftest import parse_spec
from fixture_builder import LARGE, MULTIPAGE, SMALL, FakeRow, FixtureSpec, _fonts, build_image_only_pdf, build_pdf
from roster_tool.names import NOTE_MULTI_PART, NOTE_NO_COMMA
from roster_tool.pdf_extractor import (
    CorruptPdfError, EncryptedPdfError, NoTextLayerError, NotAPdfError, PdfTooLargeError, extract_pdf,
    MAX_PDF_BYTES,
)
from roster_tool.roster_parser import NOTE_NO_ID, ParseRules, TableRosterParser


def codes(result):
    return {i.code for i in result.issues}


def students(result):
    return [(s.w_number, s.first_name, s.last_name) for s in result.roster.students]


def expected(spec):
    return [(r.w_number, r.first, r.last) for r in spec.rows]


# --- Extraction ----------------------------------------------------------------

def test_extracts_text_lines_and_cells():
    doc = extract_pdf(build_pdf(SMALL))
    texts = [line.text for line in doc.lines]
    assert "Course: PHIL 320 Logic and Critical Reasoning" in texts
    row = next(line for line in doc.lines if line.text.startswith("W1234567"))
    assert [c.text for c in row.cells] == ["W1234567", "Hewad Ahmadzai"]  # wide gap -> separate cells


def test_extraction_is_multi_page_and_in_reading_order():
    doc = extract_pdf(build_pdf(MULTIPAGE))
    assert [p.number for p in doc.pages] == [1, 2, 3]
    ids = [m for line in doc.lines for m in re.findall(r"\bW\d{7}\b", line.text)]
    assert ids == [r.w_number for r in MULTIPAGE.rows]


def test_extraction_records_timings():
    doc = extract_pdf(build_pdf(SMALL))
    assert set(doc.timings_ms) == {"pdf_open", "text_extract"}


def test_not_a_pdf():
    for data in (b"", b"hello world", b"\x89PNG\r\n\x1a\n" + b"0" * 100):
        with pytest.raises(NotAPdfError):
            extract_pdf(data)


@pytest.mark.parametrize("data", [b"%PDF-1.4\ngarbage", build_pdf(SMALL)[:400]])
def test_corrupt_or_truncated_pdf(data):
    with pytest.raises(CorruptPdfError):
        extract_pdf(data)


def test_encrypted_pdf():
    spec = FixtureSpec(**{**SMALL.__dict__, "extra": {"encrypt": StandardEncryption("pw", ownerPassword="o")}})
    with pytest.raises(EncryptedPdfError):
        extract_pdf(build_pdf(spec))


def test_scanned_pdf_without_text_layer_is_refused():
    with pytest.raises(NoTextLayerError) as exc:
        extract_pdf(build_image_only_pdf())
    assert "scan" in exc.value.user_message


def test_oversized_pdf_rejected_before_parsing():
    with pytest.raises(PdfTooLargeError):
        extract_pdf(b"%PDF-1.4\n" + b"0" * MAX_PDF_BYTES)


# --- Parsing the committed fixtures --------------------------------------------

@pytest.mark.parametrize("spec", [SMALL, LARGE, MULTIPAGE], ids=lambda s: s.filename)
def test_fixture_parses_exactly(fixture_results, spec):
    _, result = fixture_results[spec.filename]
    assert students(result) == expected(spec)  # W numbers, first and last names, order
    assert result.roster.course_title == spec.course_title
    assert result.roster.section == spec.section
    assert result.roster.uncertain_fields == []
    assert result.issues == []


def test_student_count_preserved(fixture_results):
    for spec, result in fixture_results.values():
        assert len(result.roster.students) == len(spec.rows)


def test_multipage_header_and_footer_lines_ignored(fixture_results):
    _, result = fixture_results[MULTIPAGE.filename]
    assert len(result.roster.students) == 95
    assert result.page_count == 3
    assert {s.source_page for s in result.roster.students} == {1, 2, 3}
    assert result.roster.expected_count == 95 and result.roster.expected_count_source == "stated"


def test_tricky_names_from_fixture(fixture_results):
    _, result = fixture_results[LARGE.filename]
    by_last = {s.last_name: s.first_name for s in result.roster.students}
    assert by_last["Garcia-Lopez"] == "Maria Fernanda"        # hyphenated
    assert by_last["O'Brien"] == "Sean"                       # apostrophe
    assert by_last["de la Cruz"] == "Isabella"                # multi-part, lowercase particle
    assert by_last["Ramírez de Arellano"] == "Juan Carlos"    # long multi-part
    assert by_last["Johnson Jr."] == "Michael"                # suffix
    assert by_last["Nguyễn"] == "Thảo Vy"                     # non-ASCII preserved
    assert by_last["Ó Súilleabháin"] == "Aoife"


def test_w_numbers_keep_leading_w(fixture_results):
    for _, result in fixture_results.values():
        assert all(s.w_number.startswith("W") and len(s.w_number) == 8 for s in result.roster.students)


# --- Course / section ------------------------------------------------------------

def _pdf_from_lines(lines, size=10):
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    y = 720
    for line in lines:
        x = 50
        for part in line if isinstance(line, tuple) else (line,):
            c.setFont("Fx", size)
            c.drawString(x, y, part)
            x += 160
        y -= 15
    c.save()
    return buf.getvalue()


ROWS = [("W1234567", "Ahmadzai, Hewad"), ("W2345678", "Alizada, Bageer"), ("W3456789", "Andrews, JuvWan")]


def test_course_and_section_from_labels_in_one_line():
    from roster_tool.pipeline import process_pdf
    r = process_pdf(_pdf_from_lines(["Course: ENGL 300 College Composition   Section: 12345", *ROWS])).roster
    assert (r.course_title, r.section, r.uncertain_fields) == ("ENGL 300 College Composition", "12345", [])


def test_course_code_and_title_labels_are_combined():
    from roster_tool.pipeline import process_pdf
    r = process_pdf(_pdf_from_lines([("Course:", "PHIL 320"), ("Title:", "Logic"), ("Class Nbr:", "10141"), *ROWS])).roster
    assert (r.course_title, r.section) == ("PHIL 320 Logic", "10141")


def test_unlabelled_course_is_marked_uncertain():
    from roster_tool.pipeline import process_pdf
    result = process_pdf(_pdf_from_lines(["PHIL 320 Logic and Critical Reasoning", "Class Nbr 10141", *ROWS]))
    r = result.roster
    assert r.course_title == "PHIL 320 Logic and Critical Reasoning"
    assert r.section == "10141"
    assert set(r.uncertain_fields) == {"course_title", "section"}
    assert {"uncertain_course_title", "uncertain_section"} <= codes(result)


def test_missing_course_and_section_are_warnings_not_errors():
    from roster_tool.pipeline import process_pdf
    result = process_pdf(_pdf_from_lines(list(ROWS)))
    assert result.roster.course_title == "" and result.roster.section == ""
    assert {"missing_course_title", "missing_section"} <= codes(result)
    assert not [i for i in result.issues if i.severity == "error"]  # roster itself is fine


# --- Problem rosters: must be surfaced, never silently "fixed" or dropped -------

def _variant(rows, **kw):
    base = dict(filename="t.pdf", course_title="PHIL 320 Logic", section="10141", rows=rows)
    base.update(kw)
    return FixtureSpec(**base)


GOOD = [FakeRow("W1234567", "Hewad", "Ahmadzai"), FakeRow("W2345678", "Bageer", "Alizada"),
        FakeRow("W3456789", "JuvWan", "Andrews"), FakeRow("W4567890", "Neil", "Balbon")]


def test_missing_w_number_row_is_kept_and_blocks_output():
    rows = GOOD[:2] + [FakeRow("", "Alana", "Campbell")] + GOOD[2:]
    result = parse_spec(_variant(rows, stated_total=5))
    assert len(result.roster.students) == 5
    missing = result.roster.students[2]
    assert (missing.w_number, missing.first_name, missing.last_name) == ("", "Alana", "Campbell")
    assert missing.review_note == NOTE_NO_ID
    assert "missing_w_number" in codes(result)


@pytest.mark.parametrize("bad_id", ["W123456", "W12345678", "w1234567", "W 1234567", "1234567"])
def test_malformed_w_number_is_kept_verbatim_and_blocks(bad_id):
    rows = GOOD[:1] + [FakeRow(bad_id, "Alana", "Campbell")] + GOOD[1:]
    result = parse_spec(_variant(rows))
    assert result.roster.students[1].w_number == bad_id  # not modified
    assert "malformed_w_number" in codes(result)


def test_duplicate_w_numbers_block_output():
    rows = GOOD + [FakeRow("W2345678", "Bageer", "Alizada")]
    result = parse_spec(_variant(rows))
    dupes = [i for i in result.issues if i.code == "duplicate_w_number"]
    assert {i.row for i in dupes} == {1, 4}


def test_stated_count_mismatch_blocks_output():
    result = parse_spec(_variant(GOOD, stated_total=6))
    assert "count_mismatch" in codes(result)


def test_row_number_gap_detected_without_stated_count():
    spec = _variant(GOOD, extra={"row_number_override": {3: 5}})  # rows numbered 1,2,3,5
    result = parse_spec(spec)
    assert result.roster.expected_count == 5 and result.roster.expected_count_source == "row_numbers"
    assert "count_mismatch" in codes(result)


def test_thirty_records_but_only_twenty_two_valid_ids_is_not_silent():
    rows = [FakeRow(f"W{2000000 + i:07d}" if i < 22 else f"W{20000 + i}", f"First{chr(65 + i)}", f"Last{chr(65 + i)}") for i in range(30)]
    result = parse_spec(_variant(rows, stated_total=30))
    assert len(result.roster.students) == 30  # bad rows kept
    assert sum(1 for i in result.issues if i.code == "malformed_w_number") == 8


def test_first_last_roster_with_multi_part_name_is_flagged():
    rows = GOOD + [FakeRow("W5678901", "Isabella", "de la Cruz")]
    result = parse_spec(_variant(rows, layout="simple", name_order="first_last"))
    s = result.roster.students[-1]
    assert (s.first_name, s.last_name, s.review_note) == ("Isabella", "de la Cruz", NOTE_MULTI_PART)
    assert "needs_review" in codes(result)
    assert not [i for i in result.issues if i.severity == "error"]  # warning only


def test_name_without_comma_in_comma_roster_is_flagged():
    rows = GOOD + [FakeRow("W5678901", "Alana", "Campbell", name_text="Alana Campbell")]
    result = parse_spec(_variant(rows))
    assert result.roster.students[-1].review_note == NOTE_NO_COMMA


def test_student_named_page_is_not_mistaken_for_footer():
    rows = GOOD + [FakeRow("W5678901", "Ellen", "Page", name_text="Ellen Page")]
    result = parse_spec(_variant(rows, layout="simple", name_order="first_last"))
    assert ("W5678901", "Ellen", "Page") in students(result)


def test_separate_first_and_last_name_columns():
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setFont("Fx", 9)
    c.drawString(40, 740, "Course: PHIL 320 Logic")
    c.drawString(300, 740, "Section: 10141")
    for x, h in ((40, "ID"), (120, "Last Name"), (260, "First Name"), (400, "Email")):
        c.drawString(x, 710, h)
    data = [("W1234567", "de la Cruz", "Maria José"), ("W2345678", "Smith", "Mary Ann")]
    for n, (w, last, first) in enumerate(data):
        y = 695 - n * 13
        c.drawString(40, y, w)
        c.drawString(120, y, last)
        c.drawString(260, y, first)
        c.drawString(400, y, f"{w.lower()}@apps.losrios.edu")  # email is not a second ID
    c.save()
    from roster_tool.pipeline import process_pdf
    result = process_pdf(buf.getvalue())
    assert students(result) == [(w, f, l) for (w, l, f) in data]
    assert all(s.review_note is None for s in result.roster.students)  # unambiguous from columns


def test_side_by_side_list_without_header():
    lines = [("W1234567", "Hewad Ahmadzai", "W2345678"), ("W3456789", "JuvWan Andrews", "W4567890")]
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setFont("Fx", 10)
    names = {"W2345678": "Bageer Alizada", "W4567890": "Neil Balbon"}
    for n, (w1, name1, w2) in enumerate(lines):
        y = 700 - n * 15
        c.drawString(50, y, w1)
        c.drawString(120, y, name1)
        c.drawString(300, y, w2)
        c.drawString(370, y, names[w2])
    c.save()
    from roster_tool.pipeline import process_pdf
    got = students(process_pdf(buf.getvalue()))
    assert sorted(got) == sorted([
        ("W1234567", "Hewad", "Ahmadzai"), ("W2345678", "Bageer", "Alizada"),
        ("W3456789", "JuvWan", "Andrews"), ("W4567890", "Neil", "Balbon"),
    ])


def test_parser_rules_are_configurable():
    # e.g. if real rosters turn out to use "First Last" only, the order can be pinned.
    parser = TableRosterParser(ParseRules(name_order="first_last"))
    doc = extract_pdf(build_pdf(SMALL))
    roster = parser.parse(doc).roster
    assert roster.students[0].first_name == "Hewad"
