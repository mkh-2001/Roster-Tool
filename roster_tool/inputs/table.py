"""Rows of cells (from a CSV file or an XLSX worksheet) -> roster.

Spreadsheet rosters are interpreted with the SAME rules as PDF rosters: this
module reuses the PDF parser's row handling (`TableRosterParser`) for
populations, the W-number rule, row-number prefixes, pronouns, name splitting,
count checks and metadata (labels, "Meetings:" class numbers). Only the
spreadsheet-specific part lives here:

* A header row is found by recognizing column names (`TableRules.headers`):
  a student-ID column plus "First Name" + "Last Name" or a single "Name"
  column are required. Unknown extra columns are ignored. Two columns for the
  same concept are an error, never a guess.
* Populations come from a "Status" column (Enrolled / Waitlisted / Dropped ...)
  and/or section-heading rows ("Current Students", "Wait List", "Drops",
  "Permission Numbers"), exactly like the PDF. Unknown or contradictory status
  values are an error.
* "Label: value" rows before the header (or label rows after it, such as
  "Total Enrolled: 17") supply course, section and counts; otherwise "Course"
  and "Section"/"Class Nbr" columns are used and must hold a single value.

The file's name is never available here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..errors import RosterStructureError
from ..models import CURRENT, WAITLIST, Roster
from ..pdf_extractor import ExtractedDocument, ExtractedPage, TextCell, TextLine
from ..roster_parser import EXCLUDED, ParseResult, ParseRules, TableRosterParser, _Row

NOTE_NO_ID = "No Student ID in this row."

CONCEPT_LABELS = {
    "id": "student ID", "first": "first name", "last": "last name", "middle": "middle name",
    "name": "name", "status": "status", "course": "course", "section": "section/class number",
    "pronouns": "pronouns", "number": "row number",
}


class MissingHeaderError(RosterStructureError):
    user_message = (
        "Couldn't find the roster's header row. The file needs a header row with a student ID column "
        "(for example 'Student ID') and either 'First Name' + 'Last Name' columns or a 'Name' column."
    )


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass
class TableRules:
    # Matched against the whole, normalized header text (see _norm_header).
    headers: dict[str, re.Pattern] = field(default_factory=lambda: {
        "id": _rx(r"(?:student\s*)?id(?:\s*(?:number|no|#))?|w\s*(?:number|no|#)|wnumber|emplid|student\s*number"),
        "first": _rx(r"first(?:\s*name)?|given\s*name"),
        "last": _rx(r"last(?:\s*name)?|surname|family\s*name"),
        "middle": _rx(r"middle(?:\s*(?:name|initial))?|mi"),
        "name": _rx(r"(?:student\s*)?(?:full\s*)?name|name\s*\(\s*last\s*,\s*first\s*\)"),
        "status": _rx(r"(?:enrollment|enrolment|enrl|enroll)?\s*status|population|roster\s*status"),
        "course": _rx(r"course(?:\s*(?:title|name))?|class\s*title"),
        "section": _rx(r"section(?:\s*(?:number|no|#))?|class\s*(?:nbr|number|no|#)"),
        "pronouns": _rx(r"(?:preferred\s*)?pronouns?"),
        "number": _rx(r"#|no|seq|row"),
    })
    # Status cell values (case-insensitive) -> population.
    status_values: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        CURRENT: ("enrolled", "current", "current student", "current students", "active", "registered"),
        WAITLIST: ("waitlisted", "wait listed", "wait-listed", "wait list", "waitlist", "waiting",
                   "on wait list", "on waitlist"),
        EXCLUDED: ("dropped", "drop", "drops", "withdrawn", "withdrew", "withdraw", "cancelled", "canceled",
                   "permission", "permission number"),
    })
    # The (first) header row must appear within this many rows.
    header_scan_rows: int = 30
    # Shared with the PDF parser: section headings, labels, W-number rule, prefixes, pronouns.
    parse_rules: ParseRules = field(default_factory=ParseRules)


def _norm_header(text: str) -> str:
    t = text.replace("_", " ").strip().rstrip(":*.").strip().lower()
    return re.sub(r"\s+", " ", t)


class TableInterpreter:
    def __init__(self, rules: TableRules | None = None):
        self.rules = rules or TableRules()
        self.parser = TableRosterParser(self.rules.parse_rules)
        pr = self.rules.parse_rules
        self._labels = {lbl for group in (pr.course_labels, pr.title_labels, pr.section_labels, pr.count_labels,
                                          pr.meetings_labels, pr.other_labels) for lbl in group}
        self._status = {v: pop for pop, values in self.rules.status_values.items() for v in values}

    # -- structure ------------------------------------------------------------

    def header_map(self, cells: list[str]) -> dict[str, int] | None:
        """{concept: column} if this row is a roster header row, else None."""
        found: dict[str, list[int]] = {}
        for i, cell in enumerate(cells):
            text = _norm_header(cell)
            if not text:
                continue
            for concept, pattern in self.rules.headers.items():
                if pattern.fullmatch(text):
                    found.setdefault(concept, []).append(i)
                    break
        if "id" not in found or not ("name" in found or {"first", "last"} <= found.keys()):
            return None
        duplicated = [c for c, cols in found.items() if len(cols) > 1]
        if duplicated:
            names = "; ".join(f"{CONCEPT_LABELS[c]}: " + ", ".join(repr(cells[i]) for i in found[c]) for c in duplicated)
            raise RosterStructureError(
                f"The header row has more than one column for the same information ({names}). "
                "Remove or rename the extra column, then upload again."
            )
        return {concept: cols[0] for concept, cols in found.items()}

    def has_header(self, rows: list[list[str]]) -> bool:
        for cells in rows[: self.rules.header_scan_rows]:
            try:
                if self.header_map(cells) is not None:
                    return True
            except RosterStructureError:
                return True  # header-like but ambiguous: still a roster candidate
        return False

    def _line(self, cells: list[str], n: int, bare_labels: bool) -> TextLine:
        """A row as a TextLine, so the PDF parser's metadata/heading logic applies unchanged."""
        out: list[TextCell] = []
        for i, text in enumerate(cells):
            if not text:
                continue
            if not out and bare_labels and not text.endswith(":") and _norm_header(text) in self._labels:
                text += ":"  # "Course" | "ANTH 310 ..." in a spreadsheet == "Course: ANTH 310 ..."
            out.append(TextCell(text, i * 100.0, i * 100.0 + 90.0))
        return TextLine(page=1, y=-float(n), font_size=10.0, cells=out)

    def _heading(self, cells: list[str]) -> str | None:
        filled = [c for c in cells if c]
        if len(filled) != 1:
            return None
        return self.parser._heading(self._line(filled, 0, False))

    def _is_label_row(self, cells: list[str], columns: dict[str, int]) -> bool:
        """'Total Enrolled: 17'-style rows after the header (a colon is required there)."""
        first = next((c for c in cells if c), "")
        id_cell = _get(cells, columns["id"])
        return bool(self.parser._label_re.match(first)) and not self._id_like(id_cell)

    def _id_like(self, text: str) -> bool:
        return bool(text) and bool(self.rules.parse_rules.id_candidate.fullmatch(text))

    # -- interpretation -------------------------------------------------------

    def interpret(self, rows: list[list[str]], source: str) -> ParseResult:
        rows = [[(c or "").strip() for c in row] for row in rows]
        header_at = next((i for i, cells in enumerate(rows[: self.rules.header_scan_rows])
                          if self.header_map(cells) is not None), None)
        if header_at is None:
            raise MissingHeaderError()

        p = self.parser
        sectioned = any(self._heading(cells) is not None for cells in rows)
        population: str | None = None if sectioned else CURRENT
        columns: dict[str, int] | None = None
        meta_lines: list[TextLine] = []
        students: list[tuple[_Row, list[str]]] = []
        unknown_status: list[tuple[int, str]] = []
        contradictions: list[int] = []
        excluded = skipped = 0

        for n, cells in enumerate(rows, start=1):  # n = the row number shown by Excel / line in the CSV
            if not any(cells):
                continue
            heading = self._heading(cells)
            if heading is not None:
                population = heading
                continue
            header = self.header_map(cells)
            if header is not None:
                columns = header
                continue
            if columns is None:  # above the first header: title / "Label: value" rows
                meta_lines.append(self._line(cells, n, bare_labels=True))
                continue
            if self._is_label_row(cells, columns):
                meta_lines.append(self._line(cells, n, bare_labels=False))
                continue
            if all(p.rules.ignore_cell.match(c) for c in cells if c):  # "No Waitlist Students", page furniture
                continue
            if sectioned and population not in (CURRENT, WAITLIST):
                excluded += 1  # Drops, Permission Numbers, or rows before the first section
                continue
            row_population = population
            if "status" in columns:
                value = _get(cells, columns["status"])
                mapped = self._status.get(re.sub(r"\s+", " ", value.lower()))
                if mapped is None:
                    unknown_status.append((n, value))
                    continue
                if mapped == EXCLUDED:
                    excluded += 1
                    continue
                if sectioned and mapped != population:
                    contradictions.append(n)
                    continue
                row_population = mapped
            row = self._student_row(cells, columns)
            if row is None:
                skipped += 1
                continue
            p._finish_row(row, row_population)
            students.append((row, cells))

        if unknown_status:
            values = sorted({v[:30] for _, v in unknown_status})
            raise RosterStructureError(
                f"Unrecognized Status value(s) {', '.join(repr(v) for v in values)} in row(s) "
                f"{_row_list([n for n, _ in unknown_status])}. Recognized values include Enrolled, "
                "Waitlisted (or Waiting) and Dropped (or Withdrawn)."
            )
        if contradictions:
            raise RosterStructureError(
                f"Row(s) {_row_list(contradictions)} have a Status that contradicts the section heading above them."
            )

        rows_only = [row for row, _ in students]
        if columns and "status" in columns and not sectioned:
            # Row numbers in a status-column export count every row (including
            # drops), so they cannot serve as a per-population count check.
            for row in rows_only:
                row.row_number = None

        roster = Roster()
        diagnostics: list[str] = []
        p._read_metadata(ExtractedDocument([ExtractedPage(1, 0.0, 0.0, meta_lines)]), roster, diagnostics)
        for concept, attr in (("course", "course_title"), ("section", "section")):
            if not getattr(roster, attr) and columns and concept in columns:
                values = sorted({_get(cells, columns[concept]) for _, cells in students} - {""})
                if len(values) > 1:
                    raise RosterStructureError(
                        f"This file has students from more than one {CONCEPT_LABELS[concept]} "
                        f"({len(values)} different values in that column). Upload one class section at a time."
                    )
                if values:
                    setattr(roster, attr, values[0])
                    diagnostics.append(f"{attr}: from the '{concept}' column")
        p._post_process(rows_only, roster, diagnostics)
        roster.students = [p._to_student(row) for row in rows_only]
        n_wait = sum(1 for row in rows_only if row.population == WAITLIST)
        diagnostics.insert(0, f"{source}: header row {header_at + 1}; {len(rows_only) - n_wait} current + "
                              f"{n_wait} wait-list rows; {excluded} excluded; {skipped} skipped (no ID or name)")
        return ParseResult(roster=roster, diagnostics=diagnostics)

    def _student_row(self, cells: list[str], columns: dict[str, int]) -> _Row | None:
        id_text = _get(cells, columns["id"])
        if "first" in columns and "last" in columns:
            first = _get(cells, columns["first"])
            middle = _get(cells, columns["middle"]) if "middle" in columns else ""
            if middle:
                first = f"{first} {middle}".strip()  # same shape as the PDF's "First M"
            last = _get(cells, columns["last"])
            has_name = bool(first or last)
            row = _Row(page=None, w_number=id_text, first=first, last=last)
        else:
            name = _get(cells, columns["name"])
            has_name = bool(name)
            row = _Row(page=None, w_number=id_text, name_text=name)
        if not has_name and not self._id_like(id_text):
            return None  # not a student row (stray note, totals without a label, ...)
        if not id_text:
            row.note = NOTE_NO_ID
        if "number" in columns:
            number = _get(cells, columns["number"]).rstrip(".")
            if number.isdigit() and 0 < int(number) < 10000:
                row.row_number = int(number)
        return row


def _get(cells: list[str], index: int) -> str:
    return cells[index] if index < len(cells) else ""


def _row_list(numbers: list[int], limit: int = 10) -> str:
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" and {len(numbers) - limit} more" if len(numbers) > limit else "")


TABLE = TableInterpreter()
