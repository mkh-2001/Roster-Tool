"""The project must not depend on where it is stored.

Guards folder renames/moves (e.g. removing a trailing space from the folder
name) and future packaging, where the app runs from an unknown location.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()
SKIP_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git", "private_rosters", "build", "dist"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".html", ".js", ".css"}


def project_text_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix in TEXT_SUFFIXES and path.resolve() != THIS_FILE:
                yield path


def test_project_files_contain_no_absolute_paths():
    needles = {str(ROOT), str(Path.home()), "/Users/", "/home/", "C:\\Users\\"}
    offenders = []
    for path in project_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        offenders += [f"{path.relative_to(ROOT)} contains {n!r}" for n in needles if n in text]
    assert offenders == []


def test_runtime_resources_resolve_inside_the_package():
    from roster_tool import help_sheet_generator, server

    package = ROOT / "roster_tool"
    assert server.STATIC_DIR == package / "static"
    assert help_sheet_generator.FONT_DIR == package / "assets" / "fonts"
    for filename, _ in server.STATIC_FILES.values():
        assert (server.STATIC_DIR / filename).is_file()
    assert (help_sheet_generator.FONT_DIR / "DejaVuSansCondensed.ttf").is_file()
