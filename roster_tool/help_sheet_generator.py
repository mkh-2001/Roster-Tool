"""Printable help sheet (US Letter PDF) from a normalized roster.

Layout
------
* Header: course title, section, tutor name (and "Page x of y" when > 1 page).
* One column per selected meeting day, side by side, each a complete student
  list:   [ ] W1234567  Last, First
* At most 3 day-columns per page. 4+ days are split into balanced groups
  (4 -> 2+2, 5 -> 3+2, ...), each group on its own page(s).
* A single day whose list does not fit one column continues in a second
  column on the same page ("Tuesday (cont.)") before using another sheet.
* Row height shrinks slightly (never below MIN_PITCH) only when that avoids an
  extra page; otherwise rows stay at a comfortable height and the sheet
  paginates. Text is never shrunk to cram students in.
* Long names shrink a little, then wrap. Names are never truncated, and no
  student is ever dropped.
* When the roster has a Wait List, every day column lists "Current Students"
  and then a separately labelled "Wait List" group. Without waitlisted
  students no label or space is added. A group that continues in another
  column/page is labelled "(cont.)".

Print and "Download PDF" both use this same PDF, so they always match.
"""

from __future__ import annotations

import io
import math
import threading
from dataclasses import dataclass, field

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import resources
from .models import Roster, Student, clean_text
from .validation import OutputBlockedError, blocking_errors, validate_roster

CURRENT_LABEL = "Current Students"
WAITLIST_LABEL = "Wait List"
LABEL_UNITS = 1.4  # a group label takes this many row heights

DAY_ORDER = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

PAGE_W, PAGE_H = letter
MARGIN = 0.4 * inch          # printer-safe on virtually all printers
GUTTER = 0.22 * inch
MAX_COLS_PER_PAGE = 3

HEADER_H = 40.0              # course / section / tutor block
COLUMN_HEAD_H = 26.0         # day label + date line
MAX_PITCH = 15.0             # comfortable row height (pt)
MIN_PITCH = 12.5             # still easy to tick; used only to save a page
BOX = 8.5                    # checkbox side (pt) ~ 3 mm
ID_SIZE = 8.5
NAME_SIZES = (9.0, 8.5, 8.0, 7.5)  # tried in order before wrapping
WRAP_EXTRA_UNITS = 0.75      # each extra wrapped line costs this many rows
MAX_TUTOR_LEN = 100

FONT_DIR = resources.FONT_DIR
FONT = "RosterSans"
FONT_BOLD = "RosterSans-Bold"
_font_lock = threading.Lock()
_fonts_loaded = False


class HelpSheetInputError(ValueError):
    """Bad tutor/day/course input. The message is safe to show to the employee."""


def ensure_fonts() -> None:
    """Register the bundled fonts once (also called at startup to pre-warm)."""
    global _fonts_loaded
    with _font_lock:
        if not _fonts_loaded:
            pdfmetrics.registerFont(TTFont(FONT, str(FONT_DIR / "DejaVuSansCondensed.ttf")))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "DejaVuSansCondensed-Bold.ttf")))
            _fonts_loaded = True


def _width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, font, size)


# --- Inputs ------------------------------------------------------------------

def normalize_days(days) -> list[str]:
    """Accepts full names or 3-letter abbreviations; returns week order, no repeats."""
    if not isinstance(days, (list, tuple)):
        raise HelpSheetInputError("Select at least one meeting day.")
    lookup = {d.lower(): d for d in DAY_ORDER} | {d[:3].lower(): d for d in DAY_ORDER}
    chosen = set()
    for value in days:
        key = str(value).strip().lower()
        if key not in lookup:
            raise HelpSheetInputError(f"Unknown meeting day: {str(value)[:20]!r}")
        chosen.add(lookup[key])
    if not chosen:
        raise HelpSheetInputError("Select at least one meeting day.")
    return [d for d in DAY_ORDER if d in chosen]


def day_groups(days: list[str]) -> list[list[str]]:
    """Split days into balanced groups of at most MAX_COLS_PER_PAGE."""
    n_groups = math.ceil(len(days) / MAX_COLS_PER_PAGE)
    base, extra = divmod(len(days), n_groups)
    groups, i = [], 0
    for g in range(n_groups):
        size = base + (1 if g < extra else 0)
        groups.append(days[i:i + size])
        i += size
    return groups


