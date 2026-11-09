import pytest

from fixture_builder import ALL_FIXTURES, FIXTURE_DIR, FixtureSpec, build_pdf
from roster_tool.models import Roster, Student
from roster_tool.pipeline import process_pdf


@pytest.fixture(scope="session")
def fixture_results():
    """Each committed fixture PDF processed once: {filename: (spec, ProcessResult)}."""
    return {spec.filename: (spec, process_pdf((FIXTURE_DIR / spec.filename).read_bytes())) for spec in ALL_FIXTURES}


def parse_spec(spec: FixtureSpec):
    return process_pdf(build_pdf(spec))


def make_roster(n: int = 3, **kwargs) -> Roster:
    students = [Student(f"W{1000000 + i:07d}", f"First{chr(65 + i % 26)}", f"Last{chr(65 + i % 26)}") for i in range(n)]
    defaults = dict(course_title="PHIL 320 Logic and Critical Reasoning", section="10141", students=students)
    defaults.update(kwargs)
    return Roster(**defaults)
