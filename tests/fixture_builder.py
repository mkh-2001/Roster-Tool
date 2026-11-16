"""Builds FAKE roster PDFs for development and tests.

ALL NAMES AND W NUMBERS HERE ARE INVENTED TEST DATA.

These layouts are our own guesses at what a roster might look like. Passing
tests against them does NOT prove the parser works on the real roster system's
PDFs (see REAL_ROSTER_INTEGRATION.md).

Each fixture is defined as data (`FixtureSpec`) next to the code that draws it,
so tests can compare parser output against the known truth.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import A3, letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = Path(__file__).resolve().parent.parent / "roster_tool" / "assets" / "fonts"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

_fonts_ready = False


def _fonts() -> None:
    global _fonts_ready
    if not _fonts_ready:
        pdfmetrics.registerFont(TTFont("Fx", str(FONT_DIR / "DejaVuSansCondensed.ttf")))
        pdfmetrics.registerFont(TTFont("Fx-Bold", str(FONT_DIR / "DejaVuSansCondensed-Bold.ttf")))
        _fonts_ready = True


@dataclass
class FakeRow:
    w_number: str  # as printed; may be deliberately malformed or empty
    first: str
    last: str
    name_text: str = ""  # as printed; defaults per layout

    def printed_name(self, order: str) -> str:
        if self.name_text:
            return self.name_text
        return f"{self.last}, {self.first}" if order == "last_first" else f"{self.first} {self.last}"


@dataclass
class FixtureSpec:
    filename: str
    course_title: str
    section: str
    rows: list[FakeRow]
    layout: str = "table"  # "table" | "simple"
    name_order: str = "last_first"
    rows_per_page: int = 40
    stated_total: int | None = None
    row_numbers: bool = True
    extra: dict = field(default_factory=dict)


# --- Fake people -----------------------------------------------------------

SPEC_SECTION_8 = [  # exactly the representative data from the specification
    ("W1234567", "Hewad", "Ahmadzai"), ("W2345678", "Bageer", "Alizada"),
    ("W3456789", "JuvWan", "Andrews"), ("W4567890", "Neil", "Balbon"),
    ("W5678901", "Alana", "Campbell"), ("W6789012", "Daniel", "Chen"),
    ("W7890123", "Maria", "Garcia"), ("W8901234", "Carlos", "Hernandez"),
    ("W9012345", "Emily", "Johnson"), ("W0123456", "Farid", "Khan"),
    ("W1123456", "Sophia", "Lee"), ("W2123456", "David", "Martinez"),
    ("W3123456", "Kevin", "Nguyen"), ("W4123456", "Aisha", "Patel"),
    ("W5123456", "Elena", "Rodriguez"), ("W6123456", "Jordan", "Williams"),
]

TRICKY = [  # (first, last) — hyphens, apostrophes, particles, suffixes, accents, long names
    ("Hewad", "Ahmadzai"), ("Maria Fernanda", "Garcia-Lopez"), ("Sean", "O'Brien"),
    ("Gianna", "D'Angelo"), ("Isabella", "de la Cruz"), ("Pieter", "Van Der Berg"),
    ("Yusuf", "Abu Bakr"), ("Olivia", "St. James"), ("Anna-Marie", "Smith-Jones"),
    ("Michael", "Johnson Jr."), ("Thảo Vy", "Nguyễn"), ("Jürgen", "Müller"),
    ("José Luis", "Peña"), ("Khamphoui Alexander", "Vongphachanh-Rasavong"),
    ("Juan Carlos", "Ramírez de Arellano"), ("Awa", "N'Diaye"), ("Daniel Wei", "Chen"),
    ("Omar", "Al-Hassan"), ("Ewan", "MacDonald"), ("Zofia", "Kowalczyk"),
    ("Aoife", "Ó Súilleabháin"), ("Mary Ann", "Lee"),
]

FIRST_POOL = [
    "Aaliyah", "Bilal", "Chloe", "Dmitri", "Esperanza", "Fatima", "Gabriel", "Hana",
    "Ibrahim", "Jasmine", "Kai", "Leilani", "Mateo", "Nadia", "Oscar", "Priya",
    "Quinn", "Rosa", "Samir", "Tatiana", "Uriel", "Valentina", "Wesley", "Ximena",
    "Yara", "Zane", "Arjun", "Bao", "Celeste", "Dawit",
]
LAST_POOL = [
    "Abernathy", "Bautista", "Castellanos", "Dang", "Espinoza", "Fofanah", "Gill",
    "Hakimi", "Ivanova", "Jaramillo", "Kaur", "Lopez", "Moua", "Nakamura", "Okafor",
    "Petrov", "Qureshi", "Rahimi", "Sandoval", "Tran", "Udeh", "Vang", "Wong",
    "Xiong", "Yilmaz", "Zamora", "Bui", "Cortez", "Duarte", "Ellis",
]


def make_people(count: int, seed: int) -> list[tuple[str, str, str]]:
    """Deterministic fake (w_number, first, last) list sorted like a roster."""
    people = list(TRICKY)
    n_first, n_last = len(FIRST_POOL), len(LAST_POOL)
    # Walks all n_first * n_last combinations without repeats (7*30 = 0 mod 30).
    for i in range(n_first * n_last):
        if len(people) >= count:
            break
        pair = (FIRST_POOL[i % n_first], LAST_POOL[(i * 7 + i // n_first + seed) % n_last])
        if pair not in people:
            people.append(pair)
    if len(people) < count:
        raise ValueError(f"cannot make {count} unique fake people")
    people = people[:count]
    people.sort(key=lambda p: (p[1].lower(), p[0].lower()))
    return [(f"W{(1000003 + seed * 100000 + n * 7919) % 10_000_000:07d}", f, l) for n, (f, l) in enumerate(people)]


def rows_from(people) -> list[FakeRow]:
    return [FakeRow(w, f, l) for (w, f, l) in people]


SMALL = FixtureSpec(
    filename="mock_roster_small.pdf",
    course_title="PHIL 320 Logic and Critical Reasoning",
    section="10141",
    rows=rows_from(SPEC_SECTION_8),
    layout="simple",
    name_order="first_last",
)

LARGE = FixtureSpec(
    filename="mock_roster_large.pdf",
    course_title="HIST 310 History of the United States",
    section="20517",
    rows=rows_from(make_people(48, seed=1)),
    rows_per_page=48,
    stated_total=48,
)

MULTIPAGE = FixtureSpec(
    filename="mock_roster_multipage.pdf",
    course_title="BIOL 310 General Biology",
    section="31877",
    rows=rows_from(make_people(95, seed=2)),
    rows_per_page=40,
    stated_total=95,
)

ALL_FIXTURES = [SMALL, LARGE, MULTIPAGE]


# --- Drawing ---------------------------------------------------------------

def build_pdf(spec: FixtureSpec) -> bytes:
    _fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1, encrypt=spec.extra.get("encrypt"))
    c.setTitle("FAKE roster fixture")
    if spec.layout == "simple":
        _draw_simple(c, spec)
    else:
        _draw_table(c, spec)
    c.save()
    return buf.getvalue()


def _draw_simple(c: canvas.Canvas, spec: FixtureSpec) -> None:
    width, height = letter
    y = height - 60
    c.setFont("Fx-Bold", 13)
    c.drawString(50, y, "Example Community College")
    y -= 16
    c.setFont("Fx", 9)
    c.drawString(50, y, "Class Roster  (FAKE DEVELOPMENT DATA)")
    y -= 26
    c.setFont("Fx", 10.5)
    c.drawString(50, y, f"Course: {spec.course_title}")
    y -= 15
    c.drawString(50, y, f"Section: {spec.section}")
    y -= 15
    c.drawString(50, y, "Term: Fall 2026")
    y -= 30
    c.setFont("Fx", 10.5)
    for row in spec.rows:
        c.drawString(50, y, row.w_number)
        c.drawString(130, y, row.printed_name(spec.name_order))
        y -= 15


_COLS = {"num": 40, "id": 64, "name": 128, "units": 372, "status": 412, "program": 470}


def _draw_table(c: canvas.Canvas, spec: FixtureSpec) -> None:
    width, height = letter
    per_page = spec.rows_per_page
    pages = max(1, -(-len(spec.rows) // per_page))
    for p in range(pages):
        y = height - 46
        c.setFont("Fx-Bold", 12)
        c.drawString(40, y, "Example Community College — Class Roster")
        c.setFont("Fx", 8)
        c.drawRightString(width - 40, y, f"Page {p + 1} of {pages}")
        y -= 18
        c.setFont("Fx", 9.5)
        c.drawString(40, y, f"Course: {spec.course_title}")
        c.drawString(360, y, f"Section: {spec.section}")
        y -= 13
        c.drawString(40, y, "Term: Fall 2026")
        c.drawString(160, y, "Instructor: Staff")
        if spec.stated_total is not None and p == 0:
            c.drawString(360, y, f"Total Enrolled: {spec.stated_total}")
        y -= 22
        c.setFont("Fx-Bold", 8.5)
        if spec.row_numbers:
            c.drawString(_COLS["num"], y, "#")
        c.drawString(_COLS["id"], y, "Student ID")
        c.drawString(_COLS["name"], y, "Name")
        c.drawString(_COLS["units"], y, "Units")
        c.drawString(_COLS["status"], y, "Status")
        c.drawString(_COLS["program"], y, "Program")
        c.setLineWidth(0.5)
        c.line(40, y - 4, width - 40, y - 4)
        y -= 15
        c.setFont("Fx", 8.5)
        for n in range(p * per_page, min(len(spec.rows), (p + 1) * per_page)):
            row = spec.rows[n]
            if spec.row_numbers:
                c.drawRightString(_COLS["num"] + 12, y, str(spec.extra.get("row_number_override", {}).get(n, n + 1)))
            if row.w_number:
                c.drawString(_COLS["id"], y, row.w_number)
            c.drawString(_COLS["name"], y, row.printed_name(spec.name_order))
            c.drawString(_COLS["units"], y, "3.00")
            c.drawString(_COLS["status"], y, "Enrolled")
            c.drawString(_COLS["program"], y, "Undeclared" if n % 3 else "AA Liberal Arts")
            y -= 12.5
        c.setFont("Fx", 7.5)
        c.drawString(40, 30, "Printed 11/09/2026 — FAKE DATA FOR DEVELOPMENT")
        c.drawRightString(width - 40, 30, f"Page {p + 1} of {pages}")
        c.showPage()


# --- Sectioned layout (structure of the real Los Rios "Print Roster") ---------
#
# Reproduces only the STRUCTURE observed in a real roster (Nov 2026): A3 pages,
# a labelled header block with class numbers inside "Meetings:", and
# "Current Students" / "Wait List" / "Drops" / "Permission Numbers" sections
# whose rows look like "N. Last, First M | (pronouns) | 1234567" (a 7-digit ID
# printed without the W). Every person, ID and code below is INVENTED.

@dataclass
class SectionedSpec:
    filename: str
    course_title: str
    meetings: list[str]                 # first line is labelled "Meetings:"
    current: list[FakeRow]              # FakeRow.w_number = expected W number
    waitlist: list[FakeRow]
    drops: list[FakeRow]
    permission: list[tuple[str, str]]   # (add code, optional name)
    pronouns: dict[str, str] = field(default_factory=dict)  # w_number -> "(she/her)"
    expected_section: str = ""
    header_extra: list[tuple[str, str]] = field(default_factory=list)  # extra (label, value) lines


def printed_id(w_number: str) -> str:
    """How the roster prints an ID: valid W numbers lose their W; anything else is printed as given."""
    return w_number[1:] if re.fullmatch(r"W\d{7}", w_number) else w_number


SECTIONED = SectionedSpec(
    filename="mock_roster_sections.pdf",
    course_title="ANTH 310 Cultural Anthropology",
    meetings=["MoWe 8:00 am - 9:15 am Room 101 LEC 20001", "MoWe 9:30 am - 10:45 am Room 102 LAB 20002"],
    current=[
        FakeRow("W3000001", "Esperanza L", "Abernathy"),
        FakeRow("W3000002", "Gabriel", "Bautista-Reyes"),          # hyphenated
        FakeRow("W3000003", "Isabella M.", "de la Cruz"),         # multi-part + initial with period
        FakeRow("W3000004", "Bao", "Dang"),
        FakeRow("W3000005", "Dawit K", "Fofanah"),
        FakeRow("W3000006", "Samir", "Hakimi"),
        FakeRow("W3000007", "Zofia A", "Kowalczyk"),
        FakeRow("W3000008", "Mary Ann", "Lee"),                   # two-word given name
        FakeRow("W3000009", "Kai T", "Moua"),
        FakeRow("W3000010", "Sean P", "O'Neil"),                  # apostrophe
        FakeRow("W3000011", "José Luis", "Peña"),                 # accents
        FakeRow("W3000012", "Pieter J", "Van Der Berg"),          # multi-part
        FakeRow("W3000013", "Khamphoui", "Vongphachanh-Rasavong"),
        FakeRow("W3000014", "Yer", "Xiong"),
    ],
    waitlist=[
        FakeRow("W3100001", "Rosa M", "Castellanos"),
        FakeRow("W3100002", "Kenji", "Nakamura"),
        FakeRow("W3100003", "Olivia R", "St. James"),
    ],
    drops=[
        FakeRow("W3200001", "Wesley J", "Ellis"),
        FakeRow("W3200002", "Jasmine", "Gill"),
        FakeRow("W3200003", "Tatiana", "Ivanova"),
        FakeRow("W3200004", "Oscar L", "Jaramillo"),
        FakeRow("W3200005", "Priya", "Rahimi"),
        FakeRow("W3200006", "Leilani", "Tran"),
        FakeRow("W3200007", "Uriel", "Udeh"),        # these continue on page 2, under no new heading
        FakeRow("W3200008", "Ximena", "Zamora"),
    ],
    # Add codes; one is deliberately 7 digits (must never become a W number).
    permission=[("410201", "Dang, Bao"), ("410202", "Xiong, Yer"), ("410203", ""), ("3999999", ""), ("410205", "")],
    pronouns={"W3000001": "(she/her/hers)", "W3000003": "(she/her)", "W3000006": "(he/him/his)",
              "W3000009": "(they/them/theirs)", "W3100001": "(she/her)", "W3200002": "(she/her)"},
    expected_section="20001",
)

_PITCH = 24.7  # row spacing observed in the real layout


def build_sectioned_pdf(spec: SectionedSpec) -> bytes:
    _fonts()
    width, height = A3
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A3, invariant=1)
    c.setTitle("FAKE roster fixture")
    pos = {"y": height - 46}

    def need(space: float) -> None:  # continue on a new page (no repeated headers) if needed
        if pos["y"] - space < 50:
            c.showPage()
            pos["y"] = height - 46

    def put(x: float, text: str, font: str = "Fx", size: float = 10.5) -> None:
        c.setFont(font, size)
        c.drawString(x, pos["y"], text)

    put(60, "Example Community College", "Fx-Bold", 11)
    pos["y"] -= 46
    header = [("Course:", spec.course_title), ("Professor:", "Staff, Example"), ("Meetings:", spec.meetings[0])]
    header += [("", m) for m in spec.meetings[1:]]
    header += [("Term:", "Fall 2026 | Full Semester | August 24, 2026 - December 18, 2026"),
               ("Drop Date:", "November 13, 2026"), ("Units:", "3.00"), ("Type:", "Grade / Enrollment Roster")]
    header += spec.header_extra
    for label, value in header:
        if label:
            put(60, label, "Fx-Bold", 10)
        put(205, value, "Fx", 10)
        pos["y"] -= 27 if label else 21

    def heading(title: str) -> None:
        need(90)
        pos["y"] -= 26
        put(60, title, "Fx-Bold", 11)
        pos["y"] -= 36

    def table_header() -> None:
        put(75, "Student Name", "Fx-Bold", 10)
        put(470, "Student ID", "Fx-Bold", 10)
        pos["y"] -= 24

    def student_rows(rows: list[FakeRow], extra=None) -> None:
        for n, row in enumerate(rows, start=1):
            need(_PITCH)
            put(75, f"{n}. {row.printed_name('last_first')}", "Fx", 11)
            if row.w_number in spec.pronouns:
                put(245, spec.pronouns[row.w_number], "Fx", 10)
            if row.w_number:
                put(480, printed_id(row.w_number), "Fx", 11)
            if extra:
                put(555, extra(n), "Fx", 10.5)
            else:
                c.setLineWidth(0.5)
                c.line(555, pos["y"] - 3, 780, pos["y"] - 3)
            pos["y"] -= _PITCH

    heading("Current Students")
    table_header()
    student_rows(spec.current)

    heading("Wait List")
    if spec.waitlist:
        table_header()
        student_rows(spec.waitlist)
    else:
        put(300, "No Waitlist Students", "Fx-Bold", 10)
        pos["y"] -= 30

    heading("Drops")
    table_header()
    student_rows(spec.drops, extra=lambda n: f"Oct {n:02d}, 2026 ({'Withdrawn' if n % 2 else 'Dropped'})")

    heading("Permission Numbers")
    for n, (code, name) in enumerate(spec.permission, start=1):
        need(_PITCH)
        put(62, f"{n}. {code} {name}".strip(), "Fx", 11)
        c.setLineWidth(0.5)
        c.line(365, pos["y"] - 3, 780, pos["y"] - 3)
        pos["y"] -= _PITCH

    need(60)
    pos["y"] -= 40
    c.setFont("Fx", 7)
    c.drawCentredString(width / 2, pos["y"], "Class rosters were last updated on Oct 01, 2026 at 07:00:00 AM.")
    c.save()
    return buf.getvalue()


# --- The same SECTIONED roster as CSV and XLSX ---------------------------------
#
# Equivalent logical content to mock_roster_sections.pdf, in two other shapes:
#   * XLSX: the PDF's sectioned layout as worksheet rows (metadata rows, section
#     headings, "N. Last, First M" names, pronoun column, numeric IDs, Drops,
#     Permission Numbers), placed AFTER an unrelated "Notes" sheet.
#   * CSV: a typical flat export: one header row, a Status column
#     (Enrolled / Waitlisted / Dropped / Withdrawn), rows interleaved, quoted
#     "Last, First M" names, Course and Class Nbr columns, a totals row.

CSV_COLUMNS = ("Student ID", "Name", "Pronouns", "Status", "Course", "Class Nbr", "Units")


def sectioned_table_rows(spec: SectionedSpec) -> list[list]:
    """The sectioned roster as spreadsheet rows (IDs as numbers, like Excel stores them)."""
    def sid(w: str):
        printed = printed_id(w)
        return int(printed) if printed.isdigit() else printed

    rows: list[list] = [["Example Community College"], [],
                        ["Course:", spec.course_title], ["Professor:", "Staff, Example"], ["Meetings:", spec.meetings[0]]]
    rows += [["", m] for m in spec.meetings[1:]]
    rows += [["Term:", "Fall 2026"], ["Units:", 3], []]

    def section(title: str, people: list[FakeRow], drop_dates: bool = False) -> None:
        rows.append([title])
        rows.append(["Student Name", "Pronouns", "Student ID"] + (["Drop Date"] if drop_dates else []))
        for n, row in enumerate(people, start=1):
            cells = [f"{n}. {row.last}, {row.first}", spec.pronouns.get(row.w_number, ""), sid(row.w_number)]
            rows.append(cells + ([f"Oct {n:02d}, 2026"] if drop_dates else []))
        rows.append([])

    section("Current Students", spec.current)
    if spec.waitlist:
        section("Wait List", spec.waitlist)
    else:
        rows += [["Wait List"], ["No Waitlist Students"], []]
    section("Drops", spec.drops, drop_dates=True)
    rows += [["Permission Numbers"], ["#", "Permission Number", "Used By"]]
    rows += [[n, int(code), name] for n, (code, name) in enumerate(spec.permission, start=1)]
    return rows


def sectioned_csv_rows(spec: SectionedSpec, columns: tuple[str, ...] = CSV_COLUMNS) -> list[list[str]]:
    """The roster as a flat status-column export, with rows interleaved."""
    others = [(r, "Waitlisted") for r in spec.waitlist]
    others += [(r, "Withdrawn" if k % 2 else "Dropped") for k, r in enumerate(spec.drops)]
    ordered = []
    for i, row in enumerate(spec.current):
        ordered.append((row, "Enrolled"))
        if i % 2 == 1 and others:
            ordered.append(others.pop(0))
    ordered += others
    values = {
        "Student ID": lambda r, s: printed_id(r.w_number), "Name": lambda r, s: f"{r.last}, {r.first}",
        "Last Name": lambda r, s: r.last, "First Name": lambda r, s: r.first,
        "Pronouns": lambda r, s: spec.pronouns.get(r.w_number, ""), "Status": lambda r, s: s,
        "Course": lambda r, s: spec.course_title, "Class Nbr": lambda r, s: spec.expected_section,
        "Units": lambda r, s: "3.00", "Email": lambda r, s: f"{printed_id(r.w_number)}@example.edu",
    }
    rows = [list(columns)] + [[values[c](r, s) for c in columns] for r, s in ordered]
    total = [""] * len(columns)
    total[0] = f"Total Enrolled: {len(spec.current)}"
    return rows + [[""] * len(columns), total]


def to_csv(rows: list[list], delimiter: str = ",", bom: bool = False) -> bytes:
    import csv as _csv
    buf = io.StringIO(newline="")
    _csv.writer(buf, delimiter=delimiter, lineterminator="\r\n").writerows(rows)
    return (b"\xef\xbb\xbf" if bom else b"") + buf.getvalue().encode("utf-8")


def xlsx_entries(data: bytes) -> list[tuple[str, bytes]]:
    """A workbook's logical content: its ZIP entries, in order, uncompressed."""
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return [(name, z.read(name)) for name in z.namelist()]