# --- Layout planning ---------------------------------------------------------

@dataclass
class RowFit:
    name_size: float
    lines: list[str]

    @property
    def units(self) -> float:
        return 1 + WRAP_EXTRA_UNITS * (len(self.lines) - 1)


@dataclass
class LayoutItem:
    """One entry of a day column: a student row, or a group label (student is None)."""

    student: Student | None
    fit: RowFit | None = None
    label: str = ""

    @property
    def units(self) -> float:
        return self.fit.units if self.student is not None else LABEL_UNITS


@dataclass
class ColumnPlan:
    day: str
    continued: bool
    start: int  # item slice [start, end)
    end: int
    show_date: bool = True  # False for a same-day continuation on the same page
    group_label: str | None = None  # e.g. "Wait List (cont.)" when a group continues here


@dataclass
class PagePlan:
    columns: list[ColumnPlan]
    col_width: float
    pitch: float
    items: list[LayoutItem] = field(repr=False)

    @property
    def fits(self) -> list[RowFit | None]:
        return [item.fit for item in self.items]


def _col_width(n_cols: int) -> float:
    return (PAGE_W - 2 * MARGIN - (n_cols - 1) * GUTTER) / n_cols


def _id_width(roster: Roster) -> float:
    sample = max((s.w_number for s in roster.students), key=lambda w: _width(w, FONT, ID_SIZE), default="W0000000")
    return max(_width(sample, FONT, ID_SIZE), _width("W0000000", FONT, ID_SIZE))


def _wrap(text: str, width: float, size: float) -> list[str]:
    lines, current = [], ""
    for word in text.split(" "):
        trial = f"{current} {word}".strip()
        if current and _width(trial, FONT, size) > width:
            lines.append(current)
            current = word
        else:
            current = trial
        while _width(current, FONT, size) > width and len(current) > 1:  # one very long word
            cut = len(current) - 1
            while cut > 1 and _width(current[:cut], FONT, size) > width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines or [""]


def fit_name(text: str, width: float) -> RowFit:
    for size in NAME_SIZES:
        if _width(text, FONT, size) <= width:
            return RowFit(size, [text])
    size = NAME_SIZES[-1]
    return RowFit(size, _wrap(text, width, size))


def _name_width(col_width: float, id_width: float) -> float:
    return col_width - BOX - 4 - id_width - 6


def _column_items(roster: Roster, name_width: float) -> list[LayoutItem]:
    """The entries every day column shows: Current Students, then a labelled Wait List."""
    def rows(students):
        return [LayoutItem(s, fit_name(s.display_name, name_width)) for s in students]

    waitlist = roster.waitlist_students
    if not waitlist:
        return rows(roster.current_students)
    return ([LayoutItem(None, label=CURRENT_LABEL)] + rows(roster.current_students)
            + [LayoutItem(None, label=WAITLIST_LABEL)] + rows(waitlist))


def _cont_label(items: list[LayoutItem], start: int) -> str | None:
    """'<group> (cont.)' if a column starting at `start` begins inside a labelled group."""
    for i in range(start, -1, -1):
        if items[i].student is None:
            return None if i == start else f"{items[i].label} (cont.)"
    return None


def _pack(items: list[LayoutItem], capacity: float) -> list[tuple[int, int]]:
    """Greedy: consecutive slices whose units fit within capacity (>= 1 item each).

    A group label is never left as the last entry of a slice, and a slice that
    starts inside a labelled group reserves room for its "(cont.)" label.
    """
    slices, start, used = [], 0, 0.0
    for i, item in enumerate(items):
        need = item.units
        if item.student is None and i + 1 < len(items):
            need += items[i + 1].units  # keep the label with its first row
        if i > start and used + need > capacity + 1e-9:
            slices.append((start, i))
            start, used = i, (LABEL_UNITS if _cont_label(items, i) else 0.0)
        used += item.units
    slices.append((start, len(items)))
    return slices


