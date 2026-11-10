"""Entry point of the packaged desktop app (frozen by PyInstaller). Keep it tiny:
all launch behaviour lives in roster_tool/desktop.py, which also runs from source."""

import sys

from roster_tool.desktop import main

if __name__ == "__main__":
    sys.exit(main())
