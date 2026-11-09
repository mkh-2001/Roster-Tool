"""Start Roster Tool:  python -m roster_tool

Starts the local-only server on 127.0.0.1 (random free port) and opens the UI
in the default browser. Press Ctrl+C to quit. Packaging into a double-click
desktop app is described in PACKAGING.md.
"""

from __future__ import annotations

import argparse
import logging
import webbrowser

from .help_sheet_generator import ensure_fonts
from .server import RosterToolServer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="roster_tool", description="Offline roster tool (local only).")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: random free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_fonts()  # pre-warm so the first help sheet is as fast as later ones
    server = RosterToolServer(port=args.port)
    print(f"Roster Tool is running at {server.url}  (this computer only; press Ctrl+C to quit)")
    if not args.no_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