def _best_pitch(items: list[LayoutItem], avail: float, slots_per_page: int) -> tuple[float, list[tuple[int, int]]]:
    """Fewest pages at MIN_PITCH, then the most comfortable pitch for that page count."""
    def pages(pitch: float) -> tuple[int, list[tuple[int, int]]]:
        slices = _pack(items, avail / pitch)
        return math.ceil(len(slices) / slots_per_page), slices

    target, _ = pages(MIN_PITCH)
    pitch = MAX_PITCH
    while pitch > MIN_PITCH:
        n, slices = pages(pitch)
        if n <= target:
            return pitch, slices
        pitch = round(pitch - 0.25, 2)
    return MIN_PITCH, pages(MIN_PITCH)[1]


def plan_layout(roster: Roster, days: list[str]) -> list[PagePlan]:
    ensure_fonts()
    days = normalize_days(days)
    id_w = _id_width(roster)
    avail = PAGE_H - 2 * MARGIN - HEADER_H - COLUMN_HEAD_H
    plans: list[PagePlan] = []

    for group in day_groups(days):
        if len(group) == 1:
            day = group[0]
            # One full-width column if everyone fits on one page...
            width = _col_width(1)
            items = _column_items(roster, _name_width(width, id_w))
            if sum(item.units for item in items) * MIN_PITCH <= avail:
                pitch, _ = _best_pitch(items, avail, 1)
                plans.append(PagePlan([ColumnPlan(day, False, 0, len(items))], width, pitch, items))
                continue
            # ...otherwise the list snakes into a second column on the same page.
            width = _col_width(2)
            items = _column_items(roster, _name_width(width, id_w))
            pitch, slices = _best_pitch(items, avail, 2)
            for p in range(0, len(slices), 2):
                cols = [ColumnPlan(day, k > 0, a, b, show_date=(k == p), group_label=_cont_label(items, a))
                        for k, (a, b) in enumerate(slices[p:p + 2], start=p)]
                plans.append(PagePlan(cols, width, pitch, items))
        else:
            width = _col_width(len(group))
            items = _column_items(roster, _name_width(width, id_w))
            pitch, slices = _best_pitch(items, avail, 1)
            for k, (a, b) in enumerate(slices):
                cols = [ColumnPlan(d, k > 0, a, b, group_label=_cont_label(items, a)) for d in group]
                plans.append(PagePlan(cols, width, pitch, items))
    return plans


# --- Rendering ---------------------------------------------------------------

@dataclass
class HelpSheetResult:
    pdf: bytes
    page_count: int
    warnings: list[str]
    plans: list[PagePlan] = field(repr=False)


def generate_help_sheet(roster: Roster, tutor_name: str, days) -> HelpSheetResult:
    ensure_fonts()
    tutor = clean_text(tutor_name)
    if not tutor:
        raise HelpSheetInputError("Enter the tutor's name.")
    if len(tutor) > MAX_TUTOR_LEN:
        raise HelpSheetInputError("Tutor name is too long.")
    days = normalize_days(days)
    if not roster.course_title:
        raise HelpSheetInputError("Enter the course title.")
    if not roster.section:
        raise HelpSheetInputError("Enter the section.")
    errors = blocking_errors(validate_roster(roster))
    if errors:
        raise OutputBlockedError(errors)

    plans = plan_layout(roster, days)
    warnings = _glyph_warnings([roster.course_title, roster.section, tutor] + [s.display_name for s in roster.students])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1, pageCompression=1)
    c.setTitle(f"Help Sheet - {roster.course_title} - {roster.section}")
    c.setCreator("Roster Tool")
    c.setAuthor("")
    id_w = _id_width(roster)
    for number, plan in enumerate(plans, start=1):
        _draw_header(c, roster, tutor, number, len(plans))
        for k, col in enumerate(plan.columns):
            x = MARGIN + k * (plan.col_width + GUTTER)
            _draw_column(c, roster, plan, col, x, id_w)
        c.showPage()
    c.save()
    return HelpSheetResult(buf.getvalue(), len(plans), warnings, plans)


def _glyph_warnings(texts: list[str]) -> list[str]:
    face = pdfmetrics.getFont(FONT).face
    missing = sorted({ch for t in texts for ch in t if not ch.isspace() and ord(ch) not in face.charToGlyph})
    if missing:
        return [f"Some characters cannot be printed with the built-in font and may appear blank: {' '.join(missing[:20])}"]
    return []


