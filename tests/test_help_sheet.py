import re
from collections import Counter

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest

from conftest import make_roster
from fixture_builder import LARGE, MULTIPAGE, SMALL
from roster_tool.help_sheet_generator import (
    MARGIN, MAX_PITCH, MIN_PITCH, PAGE_H, PAGE_W, ROW_PITCH, HelpSheetInputError, day_groups, generate_help_sheet,
    normalize_days, plan_layout,
)
from roster_tool.models import Student
from roster_tool.pdf_extractor import extract_pdf
from roster_tool.validation import OutputBlockedError


def roster_of(fixture_results, spec):
    return fixture_results[spec.filename][1].roster


def w_counts(pdf: bytes) -> Counter:
    """How many times each W number is printed in the generated PDF."""
    doc = extract_pdf(pdf)
    return Counter(m for line in doc.lines for m in re.findall(r"\bW\d{7}\b", line.text))


# --- Day selection --------------------------------------------------------------

def test_days_normalized_to_week_order_without_repeats():
    assert normalize_days(["thu", "Tuesday", "Tue"]) == ["Tuesday", "Thursday"]
    assert normalize_days(["Sun", "Mon"]) == ["Monday", "Sunday"]


@pytest.mark.parametrize("days", [[], None, ["Funday"], "Monday"])
def test_invalid_day_selection(days):
    with pytest.raises(HelpSheetInputError):
        normalize_days(days)


@pytest.mark.parametrize("n,expected", [(1, [1]), (2, [2]), (3, [3]), (4, [2, 2]), (5, [3, 2]), (6, [3, 3]), (7, [3, 2, 2])])
def test_day_groups_balanced_max_three_columns(n, expected):
    groups = day_groups(list(range(n)))
    assert [len(g) for g in groups] == expected


# --- Layouts ------------------------------------------------------------------------

@pytest.mark.parametrize("days,columns", [
    (["Tuesday"], 1), (["Tuesday", "Thursday"], 2), (["Monday", "Wednesday", "Friday"], 3),
])
def test_one_two_three_day_layouts_side_by_side(fixture_results, days, columns):
    roster = roster_of(fixture_results, SMALL)
    result = generate_help_sheet(roster, "Jane Tutor", days)
    assert result.page_count == 1
    plan = result.plans[0]
    assert [c.day for c in plan.columns] == days
    assert len(plan.columns) == columns
    assert all((c.start, c.end) == (0, 16) for c in plan.columns)  # each column is the full list
    assert set(w_counts(result.pdf).values()) == {columns}  # every W number once per day


def header_lines(pdf: bytes) -> list[str]:
    """Text lines above the first day column on page 1."""
    lines = extract_pdf(pdf).pages[0].lines
    stop = next(i for i, line in enumerate(lines) if "Date:" in line.text)
    return [line.text for line in lines[:stop]]


def test_header_shows_class_professor_tutor_location_term(fixture_results):
    roster = roster_of(fixture_results, SMALL)  # gives course and term, no professor or meetings
    result = generate_help_sheet(roster, "Jane Tutor", ["Tue", "Thu"])
    assert header_lines(result.pdf) == [
        "Class: PHIL 320 Logic and Critical Reasoning", "Professor: —", "Tutor: Jane Tutor",
        "Meetings Location: —", "Term: Fall 2026",
    ]
    text = "\n".join(line.text for line in extract_pdf(result.pdf).lines)
    assert "Section:" not in text and roster.section not in text  # section is not printed
    assert text.count("Date:") == 2
    assert "Ahmadzai, Hewad" in text  # Last, First


def test_rows_have_checkboxes(fixture_results):
    roster = roster_of(fixture_results, SMALL)
    result = generate_help_sheet(roster, "Jane", ["Mon", "Wed", "Fri"])
    page = pdfium.PdfDocument(result.pdf)[0]
    squares = 0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]):
        left, bottom, right, top = obj.get_bounds()
        if 7 < right - left < 11 and 7 < top - bottom < 11:
            squares += 1
    assert squares == 16 * 3


def test_us_letter_and_printer_safe_margins(fixture_results):
    roster = roster_of(fixture_results, MULTIPAGE)
    result = generate_help_sheet(roster, "Jane Tutor", ["Mon", "Wed", "Fri"])
    pdf = pdfium.PdfDocument(result.pdf)
    for page in pdf:
        assert page.get_size() == pytest.approx((612, 792))
        for obj in page.get_objects():
            left, bottom, right, top = obj.get_bounds()
            assert left >= MARGIN - 1 and right <= PAGE_W - MARGIN + 1
            assert bottom >= MARGIN - 1 and top <= PAGE_H - MARGIN + 1


