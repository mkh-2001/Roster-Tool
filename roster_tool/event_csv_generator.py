"""Event Invites CSV: the exact import format, nothing more.

    Email,First Name,Last Name,SSO ID,Student ID
    W1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567

SSO ID == Student ID == W number, exactly as on the roster (leading W kept).
The header and field rules are locked down by tests/test_event_csv.py.
"""

from __future__ import annotations

import csv
import io

from .models import Roster, output_stem
from .validation import OutputBlockedError, blocking_errors, validate_roster

HEADER = ("Email", "First Name", "Last Name", "SSO ID", "Student ID")
EMAIL_DOMAIN = "apps.losrios.edu"
LINE_TERMINATOR = "\r\n"  # RFC 4180
ENCODING = "utf-8"        # no byte-order mark: a BOM would make the first header "﻿Email"


def build_rows(roster: Roster) -> list[tuple[str, str, str, str, str]]:
    return [
        (f"{s.w_number}@{EMAIL_DOMAIN}", s.first_name, s.last_name, s.w_number, s.w_number)
        for s in roster.students
    ]


def generate_csv(roster: Roster) -> tuple[str, list[tuple[str, ...]]]:
    """Return (csv_text, rows). Raises OutputBlockedError if the roster has errors."""
    errors = blocking_errors(validate_roster(roster))
    if errors:
        raise OutputBlockedError(errors)
    rows = build_rows(roster)
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator=LINE_TERMINATOR, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(HEADER)
    writer.writerows(rows)
    return buf.getvalue(), rows


def generate_csv_bytes(roster: Roster) -> bytes:
    text, _ = generate_csv(roster)
    return text.encode(ENCODING)


def csv_filename(roster: Roster) -> str:
    return f"event_invites_{output_stem(roster)}.csv"
