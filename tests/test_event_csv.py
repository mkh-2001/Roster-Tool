"""Event Invites CSV. These tests lock down the import format - do not loosen them."""

import csv
import io

import pytest

from conftest import make_roster
from roster_tool.event_csv_generator import EMAIL_DOMAIN, HEADER, csv_filename, generate_csv, generate_csv_bytes
from roster_tool.models import Roster, Student
from roster_tool.validation import OutputBlockedError

EXACT_HEADER = "Email,First Name,Last Name,SSO ID,Student ID"


def hewad_roster(*extra):
    return Roster("PHIL 320 Logic and Critical Reasoning", "10141", [Student("W1234567", "Hewad", "Ahmadzai"), *extra])


# --- CRITICAL (spec section 34) ------------------------------------------------

def test_critical_header_is_exact():
    text, _ = generate_csv(hewad_roster())
    assert text.split("\r\n")[0] == EXACT_HEADER


def test_critical_hewad_row_is_exact():
    text, _ = generate_csv(hewad_roster())
    assert text.split("\r\n")[1] == "W1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567"


def test_critical_complete_example_file():
    roster = hewad_roster(Student("W2345678", "Bageer", "Alizada"))
    assert generate_csv_bytes(roster) == (
        b"Email,First Name,Last Name,SSO ID,Student ID\r\n"
        b"W1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567\r\n"
        b"W2345678@apps.losrios.edu,Bageer,Alizada,W2345678,W2345678\r\n"
    )


# --- Field rules ----------------------------------------------------------------

def test_column_order_and_names():
    assert HEADER == ("Email", "First Name", "Last Name", "SSO ID", "Student ID")
    assert EMAIL_DOMAIN == "apps.losrios.edu"


def test_email_keeps_w_and_has_no_spaces(fixture_results):
    for _, result in fixture_results.values():
        _, rows = generate_csv(result.roster)
        for row, s in zip(rows, result.roster.students):
            assert row[0] == f"{s.w_number}@apps.losrios.edu"
            assert row[0].startswith("W") and " " not in row[0]


def test_sso_id_and_student_id_equal_w_number(fixture_results):
    for _, result in fixture_results.values():
        _, rows = generate_csv(result.roster)
        assert all(row[3] == row[4] == s.w_number for row, s in zip(rows, result.roster.students))


def test_one_row_per_student_and_only_five_columns(fixture_results):
    for spec, result in fixture_results.values():
        text, _ = generate_csv(result.roster)
        parsed = list(csv.reader(io.StringIO(text, newline="")))
        assert len(parsed) == len(spec.rows) + 1
        assert all(len(row) == 5 for row in parsed)
        assert result.roster.course_title not in text  # no course, section or tutor metadata


@pytest.mark.parametrize("first,last", [
    ('Mary, Ann', "Smith"),           # comma
    ('Jo "JJ"', "O'Neil"),            # quotes and apostrophe
    ("Ana", "Line\nBreak"),           # newline (should never happen, but must not corrupt the file)
])
def test_csv_escaping_roundtrips(first, last):
    roster = hewad_roster(Student("W2345678", first, last))
    text, _ = generate_csv(roster)
    rows = list(csv.reader(io.StringIO(text, newline="")))
    assert rows[2] == ["W2345678@apps.losrios.edu", first, last, "W2345678", "W2345678"]


def test_quotes_are_rfc4180_doubled():
    text, _ = generate_csv(hewad_roster(Student("W2345678", 'Jo "JJ"', "Smith")))
    assert text.split("\r\n")[2] == 'W2345678@apps.losrios.edu,"Jo ""JJ""",Smith,W2345678,W2345678'


def test_utf8_without_bom():
    data = generate_csv_bytes(hewad_roster(Student("W2345678", "Thảo Vy", "Nguyễn"), Student("W3456789", "José", "Peña")))
    assert not data.startswith(b"\xef\xbb\xbf")
    assert data.startswith(EXACT_HEADER.encode("ascii"))
    text = data.decode("utf-8")
    assert "Thảo Vy,Nguyễn" in text and "José,Peña" in text


def test_csv_refuses_roster_with_errors():
    roster = hewad_roster(Student("W123", "Bad", "Id"))
    with pytest.raises(OutputBlockedError) as exc:
        generate_csv(roster)
    assert exc.value.issues[0].code == "malformed_w_number"


def test_csv_does_not_need_course_or_section():
    roster = make_roster(course_title="", section="")
    text, rows = generate_csv(roster)
    assert len(rows) == 3


def test_csv_filename_is_safe():
    assert csv_filename(hewad_roster()) == "event_invites_PHIL_320_10141.csv"


def test_help_sheet_header_fields_never_reach_the_csv():
    plain = make_roster(5)
    dressed = make_roster(5, professor="Staff", term="Fall 2026", meetings_location="Science 415")
    assert generate_csv(dressed) == generate_csv(plain)
    assert generate_csv_bytes(dressed) == generate_csv_bytes(plain)
