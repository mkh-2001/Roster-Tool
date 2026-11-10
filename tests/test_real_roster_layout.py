"""Regression tests for the real Los Rios roster STRUCTURE (sections, numbered
"N. Last, First M" rows, pronoun cells, 7-digit IDs, class numbers in Meetings).

Every person, ID and code here is INVENTED (tests/fixture_builder.py:SECTIONED).
No real roster data is used or stored.
"""

import csv
import inspect
import io
import json
import re
from collections import Counter
from dataclasses import replace

import pytest

from conftest import make_roster
from fixture_builder import FIXTURE_DIR, SECTIONED, FakeRow, build_sectioned_pdf, make_people
from roster_tool.event_csv_generator import generate_csv
from roster_tool.help_sheet_generator import CURRENT_LABEL, WAITLIST_LABEL, generate_help_sheet, plan_layout
from roster_tool.models import CURRENT, WAITLIST, Roster, Student
from roster_tool.pdf_extractor import extract_pdf
from roster_tool.pipeline import process_pdf
from roster_tool.roster_parser import NOTE_NO_ID
from roster_tool.validation import OutputBlockedError, validate_roster

EXACT_HEADER = "Email,First Name,Last Name,SSO ID,Student ID"


@pytest.fixture(scope="module")
def result():
    return process_pdf((FIXTURE_DIR / SECTIONED.filename).read_bytes())


def people(students):
    return [(s.w_number, s.first_name, s.last_name) for s in students]


def expected(rows):
    return [(r.w_number, r.first, r.last) for r in rows]


def all_text(roster: Roster) -> str:
    return json.dumps(roster.to_dict(), ensure_ascii=False)


def pdf_text(pdf: bytes) -> str:
    return "\n".join(line.text for line in extract_pdf(pdf).lines)


def parse(spec):
    return process_pdf(build_sectioned_pdf(spec))


# --- Populations -----------------------------------------------------------------

def test_committed_fixture_matches_builder():
    assert (FIXTURE_DIR / SECTIONED.filename).read_bytes() == build_sectioned_pdf(SECTIONED)


def test_current_students_parsed(result):
    roster = result.roster
    assert people(roster.current_students) == expected(SECTIONED.current)
    assert all(s.status == CURRENT for s in roster.current_students)


def test_waitlisted_students_parsed(result):
    roster = result.roster
    assert people(roster.waitlist_students) == expected(SECTIONED.waitlist)
    assert all(s.status == WAITLIST for s in roster.waitlist_students)


def test_status_distinguishable_and_kept_through_ui_round_trip(result):
    roster = result.roster
    statuses = [s["status"] for s in roster.to_dict()["students"]]
    assert statuses == [CURRENT] * 14 + [WAITLIST] * 3
    again = Roster.from_dict(roster.to_dict())
    assert people(again.current_students) == people(roster.current_students)
    assert people(again.waitlist_students) == people(roster.waitlist_students)
    with pytest.raises(ValueError):
        Student.from_dict({"w_number": "W1234567", "first_name": "A", "last_name": "B", "status": "dropped"})


def test_clean_roster_has_no_issues(result):
    assert result.issues == []
    assert result.roster.expected_count == 14 and result.roster.expected_count_source == "row_numbers"
    assert result.roster.expected_waitlist_count == 3


def test_drops_excluded(result):
    text = all_text(result.roster)
    for row in SECTIONED.drops:
        assert row.w_number[1:] not in text and row.last not in text
    assert len(result.roster.students) == 14 + 3


def test_permission_numbers_excluded(result):
    text = all_text(result.roster)
    for code, _ in SECTIONED.permission:
        assert code not in text
    assert "W3999999" not in text  # a 7-digit add code never becomes a W number


def test_headings_not_parsed_as_students(result):
    names = " ".join(s.display_name for s in result.roster.students)
    for word in ("Current", "Wait", "List", "Drops", "Permission", "Student Name", "Student ID", "No Waitlist"):
        assert word not in names


# --- Name and ID handling ----------------------------------------------------------

def test_row_number_prefix_removed(result):
    assert not any(re.match(r"\d", s.last_name) for s in result.roster.students)
    assert result.roster.students[0].last_name == "Abernathy"


def test_pronouns_do_not_contaminate_names(result):
    for s in result.roster.students:
        assert "(" not in s.display_name and "/" not in s.display_name
    by_w = {s.w_number: s for s in result.roster.students}
    assert by_w["W3000001"].first_name == "Esperanza L"
    assert by_w["W3000009"].first_name == "Kai T"
    assert by_w["W3100001"].first_name == "Rosa M"


