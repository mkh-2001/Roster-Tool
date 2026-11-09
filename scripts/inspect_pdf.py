"""Show what the extractor and parser see in a roster PDF (for adapting the parser).

    .venv/bin/python scripts/inspect_pdf.py path/to/roster.pdf            # text layout + parse summary
    .venv/bin/python scripts/inspect_pdf.py path/to/roster.pdf --students # also list parsed students

WARNING: with a REAL roster this prints student names and W numbers to your
terminal. Run it only on a staff computer, and never paste the output into
tickets, chats, emails or commits.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from roster_tool.models import normalize_roster  # noqa: E402
from roster_tool.pdf_extractor import PdfExtractionError, extract_pdf  # noqa: E402
from roster_tool.roster_parser import DEFAULT_PARSER  # noqa: E402
from roster_tool.validation import validate_roster  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--students", action="store_true", help="list every parsed student")
    ap.add_argument("--no-text", action="store_true", help="skip the raw text dump")
    args = ap.parse_args()

    print("!! Output may contain student data. Do not share or commit it. !!\n", file=sys.stderr)
    try:
        doc = extract_pdf(args.pdf.read_bytes())
    except PdfExtractionError as exc:
        print(f"EXTRACTION FAILED: {type(exc).__name__}: {exc.user_message}")
        return 1

    if not args.no_text:
        print("# Extracted text  (one line per text line; ' | ' = column gap / cell boundary)")
        print(doc.debug_text())

    result = DEFAULT_PARSER.parse(doc)
    roster = normalize_roster(result.roster)
    issues = validate_roster(roster)
    print("\n# Parser diagnostics")
    for d in result.diagnostics:
        print(f"  - {d}")
    print("\n# Parse summary")
    print(f"  course title : {roster.course_title!r}")
    print(f"  section      : {roster.section!r}")
    print(f"  uncertain    : {roster.uncertain_fields}")
    print(f"  expected     : {roster.expected_count} ({roster.expected_count_source}); "
          f"wait list: {roster.expected_waitlist_count}")
    print(f"  students     : {len(roster.current_students)} current + {len(roster.waitlist_students)} wait list")
    print(f"  issues       : {sum(i.severity == 'error' for i in issues)} error(s), "
          f"{sum(i.severity == 'warning' for i in issues)} warning(s)")
    for i in issues:
        print(f"    [{i.severity}] {i.code}: {i.message}")
    if args.students:
        print("\n# Students  (row | W number | last | first | page | list | note)")
        for n, s in enumerate(roster.students, 1):
            print(f"  {n:>3} | {s.w_number:<10} | {s.last_name} | {s.first_name} | p{s.source_page} | "
                  f"{s.status} | {s.review_note or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