def test_pagination_keeps_every_student(fixture_results):
    roster = roster_of(fixture_results, MULTIPAGE)  # 95 students
    result = generate_help_sheet(roster, "Jane Tutor", ["Mon", "Wed", "Fri"])
    assert result.page_count == 4  # 29 double-spaced rows per page
    counts = w_counts(result.pdf)
    assert len(counts) == 95 and set(counts.values()) == {3}
    for plan in result.plans:
        assert MIN_PITCH <= plan.pitch <= MAX_PITCH
    # pages cover the roster contiguously, in order
    slices = [(p.columns[0].start, p.columns[0].end) for p in result.plans]
    assert slices[0][0] == 0 and slices[-1][1] == 95
    assert all(a[1] == b[0] for a, b in zip(slices, slices[1:]))
    assert all(c.continued for c in result.plans[1].columns)


def test_large_class_is_never_shrunk_below_minimum(fixture_results):
    roster = make_roster(400)
    plans = plan_layout(roster, ["Tue", "Thu"])
    assert all(p.pitch >= MIN_PITCH for p in plans)
    covered = sum(p.columns[0].end - p.columns[0].start for p in plans)
    assert covered == 400


def test_single_day_long_list_continues_in_second_column(fixture_results):
    roster = roster_of(fixture_results, MULTIPAGE)
    result = generate_help_sheet(roster, "Jane", ["Tuesday"])
    assert result.page_count == 2  # 95 students, two double-spaced columns per sheet
    cols = result.plans[0].columns
    assert [(c.day, c.continued, c.show_date) for c in cols] == [("Tuesday", False, True), ("Tuesday", True, False)]
    assert set(w_counts(result.pdf).values()) == {1}


def test_single_day_short_list_uses_one_column(fixture_results):
    result = generate_help_sheet(roster_of(fixture_results, SMALL), "Jane", ["Tuesday"])
    assert len(result.plans[0].columns) == 1


