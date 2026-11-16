"""Optional regression tests against REAL rosters kept OUTSIDE version control.

Real PDFs live in  private_rosters/  (git-ignored) together with a hand-written
private_rosters/expected.json, e.g.:

    {
      "fall_phil320.pdf": {
        "course_title": "PHIL 320 Logic and Critical Reasoning",
        "section": "10141",
        "student_count": 32,
        "students": [{"w_number": "W...", "first_name": "...", "last_name": "..."}]
      }
    }

Every key except the filename is optional. These tests are skipped wherever the
folder does not exist (including the repository itself). Failure output can
contain student data: keep it on the staff computer.
"""

import json
from pathlib import Path

import pytest

from roster_tool.event_csv_generator import generate_csv
from roster_tool.help_sheet_generator import generate_help_sheet
from roster_tool.pipeline import process_pdf

PRIVATE = Path(__file__).resolve().parent.parent / "private_rosters"
EXPECTED = PRIVATE / "expected.json"
# utf-8-sig: this file is written by hand, and Windows editors tend to add a BOM.
CASES = json.loads(EXPECTED.read_text(encoding="utf-8-sig")) if EXPECTED.exists() else {}


@pytest.mark.skipif(not CASES, reason="no private real-roster fixtures on this machine")
@pytest.mark.parametrize("filename", sorted(CASES) or ["-"])
def test_real_roster(filename):
    exp = CASES[filename]
    result = process_pdf((PRIVATE / filename).read_bytes())
    roster = result.roster
    errors = [i.message for i in result.issues if i.severity == "error"]
    assert errors == []
    if "course_title" in exp:
        assert roster.course_title == exp["course_title"]
    if "section" in exp:
        assert roster.section == exp["section"]
    if "student_count" in exp:
        assert len(roster.students) == exp["student_count"]
    if "students" in exp:
        got = [(s.w_number, s.first_name, s.last_name) for s in roster.students]
        want = [(e["w_number"], e["first_name"], e["last_name"]) for e in exp["students"]]
        assert got == want
    assert result.timings_ms["total"] < 2000
    generate_csv(roster)
    generate_help_sheet(roster, "Test Tutor", ["Tuesday", "Thursday"])
