"""Normalized roster model.

Everything downstream of the roster parser (validation, the help-sheet
generator, the event-CSV generator, the UI) consumes ONLY these types. None of
them know anything about how the source PDF was laid out.

The model deliberately allows *invalid* data (an empty or malformed W number,
an empty name). The parser must surface problem rows instead of dropping them;
`validation.validate_roster` decides whether output may be generated.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# Valid W-number format: uppercase "W" followed by exactly 7 digits. This
# single pattern is used by both the parser and the validator. (Real Los Rios
# rosters print the 7 digits without the W; the parser adds it, see
# ParseRules.printed_student_id.)
W_NUMBER_RE = re.compile(r"W\d{7}")
W_NUMBER_DESCRIPTION = "W followed by 7 digits"

# Roster populations. Dropped students are never part of the model.
CURRENT = "current"    # enrolled ("Current Students")
WAITLIST = "waitlist"  # "Wait List"
STATUSES = (CURRENT, WAITLIST)

# Limits for data arriving from the UI (a trust boundary, even though local).
MAX_STUDENTS = 2000
MAX_TEXT_LEN = 300

_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """Canonicalize text without changing its spelling.

    - Unicode NFC (composes accents into canonical form; never strips them)
    - any run of whitespace (tabs, non-breaking spaces, newlines) -> one space
    - leading/trailing whitespace removed
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return _WHITESPACE_RE.sub(" ", text).strip()


def is_valid_w_number(value: str) -> bool:
    return bool(W_NUMBER_RE.fullmatch(value or ""))


@dataclass
class Student:
    w_number: str
    first_name: str
    last_name: str
    # Session-only hint from the parser, e.g. "Name has more than two parts".
    # Never written to any output file.
    review_note: str | None = None
    # 1-based page in the source PDF (shown in the UI to help find the row).
    source_page: int | None = None
    # Enrollment population: CURRENT or WAITLIST.
    status: str = CURRENT

    @property
    def display_name(self) -> str:
        if self.last_name and self.first_name:
            return f"{self.last_name}, {self.first_name}"
        return self.last_name or self.first_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "w_number": self.w_number,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "display_name": self.display_name,
            "review_note": self.review_note,
            "source_page": self.source_page,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Student":
        if not isinstance(data, dict):
            raise ValueError("student must be an object")
        note = clean_text(_bounded(data.get("review_note"), "review_note"))
        page = data.get("source_page")
        status = data.get("status", CURRENT)
        if status not in STATUSES:
            raise ValueError("student status must be 'current' or 'waitlist'")
        return cls(
            # W numbers are only trimmed, never otherwise altered.
            w_number=_bounded(data.get("w_number"), "w_number").strip(),
            first_name=clean_text(_bounded(data.get("first_name"), "first_name")),
            last_name=clean_text(_bounded(data.get("last_name"), "last_name")),
            review_note=note or None,
            source_page=page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None,
            status=status,
        )


@dataclass
class Roster:
    course_title: str = ""
    section: str = ""
    # Current Students and Wait List students, each tagged with `status`
    # (in source order: current first, then wait list).
    students: list[Student] = field(default_factory=list)
    # Current Students count stated by the PDF itself ("Total Enrolled: 32") or
    # implied by its row numbering. None when the PDF gives no such cross-check.
    expected_count: int | None = None
    expected_count_source: str | None = None  # "stated" | "row_numbers"
    # Fields extracted with low confidence that the employee should confirm.
    uncertain_fields: list[str] = field(default_factory=list)
    # Wait List count implied by the PDF's Wait List row numbering, if any.
    expected_waitlist_count: int | None = None
    # Header information shown on the Help Sheet ("" when the roster doesn't give it).
    professor: str = ""
    term: str = ""
    meetings_location: str = ""  # e.g. "Room 210", or "Room 101 (LEC); Room 102 (LAB)"

    @property
    def current_students(self) -> list[Student]:
        return [s for s in self.students if s.status == CURRENT]

    @property
    def waitlist_students(self) -> list[Student]:
        return [s for s in self.students if s.status == WAITLIST]

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_title": self.course_title,
            "section": self.section,
            "students": [s.to_dict() for s in self.students],
            "expected_count": self.expected_count,
            "expected_count_source": self.expected_count_source,
            "uncertain_fields": list(self.uncertain_fields),
            "expected_waitlist_count": self.expected_waitlist_count,
            "professor": self.professor,
            "term": self.term,
            "meetings_location": self.meetings_location,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Roster":
        """Rebuild a roster sent back by the UI (possibly edited). Strict about shape."""
        if not isinstance(data, dict):
            raise ValueError("roster must be an object")
        students_raw = data.get("students")
        if not isinstance(students_raw, list):
            raise ValueError("roster.students must be a list")
        if len(students_raw) > MAX_STUDENTS:
            raise ValueError("too many students")
        expected = _optional_count(data.get("expected_count"), "expected_count")
        expected_wait = _optional_count(data.get("expected_waitlist_count"), "expected_waitlist_count")
        source = data.get("expected_count_source")
        uncertain = data.get("uncertain_fields") or []
        if not isinstance(uncertain, list):
            raise ValueError("uncertain_fields must be a list")
        return cls(
            course_title=clean_text(_bounded(data.get("course_title"), "course_title")),
            section=clean_text(_bounded(data.get("section"), "section")),
            students=[Student.from_dict(s) for s in students_raw],
            expected_count=expected,
            expected_count_source=source if source in ("stated", "row_numbers") else None,
            uncertain_fields=[f for f in uncertain if f in ("course_title", "section")],
            expected_waitlist_count=expected_wait,
            professor=_header_text(data.get("professor")),
            term=_header_text(data.get("term")),
            meetings_location=_header_text(data.get("meetings_location")),
        )


def normalize_roster(roster: Roster) -> Roster:
    """Canonicalize all text fields in place (idempotent) and return the roster."""
    roster.course_title = clean_text(roster.course_title)
    roster.section = clean_text(roster.section)
    roster.professor = clean_text(roster.professor)
    roster.term = clean_text(roster.term)
    roster.meetings_location = clean_text(roster.meetings_location)
    for s in roster.students:
        s.w_number = (s.w_number or "").strip()
        s.first_name = clean_text(s.first_name)
        s.last_name = clean_text(s.last_name)
    return roster


def output_stem(roster: Roster) -> str:
    """Filesystem-safe name like 'PHIL_320_10141' (course code + section)."""
    course_code = " ".join(roster.course_title.split(" ")[:2])
    parts = [re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_")[:40] for p in (course_code, roster.section)]
    return "_".join(p for p in parts if p) or "roster"


def _optional_count(value: Any, name: str) -> int | None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or null")
    return value


def _header_text(value: Any) -> str:
    """Help-Sheet-only header text (professor, term, meetings location).

    It never rejects the roster: a value that isn't a short string is dropped,
    so it can't block validation or the Event CSV, which never uses it.
    """
    if isinstance(value, str) and len(value) <= MAX_TEXT_LEN:
        return clean_text(value)
    return ""


def _bounded(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if len(value) > MAX_TEXT_LEN:
        raise ValueError(f"{name} is too long")
    return value