def _fit_size(text: str, font: str, size: float, min_size: float, width: float) -> float:
    while size > min_size and _width(text, font, size) > width:
        size -= 0.5
    return size


def _draw_header(c: canvas.Canvas, roster: Roster, tutor: str, page: int, pages: int) -> None:
    top = PAGE_H - MARGIN
    usable = PAGE_W - 2 * MARGIN
    page_label = f"Page {page} of {pages}" if pages > 1 else ""
    label_w = _width(page_label, FONT, 8) + 12 if page_label else 0

    title_size = _fit_size(roster.course_title, FONT_BOLD, 13, 9, usable - label_w)
    c.setFillGray(0)
    c.setFont(FONT_BOLD, title_size)
    c.drawString(MARGIN, top - 12, roster.course_title)
    if page_label:
        c.setFont(FONT, 8)
        c.setFillGray(0.35)
        c.drawRightString(PAGE_W - MARGIN, top - 11, page_label)
        c.setFillGray(0)

    y = top - 27
    x = MARGIN
    for label, value in (("Section: ", roster.section), ("Tutor: ", tutor)):
        c.setFont(FONT_BOLD, 10)
        c.drawString(x, y, label)
        x += _width(label, FONT_BOLD, 10)
        c.setFont(FONT, 10)
        c.drawString(x, y, value)
        x += _width(value, FONT, 10) + 24

    c.setLineWidth(0.8)
    c.line(MARGIN, top - HEADER_H + 6, PAGE_W - MARGIN, top - HEADER_H + 6)


def _draw_column(c: canvas.Canvas, roster: Roster, plan: PagePlan, col: ColumnPlan, x: float, id_w: float) -> None:
    w = plan.col_width
    top = PAGE_H - MARGIN - HEADER_H

    # Day label and date line
    label = f"{col.day} (cont.)" if col.continued else col.day
    base = top - 13
    c.setFont(FONT_BOLD, 10.5)
    c.drawString(x, base, label)
    c.setLineWidth(0.6)
    if col.show_date:
        date_x = x + _width(label, FONT_BOLD, 10.5) + 12
        c.setFont(FONT, 9)
        c.drawString(date_x, base, "Date:")
        line_x0 = date_x + _width("Date:", FONT, 9) + 4
        c.line(line_x0, base - 1.5, x + w, base - 1.5)
    c.line(x, top - COLUMN_HEAD_H + 4, x + w, top - COLUMN_HEAD_H + 4)

    # Rows
    y = top - COLUMN_HEAD_H
    name_x = x + BOX + 4 + id_w + 6
    if col.group_label:
        y = _draw_group_label(c, col.group_label, x, y, w, plan.pitch)
    for i in range(col.start, col.end):
        item = plan.items[i]
        if item.student is None:
            y = _draw_group_label(c, item.label, x, y, w, plan.pitch)
            continue
        fit = item.fit
        row_h = plan.pitch * fit.units
        baseline = y - plan.pitch / 2 - 3
        s = item.student

        c.setLineWidth(0.8)
        c.setStrokeGray(0)
        c.rect(x, baseline - 1.5, BOX, BOX, stroke=1, fill=0)
        c.setFont(FONT, ID_SIZE)
        c.drawString(x + BOX + 4, baseline, s.w_number)
        c.setFont(FONT, fit.name_size)
        for n, text in enumerate(fit.lines):
            c.drawString(name_x, baseline - n * fit.name_size * 1.12, text)

        y -= row_h
        c.setLineWidth(0.3)
        c.setStrokeGray(0.75)
        c.line(x, y + 0.5, x + w, y + 0.5)
        c.setStrokeGray(0)


def _draw_group_label(c: canvas.Canvas, text: str, x: float, y: float, w: float, pitch: float) -> float:
    """Bold group label ("Current Students", "Wait List") with a rule; returns the new y."""
    height = LABEL_UNITS * pitch
    baseline = y - height + 5
    c.setFillGray(0)
    c.setFont(FONT_BOLD, 9.5)
    c.drawString(x, baseline, text)
    c.setLineWidth(0.6)
    c.setStrokeGray(0)
    c.line(x, baseline - 3, x + w, baseline - 3)
    return y - height
