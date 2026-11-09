"""Regenerate the FAKE development roster PDFs in tests/fixtures/.

Usage:  .venv/bin/python scripts/make_fixtures.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fixture_builder import write_fixtures  # noqa: E402

if __name__ == "__main__":
    for path in write_fixtures():
        print(f"wrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")
