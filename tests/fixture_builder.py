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
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import letter
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
    return paths
