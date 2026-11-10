"""The one place that knows where runtime resources live.

Works unchanged from source and inside a PyInstaller-frozen app: PyInstaller
sets each module's `__file__` inside the bundle, and the build places the
`static/` and `assets/` folders next to the package modules there (see
packaging/roster_tool.spec). Nothing else in the code base should build
resource paths itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
FONT_DIR = PACKAGE_DIR / "assets" / "fonts"

# Files the application cannot run without.
REQUIRED_FILES = (
    STATIC_DIR / "index.html",
    STATIC_DIR / "app.js",
    STATIC_DIR / "styles.css",
    FONT_DIR / "DejaVuSansCondensed.ttf",
    FONT_DIR / "DejaVuSansCondensed-Bold.ttf",
    FONT_DIR / "DejaVu-LICENSE.txt",
)


def is_frozen() -> bool:
    """True when running from a packaged (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False))


def missing_resources() -> list[str]:
    """Required files that are missing, relative to the package (empty when all is well)."""
    return [str(p.relative_to(PACKAGE_DIR)) for p in REQUIRED_FILES if not p.is_file()]
