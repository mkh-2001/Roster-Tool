"""Lightweight sanity checks on a normalized roster.

Severity "error" blocks every output (help sheet and CSV). Severity "warning"
is shown to the employee but does not block.

Messages refer to rows by number, never by student name or W number, so an
issue list is safe to display but still never needs to be logged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import W_NUMBER_DESCRIPTION, Roster, is_valid_w_number

ERROR = "error"
WARNING = "warning"

# A name starting with one of these can be interpreted as a formula by
# spreadsheet software. Real names essentially never do, so it is also a good
# signal of an extraction problem.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    row: int | None = None  # 0-based index into roster.students
    field: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def validate_roster(roster: Roster) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    students = roster.students

    if not students:
        issues.append(ValidationIssue(
            ERROR, "no_students",
            "No students were found. This PDF may not be a class roster, or its layout is not supported yet.",
        ))

    rows_by_w: dict[str, list[int]] = {}
    for i, s in enumerate(students):
        label = f"Row {i + 1}"
        if not s.w_number:
            issues.append(ValidationIssue(ERROR, "missing_w_number", f"{label}: no W number.", i, "w_number"))
        elif not is_valid_w_number(s.w_number):
            issues.append(ValidationIssue(
                ERROR, "malformed_w_number",
                f"{label}: W number is not in the expected format ({W_NUMBER_DESCRIPTION}).", i, "w_number",
            ))
        else:
            rows_by_w.setdefault(s.w_number, []).append(i)

        if not s.first_name:
            issues.append(ValidationIssue(ERROR, "missing_first_name", f"{label}: first name is empty.", i, "first_name"))
        if not s.last_name:
            issues.append(ValidationIssue(ERROR, "missing_last_name", f"{label}: last name is empty.", i, "last_name"))

        for field_name in ("first_name", "last_name"):
            value = getattr(s, field_name)
            if any(ch.isdigit() for ch in value):
                issues.append(ValidationIssue(
                    WARNING, "name_contains_digits", f"{label}: name contains digits; check extraction.", i, field_name,
                ))
            elif value.startswith(_FORMULA_PREFIXES):
                issues.append(ValidationIssue(
                    WARNING, "name_unusual_start", f"{label}: name starts with an unusual character.", i, field_name,
                ))

        # A parser note is listed only when the row has no error of its own
        # (otherwise the error already sends the employee to this row).
        row_has_error = any(x.row == i and x.severity == ERROR for x in issues)
        if s.review_note and not row_has_error:
            issues.append(ValidationIssue(WARNING, "needs_review", f"{label}: {s.review_note}", i))

    for w_rows in rows_by_w.values():
        if len(w_rows) > 1:
            listed = ", ".join(str(r + 1) for r in w_rows)
            for r in w_rows:
                issues.append(ValidationIssue(
                    ERROR, "duplicate_w_number", f"Rows {listed} have the same W number.", r, "w_number",
                ))

    # Counts are checked per population. `field` names the roster attribute
    # holding the expectation (the UI lets the employee accept the list as is).
    n_current = len(roster.current_students)
    if roster.expected_count is not None and roster.expected_count != n_current:
        if roster.expected_count_source == "row_numbers":
            basis = f"The PDF numbers its rows up to {roster.expected_count}"
        else:
            basis = f"The PDF says {roster.expected_count} students are enrolled"
        issues.append(ValidationIssue(
            ERROR, "count_mismatch",
            f"{basis}, but {n_current} student rows were found. There may be a parsing problem.",
            field="expected_count",
        ))
    n_wait = len(roster.waitlist_students)
    if roster.expected_waitlist_count is not None and roster.expected_waitlist_count != n_wait:
        issues.append(ValidationIssue(
            ERROR, "count_mismatch",
            f"The PDF numbers its Wait List rows up to {roster.expected_waitlist_count}, but {n_wait} "
            "wait-list rows were found. There may be a parsing problem.",
            field="expected_waitlist_count",
        ))

    if not roster.course_title:
        issues.append(ValidationIssue(WARNING, "missing_course_title", "Course title was not found. Please enter it.", field="course_title"))
    elif "course_title" in roster.uncertain_fields:
        issues.append(ValidationIssue(WARNING, "uncertain_course_title", "Please confirm the course title.", field="course_title"))
    if not roster.section:
        issues.append(ValidationIssue(WARNING, "missing_section", "Section was not found. Please enter it.", field="section"))
    elif "section" in roster.uncertain_fields:
        issues.append(ValidationIssue(WARNING, "uncertain_section", "Please confirm the section.", field="section"))

    return issues


def blocking_errors(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return [i for i in issues if i.severity == ERROR]


class OutputBlockedError(Exception):
    """Raised by a generator when the roster has errors that must be fixed first."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__(f"{len(issues)} problem(s) must be fixed before generating output")
