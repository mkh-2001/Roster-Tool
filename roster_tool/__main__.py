"""Start Roster Tool from source:  python -m roster_tool [--no-browser] [--port N]

Same launcher as the packaged desktop app (roster_tool/desktop.py): starts the
local-only server on 127.0.0.1 and opens the UI in the default browser. The
app stops when its browser tab is closed, when Quit is clicked, or on Ctrl+C.
Packaging is described in PACKAGING.md.
"""

from .desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