def test_five_days_split_across_pages(fixture_results):
    roster = roster_of(fixture_results, LARGE)
    result = generate_help_sheet(roster, "Jane", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    groups = [[c.day for c in p.columns] for p in result.plans]
    first, second = ["Monday", "Tuesday", "Wednesday"], ["Thursday", "Friday"]
    assert groups == [first, first, second, second]  # 48 students: 2 pages per day group
    assert set(w_counts(result.pdf).values()) == {5}


def test_long_names_wrap_instead_of_truncating():
    long_name = Student("W1234567", "Maria Guadalupe Esperanza Concepción", "Hernandez-Rodriguez de la Fuente y Castillo")
    roster = make_roster(2)
    roster.students.append(long_name)
    result = generate_help_sheet(roster, "Jane", ["Mon", "Wed", "Fri"])
    fit = result.plans[0].fits[2]
    assert len(fit.lines) >= 2
    assert " ".join(fit.lines) == long_name.display_name  # nothing dropped
    text = " ".join(line.text for line in extract_pdf(result.pdf).lines)
    assert "Castillo, Maria" in text or "Castillo," in text


# --- Inputs -------------------------------------------------------------------------

def test_tutor_name_required():
    for tutor in ("", "   ", None):
        with pytest.raises(HelpSheetInputError, match="tutor"):
            generate_help_sheet(make_roster(), tutor, ["Mon"])


def test_course_and_section_required_for_help_sheet():
    with pytest.raises(HelpSheetInputError, match="course"):
        generate_help_sheet(make_roster(course_title=""), "Jane", ["Mon"])
    with pytest.raises(HelpSheetInputError, match="section"):
        generate_help_sheet(make_roster(section=""), "Jane", ["Mon"])


def test_help_sheet_refuses_roster_with_errors():
    roster = make_roster(3)
    roster.students[1].w_number = ""
    with pytest.raises(OutputBlockedError):
        generate_help_sheet(roster, "Jane", ["Mon"])


def test_tutor_name_is_printed_as_entered():
    result = generate_help_sheet(make_roster(), "  María  O'Connor ", ["Mon"])
    assert "Tutor: María O'Connor" in header_lines(result.pdf)  # whitespace tidied, nothing else changed


def test_meetings_location_is_prefilled_from_the_roster_and_editable():
    def location_line(**kwargs):
        roster = make_roster(3, meetings_location=kwargs.pop("roster_value", ""))
        lines = header_lines(generate_help_sheet(roster, "Jane", ["Tue"], **kwargs).pdf)
        return next(line for line in lines if line.startswith("Meetings Location:"))

    assert location_line(roster_value="Science 415") == "Meetings Location: Science 415"  # not sent: roster's
    assert location_line(roster_value="Science 415", meetings_location=" Room 5 ") == "Meetings Location: Room 5"
    assert location_line(meetings_location="Library  210") == "Meetings Location: Library 210"  # entered by hand
    assert location_line(roster_value="Science 415", meetings_location="") == "Meetings Location: —"  # cleared
    assert location_line() == "Meetings Location: —"  # optional
    with pytest.raises(HelpSheetInputError, match="Meetings Location"):
        generate_help_sheet(make_roster(3), "Jane", ["Tue"], meetings_location="x" * 301)


def test_header_values_come_from_the_roster_and_fit_the_page():
    roster = make_roster(3, professor="de la Vega, Ines", meetings_location="Science 415",
                         term="Fall 2026 | Full Semester | August 22, 2026 - December 17, 2026")
    result = generate_help_sheet(roster, "Jane", ["Tue"])
    assert header_lines(result.pdf) == [
        "Class: PHIL 320 Logic and Critical Reasoning", "Professor: de la Vega, Ines", "Tutor: Jane",
        "Meetings Location: Science 415", "Term: Fall 2026 | Full Semester | August 22, 2026 - December 17, 2026",
    ]
    long_roster = make_roster(3, course_title="ENGL 300 " + "Very Long Course Title " * 8, professor="X" * 120)
    long_sheet = generate_help_sheet(long_roster, "T" * 100, ["Tue"], meetings_location="Room " * 60)
    page = pdfium.PdfDocument(long_sheet.pdf)[0]
    for obj in page.get_objects():  # long values are scaled down, never cut off or off the page
        left, _, right, _ = obj.get_bounds()
        assert left >= MARGIN - 1 and right <= PAGE_W - MARGIN + 1


def test_student_rows_are_double_spaced():
    assert ROW_PITCH == pytest.approx(2.0 * 1.2 * 9.0)  # line spacing 2.0 for 9 pt names
    result = generate_help_sheet(make_roster(20), "Jane", ["Tuesday", "Thursday"])
    assert all(plan.pitch == pytest.approx(ROW_PITCH) for plan in result.plans)
    # Measured on the printed page: consecutive rows in a column are one row pitch apart.
    ys = [line.y for line in extract_pdf(result.pdf).pages[0].lines if re.search(r"\bW\d{7}\b", line.text)]
    gaps = [round(a - b, 1) for a, b in zip(ys, ys[1:])]
    assert len(ys) == 20 and set(gaps) == {round(ROW_PITCH, 1)}


def test_wrapped_names_do_not_overlap_the_next_row():
    roster = make_roster(3)
    roster.students[1].first_name = "Maria Guadalupe Esperanza Concepción"
    roster.students[1].last_name = "Hernandez-Rodriguez de la Fuente y Castillo"
    result = generate_help_sheet(roster, "Jane", ["Mon", "Wed", "Fri"])
    fit = result.plans[0].fits[1]
    assert len(fit.lines) >= 2  # wraps (4 lines in a narrow 3-day column)
    first_column = [(line.y, line.cells[0].text) for line in extract_pdf(result.pdf).pages[0].lines
                    if line.cells[0].x0 < 200]
    w_ys = [y for y, text in first_column if re.match(r"W\d{7}\b", text)]
    name_ys = [y for y, text in first_column if text in fit.lines]
    assert len(w_ys) == 3 and len(name_ys) == len(fit.lines)
    assert all(a - b >= ROW_PITCH - 0.1 for a, b in zip(w_ys, w_ys[1:]))  # the tall row pushes the next one down
    above, _, below = w_ys
    # every wrapped line stays inside its own row, clear of the neighbouring rows' half-pitch bands
    assert all(below + ROW_PITCH / 2 < y < above - ROW_PITCH / 2 for y in name_ys)


def test_unprintable_characters_produce_warning():
    roster = make_roster(1)
    roster.students[0].last_name = "王"  # not in the bundled Latin font
    result = generate_help_sheet(roster, "Jane", ["Mon"])
    assert result.warnings and "王" in result.warnings[0]