def build_xlsx(sheets: list[tuple[str, list[list]]], hidden: tuple[str, ...] = ()) -> bytes:
    """A workbook with the given sheets, made byte-for-byte reproducible on every platform."""
    import zipfile
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        if title in hidden:
            ws.sheet_state = "hidden"
    wb.properties.creator = "Roster Tool tests"
    raw = io.BytesIO()
    wb.save(raw)
    # Fixed timestamps (zip entries and document properties) for stable fixtures.
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dcterms:(created|modified)[^>]*>)[^<]*", rb"\g<1>2000-01-01T00:00:00Z", data)
            entry = zipfile.ZipInfo(info.filename, date_time=(2000, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            # ZipInfo takes these from the building machine (create_system is 0 on
            # Windows, 3 elsewhere; external_attr is filled in at write time), which
            # would make the fixture's bytes depend on who built it. Pin both.
            entry.create_system = 3          # Unix, regardless of the build platform
            entry.external_attr = 0o600 << 16
            dst.writestr(entry, data)
    return out.getvalue()


NOTES_SHEET = [["Notes"], ["This sheet is not a roster."], ["Upload the whole workbook; the Roster sheet is found automatically."]]


def build_sectioned_xlsx(spec: SectionedSpec) -> bytes:
    return build_xlsx([("Notes", NOTES_SHEET), ("Roster", sectioned_table_rows(spec))])


def build_sectioned_csv(spec: SectionedSpec) -> bytes:
    return to_csv(sectioned_csv_rows(spec))


def build_image_only_pdf() -> bytes:
    """A PDF with graphics but no text layer (like a scan)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    for i in range(30):
        c.rect(50, 700 - i * 20, 400, 12, fill=1, stroke=0)
    c.save()
    return buf.getvalue()


def write_fixtures(directory: Path = FIXTURE_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in ALL_FIXTURES:
        path = directory / spec.filename
        path.write_bytes(build_pdf(spec))
        paths.append(path)
    for name, data in ((SECTIONED.filename, build_sectioned_pdf(SECTIONED)),
                       (CSV_FIXTURE, build_sectioned_csv(SECTIONED)),
                       (XLSX_FIXTURE, build_sectioned_xlsx(SECTIONED))):
        path = directory / name
        path.write_bytes(data)
        paths.append(path)
    return paths


CSV_FIXTURE = "mock_roster_sections.csv"
XLSX_FIXTURE = "mock_roster_sections.xlsx"