def test_pronouns_in_the_same_cell_as_the_name_are_removed():
    row = FakeRow("W3000001", "Esperanza L (she/her)", "Abernathy")
    spec = replace(SECTIONED, current=[row] + SECTIONED.current[1:], pronouns={})  # no separate pronoun cell
    first = parse(spec).roster.students[0]
    assert (first.last_name, first.first_name) == ("Abernathy", "Esperanza L")


def test_multi_part_names_intact(result):
    lasts = {s.last_name for s in result.roster.students}
    assert {"de la Cruz", "Van Der Berg", "St. James"} <= lasts
    assert any(s.first_name == "Mary Ann" for s in result.roster.students)


def test_hyphenated_names_intact(result):
    lasts = {s.last_name for s in result.roster.students}
    assert {"Bautista-Reyes", "Vongphachanh-Rasavong"} <= lasts


def test_apostrophes_and_accents_intact(result):
    by_w = {s.w_number: s for s in result.roster.students}
    assert by_w["W3000010"].last_name == "O'Neil"
    assert (by_w["W3000011"].last_name, by_w["W3000011"].first_name) == ("Peña", "José Luis")


def test_middle_initials_not_discarded(result):
    by_w = {s.w_number: s for s in result.roster.students}
    assert by_w["W3000001"].first_name == "Esperanza L"
    assert by_w["W3000003"].first_name == "Isabella M."
    assert by_w["W3100003"].first_name == "Olivia R"


def test_printed_seven_digit_id_becomes_w_number(result):
    # The PDF prints 3000001; the confirmed Los Rios rule makes it W3000001.
    assert "3000001" in pdf_text((FIXTURE_DIR / SECTIONED.filename).read_bytes())
    assert "W3000001" not in pdf_text((FIXTURE_DIR / SECTIONED.filename).read_bytes())
    assert all(re.fullmatch(r"W\d{7}", s.w_number) for s in result.roster.students)


def test_unrelated_seven_digit_numbers_are_not_converted():
    spec = replace(SECTIONED, header_extra=[("Reference:", "7000001")])
    roster = parse(spec).roster
    w_numbers = {s.w_number for s in roster.students}
    assert w_numbers == {r.w_number for r in SECTIONED.current + SECTIONED.waitlist}
    for number in ("7000001", "3999999", *(r.w_number[1:] for r in SECTIONED.drops)):
        assert "W" + number not in w_numbers and number not in all_text(roster)


def test_seven_digit_rule_also_applies_to_simple_rosters():
    from fixture_builder import FixtureSpec, build_pdf
    spec = FixtureSpec("t.pdf", "PHIL 320 Logic", "10141", [FakeRow("1234567", "Hewad", "Ahmadzai")])
    assert process_pdf(build_pdf(spec)).roster.students[0].w_number == "W1234567"


# --- Section / class number ------------------------------------------------------------

def test_section_is_first_class_number_in_meetings(result):
    assert result.roster.section == SECTIONED.expected_section == "20001"
    assert result.roster.uncertain_fields == []


def test_section_never_comes_from_numbers_outside_meetings():
    spec = replace(SECTIONED, meetings=["TBA Online"], header_extra=[("Reference:", "LEC 99999")])
    roster = parse(spec).roster
    assert roster.section == ""  # not 99999, not an ID, date or add code
    assert "missing_section" in {i.code for i in validate_roster(roster)}


def test_filename_cannot_influence_parsing(tmp_path):
    # The pipeline takes bytes only: there is no way to pass a filename in.
    assert list(inspect.signature(process_pdf).parameters) == ["data", "parser"]
    data = (FIXTURE_DIR / SECTIONED.filename).read_bytes()
    misleading = tmp_path / "Print Roster for (99999_0000).pdf"
    unrelated = tmp_path / "zz scan final v2.pdf"
    misleading.write_bytes(data)
    unrelated.write_bytes(data)
    a = process_pdf(misleading.read_bytes()).roster
    b = process_pdf(unrelated.read_bytes()).roster
    assert a.to_dict() == b.to_dict()
    assert a.section == "20001"


# --- Robustness ---------------------------------------------------------------------------

def test_no_waitlist_students():
    spec = replace(SECTIONED, waitlist=[])
    res = parse(spec)
    assert res.roster.waitlist_students == [] and res.roster.expected_waitlist_count is None
    assert len(res.roster.students) == 14 and res.issues == []
    sheet = generate_help_sheet(res.roster, "Jane", ["Tue", "Thu"])
    text = pdf_text(sheet.pdf)
    assert WAITLIST_LABEL not in text and CURRENT_LABEL not in text  # compact: no empty Wait List


def test_current_students_spanning_pages():
    many = [FakeRow(w.replace("W1", "W4", 1), f, l) for (w, f, l) in make_people(60, seed=3)]
    res = parse(replace(SECTIONED, current=many))
    assert people(res.roster.current_students) == expected(many)
    assert {s.source_page for s in res.roster.current_students} == {1, 2}
    assert people(res.roster.waitlist_students) == expected(SECTIONED.waitlist)
    assert res.issues == []


