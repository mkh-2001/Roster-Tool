"""Roster interpretation: ExtractedDocument -> normalized Roster.

THIS is the module to adapt when a real roster PDF becomes available
(see REAL_ROSTER_INTEGRATION.md). Everything specific to a roster's layout
lives in `ParseRules`; nothing downstream of this module knows how the PDF
looked.

How a document is read
----------------------
1. Metadata lines ("Course: ...", "Section: ...", "Total Enrolled: 32") are
   recognised by their labels and supply course title, section and the
   expected student count.
2. A table header line (a cell like "ID"/"Student ID" plus a cell like
   "Name"/"Last Name") maps x-positions to columns, if present.
3. Every line containing something that looks like a student ID becomes a
   student row. The ID is kept exactly as printed (malformed IDs are flagged
   later by validation, never "fixed").
4. Lines inside the student table that have NO ID are kept as rows with an
   empty W number and a review note, so a missing ID or wrapped text can never
   silently disappear.
5. Row numbers printed in the PDF (1, 2, 3, ...) provide an extra count check.

IMPORTANT: these rules were developed against our own FAKE fixtures only.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Protocol

from .models import W_NUMBER_RE, Roster, Student, normalize_roster
from .names import NOTE_NO_COMMA, split_name
from .pdf_extractor import ExtractedDocument, TextCell, TextLine

NOTE_NO_ID = (
    "No W number on this line. It may be a student whose W number is missing, "
    "or text that wrapped from the row above."
)
NOTE_MULTI_ID = "This line contains more than one ID; check this row."


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


@dataclass
class ParseRules:
    # Strict, valid W number (shared with validation).
    w_number: re.Pattern = W_NUMBER_RE
    # Anything that LOOKS like an attempt at a student ID, so malformed IDs
    # ("W123456", "w1234567", "W 1234567", "1234567") are surfaced, not skipped.
    # Excludes email addresses (w1234567@...).
    id_candidate: re.Pattern = re.compile(
        r"(?<![A-Za-z0-9@._])(?:[Ww][ \-#:]?\d{5,9}|\d{7})(?![A-Za-z0-9@])"
    )
    # "auto": "Last, First" when a comma is present, otherwise "First Last".
    name_order: str = "auto"

    # Labels are matched as "<label>:" (case-insensitive).
    course_labels: tuple[str, ...] = ("course", "course title", "course name")
    title_labels: tuple[str, ...] = ("title", "description")
    section_labels: tuple[str, ...] = (
        "section", "section number", "class nbr", "class number", "class no", "class #",
    )
    count_labels: tuple[str, ...] = (
        "total enrolled", "enrolled", "enrollment", "total enrollment", "total students",
        "students enrolled", "number of students",
    )
    # Other known labels: only used to end the value of the label before them,
    # and to recognise metadata lines.
    other_labels: tuple[str, ...] = (
        "term", "session", "instructor", "units", "room", "days", "time", "meets",
        "location", "dates", "campus", "printed", "career", "component", "mode",
        "status", "capacity", "waitlist", "waitlisted", "college", "subject",
    )
    # Fallback: a line beginning with SUBJECT NUMBER, e.g. "PHIL 320 Logic ...".
    course_line: re.Pattern = re.compile(r"^[A-Z]{2,5}\s?\d{1,4}[A-Z]{0,2}\b")
    # Fallback for a section number given without a colon.
    section_fallback: re.Pattern = _rx(
        r"\b(?:section|class\s*(?:nbr|number|no\.?|#))\s*[:#]?\s*(\d{3,6})\b"
    )
    # A course value that is only "SUBJ 123" (the title would be elsewhere).
    course_code_only: re.Pattern = re.compile(r"^[A-Z]{2,5}\s?\d{1,4}[A-Z]{0,2}$")

    # Whole cells that are page furniture, never student data.
    ignore_cell: re.Pattern = _rx(
        r"^(?:page\s+\d+(?:\s+of\s+\d+)?|\d+\s+of\s+\d+|printed\b.*|run\s+date\b.*|class\s+roster.*)$"
    )

    # Table-header cells.
    header_id: re.Pattern = _rx(r"^(?:student\s*)?id(?:\s*(?:number|no\.?|#))?$|^w\s*(?:number|no\.?|#)$|^emplid$")
    header_name: re.Pattern = _rx(r"^(?:student\s*)?(?:full\s*)?name$")
    header_last: re.Pattern = _rx(r"^(?:last(?:\s*name)?|surname|family\s*name)$")
    header_first: re.Pattern = _rx(r"^(?:first(?:\s*name)?|given\s*name)$")
    header_number: re.Pattern = _rx(r"^(?:#|no\.?|seq\.?)$")

    # A line belongs to the student table if it is within this many row
    # pitches of the neighbouring table line.
    table_gap_pitches: float = 1.6
    # Fraction of rows that must use "Last, First" before a comma-less name
    # is flagged as inconsistent.
    comma_majority: float = 0.6


@dataclass
class ParseResult:
    roster: Roster
    # Structural notes for developers (no student data), e.g. "page 1: header found".
    diagnostics: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


class RosterParser(Protocol):
    name: str

    def parse(self, doc: ExtractedDocument) -> ParseResult: ...


# --- Internal row representation -------------------------------------------

@dataclass
class _Segment:
    text: str
    x0: float
    x1: float
    is_id: bool = False


@dataclass
class _Row:
    page: int
    w_number: str
    name_text: str = ""
    first: str | None = None  # set when the PDF has separate first/last columns
    last: str | None = None
    row_number: int | None = None
    note: str | None = None


class TableRosterParser:
    """Generic parser for text-based rosters (tables or simple lists)."""

    name = "generic-table"

    def __init__(self, rules: ParseRules | None = None):
        self.rules = rules or ParseRules()
        r = self.rules
        all_labels = r.course_labels + r.title_labels + r.section_labels + r.count_labels + r.other_labels
        alternation = "|".join(
            r"\s*".join(re.escape(word) for word in lbl.split(" "))
            for lbl in sorted(all_labels, key=len, reverse=True)
        )
        self._label_re = re.compile(rf"(?<![A-Za-z])(?P<label>{alternation})\s*:", re.IGNORECASE)

    # -- public -------------------------------------------------------------

    def parse(self, doc: ExtractedDocument) -> ParseResult:
        diagnostics: list[str] = []
        roster = Roster()
        self._read_metadata(doc, roster, diagnostics)

        rows: list[_Row] = []
        columns: list[tuple[str, float, float]] | None = None
        for page in doc.pages:
            header_idx, page_columns = self._find_header(page.lines)
            if page_columns:
                columns = page_columns
                diagnostics.append(f"page {page.number}: table header -> {[c[0] for c in columns]}")
            elif columns:
                diagnostics.append(f"page {page.number}: no header; reusing previous page's columns")
            rows.extend(self._read_page_rows(page.lines, header_idx, columns, page.number, diagnostics))

        self._post_process(rows, roster, diagnostics)
        roster.students = [self._to_student(row) for row in rows]
        return ParseResult(roster=roster, diagnostics=diagnostics)

    # -- metadata -----------------------------------------------------------

    def _labelled_values(self, line: TextLine) -> list[tuple[str, str]]:
        """[(label, value)] for 'Label: value' pairs in a line, cell by cell."""
        found = []
        cells = line.cells
        for ci, cell in enumerate(cells):
            matches = list(self._label_re.finditer(cell.text))
            for mi, m in enumerate(matches):
                end = matches[mi + 1].start() if mi + 1 < len(matches) else len(cell.text)
                value = cell.text[m.end():end].strip()
                if not value and mi + 1 == len(matches) and ci + 1 < len(cells):
                    nxt = cells[ci + 1].text
                    if not self._label_re.search(nxt):
                        value = nxt.strip()
                label = re.sub(r"\s+", " ", m.group("label").lower())
                found.append((label, value))
        return found

    def _read_metadata(self, doc: ExtractedDocument, roster: Roster, diagnostics: list[str]) -> None:
        r = self.rules
        courses: list[str] = []
        titles: list[str] = []
        sections: list[str] = []
        counts: list[int] = []
        for line in doc.lines:
            for label, value in self._labelled_values(line):
                if not value:
                    continue
                if label in r.course_labels:
                    courses.append(value)
                elif label in r.title_labels:
                    titles.append(value)
                elif label in r.section_labels:
                    sections.append(value.split(" ")[0].strip(".,;"))
                elif label in r.count_labels:
                    m = re.match(r"(\d{1,4})\b", value)
                    if m:
                        counts.append(int(m.group(1)))

        # Course title
        if courses:
            course = courses[0]
            if r.course_code_only.match(course) and titles:
                course = f"{course} {titles[0]}"
            roster.course_title = course
            if len(set(courses)) > 1:
                roster.uncertain_fields.append("course_title")
                diagnostics.append("course: different values on different pages")
        else:
            first_page = doc.pages[0].lines[:15] if doc.pages else []
            for line in first_page:
                text = line.cells[0].text
                if r.course_line.match(text) and not r.id_candidate.search(line.text):
                    roster.course_title = text
                    roster.uncertain_fields.append("course_title")
                    diagnostics.append("course: no label; guessed from a SUBJECT NUMBER line")
                    break

        # Section
        if sections:
            roster.section = sections[0]
            if len(set(sections)) > 1:
                roster.uncertain_fields.append("section")
                diagnostics.append("section: different values on different pages")
        else:
            for line in doc.lines[:40]:
                m = r.section_fallback.search(line.text)
                if m:
                    roster.section = m.group(1)
                    roster.uncertain_fields.append("section")
                    diagnostics.append("section: found without a 'Section:' label")
                    break

        if counts:
            roster.expected_count = counts[0]
            roster.expected_count_source = "stated"
            if len(set(counts)) > 1:
                diagnostics.append("enrolled count: different values on different pages")

    # -- table structure ----------------------------------------------------

    def _find_header(self, lines: list[TextLine]) -> tuple[int | None, list[tuple[str, float, float]] | None]:
        r = self.rules
        for idx, line in enumerate(lines):
            roles = []
            for cell in line.cells:
                t = cell.text.strip()
                if r.header_id.match(t):
                    role = "id"
                elif r.header_last.match(t):
                    role = "last"
                elif r.header_first.match(t):
                    role = "first"
                elif r.header_name.match(t):
                    role = "name"
                elif r.header_number.match(t):
                    role = "number"
                else:
                    role = "other"
                roles.append((role, cell.x0, cell.x1))
            kinds = {role for role, _, _ in roles}
            if "id" in kinds and ({"name"} & kinds or {"first", "last"} <= kinds):
                return idx, roles
        return None, None

    def _segments(self, line: TextLine) -> list[_Segment]:
        """Cells split around ID candidates; page-furniture cells removed."""
        segs: list[_Segment] = []
        for cell in line.cells:
            if self.rules.ignore_cell.match(cell.text.strip()):
                continue
            segs.extend(self._split_cell(cell))
        return segs

    def _split_cell(self, cell: TextCell) -> list[_Segment]:
        text = cell.text
        matches = list(self.rules.id_candidate.finditer(text))
        if not matches:
            return [_Segment(text, cell.x0, cell.x1)]
        width = (cell.x1 - cell.x0) / max(len(text), 1)

        def piece(a: int, b: int, is_id: bool) -> _Segment | None:
            chunk = text[a:b]
            stripped = chunk.strip()
            if not stripped:
                return None
            lead = len(chunk) - len(chunk.lstrip())
            x0 = cell.x0 + (a + lead) * width
            return _Segment(stripped, x0, x0 + len(stripped) * width, is_id)

        out, pos = [], 0
        for m in matches:
            out += [piece(pos, m.start(), False), piece(m.start(), m.end(), True)]
            pos = m.end()
        out.append(piece(pos, len(text), False))
        return [s for s in out if s]

    @staticmethod
    def _assign(seg: _Segment, columns: list[tuple[str, float, float]]) -> str:
        best, best_overlap, best_dist = "other", 0.0, float("inf")
        for role, x0, x1 in columns:
            overlap = min(seg.x1, x1) - max(seg.x0, x0)
            if overlap > best_overlap:
                best, best_overlap = role, overlap
            elif best_overlap <= 0:
                dist = max(x0 - seg.x1, seg.x0 - x1, 0)
                if dist < best_dist:
                    best, best_dist = role, dist
        return best

    # -- rows ---------------------------------------------------------------

    def _read_page_rows(self, lines, header_idx, columns, page_no, diagnostics) -> list[_Row]:
        r = self.rules
        start = header_idx + 1 if header_idx is not None else 0
        body = lines[start:]
        kinds = []  # "id" | "meta" | "skip" | "text"
        for line in body:
            segs = self._segments(line)
            if self._labelled_values(line):
                kinds.append(("meta", segs))
            elif any(s.is_id for s in segs):
                kinds.append(("id", segs))
            elif any(any(ch.isalpha() for ch in s.text) for s in segs):
                kinds.append(("text", segs))
            else:
                kinds.append(("skip", segs))

        id_positions = [i for i, (k, _) in enumerate(kinds) if k == "id"]
        if not id_positions:
            return []
        # Without a header we can't tell where the table starts, so only
        # extend the table region upward when a header marks its top.
        in_table = self._table_region(body, id_positions, extend_up=header_idx is not None)

        rows: list[_Row] = []
        for i, (kind, segs) in enumerate(kinds):
            if kind == "id":
                rows.extend(self._rows_from_segments(segs, columns, page_no))
            elif kind == "text" and in_table[i]:
                row = self._row_without_id(segs, columns, page_no)
                if row:
                    rows.append(row)
        missing = sum(1 for row in rows if not row.w_number)
        diagnostics.append(f"page {page_no}: {len(rows)} rows ({missing} without an ID)")
        return rows

    def _table_region(self, body: list[TextLine], id_positions: list[int], extend_up: bool) -> list[bool]:
        """Lines between/around ID rows that are spaced like table rows."""
        ys = [body[i].y for i in id_positions]
        gaps = [a - b for a, b in zip(ys, ys[1:]) if a - b > 0]
        pitch = statistics.median(gaps) if gaps else 1.8 * body[id_positions[0]].font_size
        limit = self.rules.table_gap_pitches * pitch
        inside = [False] * len(body)
        first, last = id_positions[0], id_positions[-1]
        for i in range(first, last + 1):
            inside[i] = True
        i = first
        while extend_up and i > 0 and body[i - 1].y - body[i].y <= limit:
            i -= 1
            inside[i] = True
        i = last
        while i + 1 < len(body) and body[i].y - body[i + 1].y <= limit:
            i += 1
            inside[i] = True
        return inside

    def _rows_from_segments(self, segs: list[_Segment], columns, page_no: int) -> list[_Row]:
        id_count = sum(1 for s in segs if s.is_id)
        use_columns = columns and id_count == 1
        if use_columns:
            row = self._row_by_columns(segs, columns, page_no)
            if row.name_text or row.first or row.last:
                return [row]
        rows = self._rows_sequential(segs, page_no)
        if columns and id_count > 1:
            for row in rows:
                row.note = row.note or NOTE_MULTI_ID
        return rows

    def _row_by_columns(self, segs, columns, page_no) -> _Row:
        parts: dict[str, list[str]] = {}
        w_number = ""
        before_id: list[_Segment] = []
        seen_id = False
        for s in segs:
            if s.is_id:
                w_number, seen_id = s.text, True
                continue
            if not seen_id:
                before_id.append(s)
            parts.setdefault(self._assign(s, columns), []).append(s.text)
        row = _Row(page=page_no, w_number=w_number)
        if "first" in parts or "last" in parts:
            row.first = " ".join(parts.get("first", []))
            row.last = " ".join(parts.get("last", []))
        else:
            row.name_text = " ".join(parts.get("name", []))
        row.row_number = self._row_number(parts.get("number", []) or [s.text for s in before_id[-1:]])
        return row

    def _rows_sequential(self, segs: list[_Segment], page_no: int) -> list[_Row]:
        """No usable header: each ID owns the next text segment (else the previous one)."""
        rows: list[_Row] = []
        id_idx = [i for i, s in enumerate(segs) if s.is_id]
        for n, i in enumerate(id_idx):
            nxt = id_idx[n + 1] if n + 1 < len(id_idx) else len(segs)
            prv = id_idx[n - 1] + 1 if n > 0 else 0
            after = [s for s in segs[i + 1:nxt] if any(ch.isalpha() for ch in s.text)]
            before = [s for s in segs[prv:i] if any(ch.isalpha() for ch in s.text)]
            name = after[0].text if after else (before[-1].text if before else "")
            row = _Row(page=page_no, w_number=segs[i].text, name_text=name)
            row.row_number = self._row_number([s.text for s in segs[prv:i]][-1:])
            rows.append(row)
        return rows

    def _row_without_id(self, segs, columns, page_no) -> _Row | None:
        if columns:
            parts: dict[str, list[str]] = {}
            for s in segs:
                parts.setdefault(self._assign(s, columns), []).append(s.text)
            if "first" in parts or "last" in parts:
                return _Row(page_no, "", first=" ".join(parts.get("first", [])),
                            last=" ".join(parts.get("last", [])), note=NOTE_NO_ID)
            text = " ".join(parts.get("name", []))
        else:
            text = next((s.text for s in segs if any(ch.isalpha() for ch in s.text)), "")
        if not text:
            return None
        return _Row(page_no, "", name_text=text, note=NOTE_NO_ID)

    @staticmethod
    def _row_number(texts: list[str]) -> int | None:
        for t in texts:
            t = t.strip().rstrip(".")
            if t.isdigit() and 0 < int(t) < 10000:
                return int(t)
        return None

    # -- whole-roster checks -------------------------------------------------

    def _post_process(self, rows: list[_Row], roster: Roster, diagnostics: list[str]) -> None:
        r = self.rules
        # Row numbering as an extra count check, when most rows are numbered.
        numbers = [row.row_number for row in rows if row.row_number is not None]
        if rows and len(numbers) >= 0.8 * len(rows) and min(numbers) == 1:
            if roster.expected_count is None:
                roster.expected_count = max(numbers)
                roster.expected_count_source = "row_numbers"
            elif max(numbers) != roster.expected_count:
                diagnostics.append("row numbering disagrees with stated enrolled count")

        # Comma consistency for single-column names.
        named = [row for row in rows if row.first is None and row.name_text]
        if r.name_order == "auto" and len(named) >= 3:
            with_comma = sum(1 for row in named if "," in row.name_text)
            if with_comma >= r.comma_majority * len(named):
                for row in named:
                    if "," not in row.name_text and not row.note:
                        row.note = NOTE_NO_COMMA

    def _to_student(self, row: _Row) -> Student:
        if row.first is not None or row.last is not None:
            first, last, note = row.first or "", row.last or "", None
        else:
            split = split_name(row.name_text, self.rules.name_order)
            first, last, note = split.first, split.last, split.note
        student = Student(
            w_number=row.w_number,
            first_name=first,
            last_name=last,
            review_note=row.note or note,
            source_page=row.page,
        )
        return student


DEFAULT_PARSER = TableRosterParser()


def parse_roster(doc: ExtractedDocument, parser: RosterParser | None = None) -> Roster:
    result = (parser or DEFAULT_PARSER).parse(doc)
    return normalize_roster(result.roster)