def test_malformed_and_missing_ids_still_block_output():
    bad = list(SECTIONED.current)
    bad[2] = FakeRow("W12345", "Isabella M.", "de la Cruz")  # printed as-is: malformed
    bad[5] = FakeRow("", "Samir", "Hakimi")                  # no ID printed at all
    res = parse(replace(SECTIONED, current=bad))
    codes = {i.code for i in res.issues}
    assert {"malformed_w_number", "missing_w_number"} <= codes
    assert res.roster.students[2].w_number == "W12345"  # kept verbatim, not guessed
    assert res.roster.students[5].review_note == NOTE_NO_ID
    with pytest.raises(OutputBlockedError):
        generate_csv(res.roster)
    with pytest.raises(OutputBlockedError):
        generate_help_sheet(res.roster, "Jane", ["Mon"])


def test_waitlist_count_mismatch_blocks():
    roster = make_roster(3, expected_waitlist_count=2)
    roster.students[2].status = WAITLIST
    issue = next(i for i in validate_roster(roster) if i.code == "count_mismatch")
    assert issue.field == "expected_waitlist_count"


# --- Event CSV ------------------------------------------------------------------------------

def test_csv_includes_current_and_waitlist_as_ordinary_rows(result):
    text, rows = generate_csv(result.roster)
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    assert text.split("\r\n")[0] == EXACT_HEADER
    assert parsed[0] == ["Email", "First Name", "Last Name", "SSO ID", "Student ID"]
    assert len(parsed) == 1 + 14 + 3
    assert all(len(row) == 5 for row in parsed)
    assert [row[3] for row in parsed[1:]] == [r.w_number for r in SECTIONED.current + SECTIONED.waitlist]
    assert parsed[15] == ["W3100001@apps.losrios.edu", "Rosa M", "Castellanos", "W3100001", "W3100001"]
    assert "wait" not in text.lower() and "current" not in text.lower()


def test_csv_excludes_drops_and_permission_numbers(result):
    text, _ = generate_csv(result.roster)
    for row in SECTIONED.drops:
        assert row.w_number[1:] not in text and row.last not in text
    for code, _ in SECTIONED.permission:
        assert code not in text


# --- Help sheet -------------------------------------------------------------------------------

def test_help_sheet_separates_current_and_waitlist(result):
    plan = plan_layout(result.roster, ["Tuesday"])[0]
    labels = [(i, item.label) for i, item in enumerate(plan.items) if item.student is None]
    assert labels == [(0, CURRENT_LABEL), (15, WAITLIST_LABEL)]
    assert [item.student.status for item in plan.items[1:15]] == [CURRENT] * 14
    assert [item.student.status for item in plan.items[16:]] == [WAITLIST] * 3


@pytest.mark.parametrize("days", [["Tuesday"], ["Tuesday", "Thursday"], ["Monday", "Wednesday", "Friday"]])
def test_waitlist_section_for_every_selected_day(result, days):
    sheet = generate_help_sheet(result.roster, "Jane", days)
    text = pdf_text(sheet.pdf)
    assert sheet.page_count == 1
    assert text.count(WAITLIST_LABEL) == len(days)
    assert text.count(CURRENT_LABEL) == len(days)
    counts = Counter(re.findall(r"\bW\d{7}\b", text))
    assert set(counts) == {s.w_number for s in result.roster.students}
    assert set(counts.values()) == {len(days)}  # every eligible student once per day


def test_help_sheet_excludes_drops_and_permission_numbers(result):
    text = pdf_text(generate_help_sheet(result.roster, "Jane", ["Tue", "Thu"]).pdf)
    for row in SECTIONED.drops:
        assert row.w_number[1:] not in text and row.last not in text
    for code, _ in SECTIONED.permission:
        assert code not in text
    assert "Drops" not in text and "Permission" not in text


def test_long_waitlist_paginates_with_continued_labels():
    roster = make_roster(70)
    for s in roster.students[50:]:
        s.status = WAITLIST
    sheet = generate_help_sheet(roster, "Jane", ["Mon", "Wed"])
    assert sheet.page_count == 3  # 70 students + 2 group labels at 29 double-spaced rows per page
    for plan in sheet.plans:
        for col in plan.columns:
            assert plan.items[col.end - 1].student is not None  # a label is never orphaned
            first = plan.items[col.start]
            if first.student is not None and col.start > 0:
                assert col.group_label in (f"{CURRENT_LABEL} (cont.)", f"{WAITLIST_LABEL} (cont.)")
    counts = Counter(re.findall(r"\bW\d{7}\b", pdf_text(sheet.pdf)))
    assert len(counts) == 70 and set(counts.values()) == {2}
