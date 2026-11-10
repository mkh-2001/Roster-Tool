"""Desktop launcher: what runs when staff double-click Roster Tool.

1. Set up a small, privacy-safe diagnostic log.
2. Check the bundled resources and native components (PDF engine, fonts, XLSX reader).
3. Start the local-only server (127.0.0.1, random free port, per-launch token).
4. Confirm the server answers, THEN open the UI in the default browser.
5. Keep running until no Roster Tool tab is open (lifecycle.SessionMonitor), the
   Quit button is used, or the operating system asks the app to stop.

Everything runs in this one process, so stopping it can never leave an
orphaned server behind. Startup problems are shown in a native dialog and
written to the log. Neither ever contains roster data.

Used both from source (`python -m roster_tool`) and by the packaged app
(packaging/launcher.py).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import platform
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__, resources

LOG_FILE = "roster-tool.log"
LOG_MAX_BYTES = 256 * 1024
LOG_BACKUPS = 2  # at most ~768 KB of logs, ever

log = logging.getLogger("roster_tool.desktop")
_log_path: Path | None = None


# --- Diagnostic log --------------------------------------------------------------

def log_dir() -> Path:
    """Per-user log folder (ROSTER_TOOL_LOG_DIR overrides it, e.g. for tests)."""
    override = os.environ.get("ROSTER_TOOL_LOG_DIR")
    if override:
        return Path(override)
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / APP_NAME / "Logs"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else home / ".local" / "state") / "roster-tool"


def configure_logging(console: bool) -> Path | None:
    """Rotating operational log (no roster data is ever logged anywhere)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in [h for h in root.handlers if getattr(h, "_roster_tool", False)]:
        root.removeHandler(handler)
        handler.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = None
    try:
        folder = log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / LOG_FILE
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                                       encoding="utf-8")
        handler.setFormatter(fmt)
        handler._roster_tool = True
        root.addHandler(handler)
    except OSError:
        path = None  # no writable log folder: run without a file log
    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream._roster_tool = True
        root.addHandler(stream)
    return path


# --- Startup checks --------------------------------------------------------------

def self_check() -> list[str]:
    """Problems that would stop the app from working (user-facing, no data)."""
    problems = []
    missing = resources.missing_resources()
    if missing:
        problems.append("Missing application files: " + ", ".join(missing))
    try:
        import pypdfium2
        pypdfium2.PdfDocument.new().close()  # loads the native PDF engine
    except Exception as exc:
        problems.append(f"The PDF engine could not be loaded ({type(exc).__name__}).")
    try:
        import openpyxl  # noqa: F401
    except Exception as exc:
        problems.append(f"The Excel (.xlsx) reader could not be loaded ({type(exc).__name__}).")
    if not missing:
        try:
            from .help_sheet_generator import ensure_fonts
            ensure_fonts()
        except Exception as exc:
            problems.append(f"The help-sheet fonts could not be loaded ({type(exc).__name__}).")
    return problems


def probe(url: str, timeout: float = 5.0) -> bool:
    """Does our own server answer? (Direct loopback request; proxies are never used.)"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200 and b'name="roster-token"' in response.read()
    except OSError:
        return False


# --- Native error dialog ----------------------------------------------------------

def _applescript_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def show_error(message: str) -> None:
    """Tell the user (native dialog) and record it in the log. Never include roster data."""
    log.error("problem shown to user: %s", message.replace("\n", " "))
    display_dialog(f"{message}\n\nDiagnostic log: {_log_path or 'not available'}")


def display_dialog(text: str) -> None:
    """A native error dialog without any GUI toolkit (osascript / Win32 MessageBox)."""
    try:
        if sys.platform == "darwin":
            script = (f'display dialog "{_applescript_string(text)}" with title "{APP_NAME}" '
                      'buttons {"OK"} default button "OK" with icon stop')
            subprocess.run(["/usr/bin/osascript", "-e", script], check=False, capture_output=True, timeout=3600)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)  # MB_ICONERROR
        elif sys.stderr is not None:
            print(text, file=sys.stderr)
    except Exception:
        if sys.stderr is not None:
            print(text, file=sys.stderr)


# --- Main --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    global _log_path
    parser = argparse.ArgumentParser(prog="roster_tool", description=f"{APP_NAME}: offline class-roster utility.")
    parser.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: a random free port)")
    parser.add_argument("--no-browser", action="store_true",
                        help="do not open the browser; also keeps running until Ctrl+C (development)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)
    no_browser = args.no_browser or os.environ.get("ROSTER_TOOL_NO_BROWSER") == "1"
    auto_exit = not no_browser or os.environ.get("ROSTER_TOOL_AUTO_EXIT") == "1"

    # Live log on the console from source, or when the packaged app was started
    # from a terminal / is the Windows debug build (a double-clicked app has none).
    on_terminal = sys.stderr is not None and getattr(sys.stderr, "isatty", lambda: False)()
    _log_path = configure_logging(console=not resources.is_frozen() or on_terminal)
    log.info("%s %s starting (%s %s, Python %s, %s)", APP_NAME, __version__, platform.system(),
             platform.machine(), platform.python_version(), "packaged" if resources.is_frozen() else "source")

    stop = threading.Event()
    why: list[str] = []

    def request_stop(reason: str) -> None:
        if not why:
            why.append(reason)
        stop.set()

    server = None
    previous_handlers: dict = {}
    try:
        t0 = time.perf_counter()
        problems = self_check()
        if problems:
            show_error(f"{APP_NAME} could not start:\n\n" + "\n".join(problems))
            return 1
        log.info("startup checks passed in %.0f ms", (time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        from .lifecycle import SessionMonitor
        from .server import RosterToolServer
        log.info("application modules loaded in %.0f ms", (time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        try:
            server = RosterToolServer(port=args.port)  # always 127.0.0.1
        except OSError as exc:
            show_error(f"{APP_NAME} could not start its local connection ({exc.strerror or type(exc).__name__}).")
            return 1
        log.info("local server bound in %.0f ms", (time.perf_counter() - t0) * 1000)
        monitor = SessionMonitor(on_exit=request_stop) if auto_exit else None
        server.lifecycle = monitor
        server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                                         name="server", daemon=True)
        server_thread.start()
        if not probe(server.url):
            show_error(f"{APP_NAME} started, but its local page did not respond.")
            return 1
        log.info("listening on %s (this computer only)", server.url)
        if not resources.is_frozen() and sys.stdout is not None:
            print(f"{APP_NAME} {__version__} is running at {server.url} (this computer only). "
                  "Close its browser tab, click Quit, or press Ctrl+C to stop.", flush=True)
        if monitor is not None:
            threading.Thread(target=monitor.run, name="session-monitor", daemon=True).start()

        for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None), getattr(signal, "SIGHUP", None)):
            if sig is not None:
                previous_handlers[sig] = signal.signal(sig, lambda *_: request_stop("stop requested by the operating system"))

        if not no_browser:
            try:
                opened = webbrowser.open(server.url)
            except Exception:
                opened = False
            if not opened:
                show_error(f"{APP_NAME} is running, but your web browser could not be opened automatically.\n\n"
                           f"Open this address in your web browser:\n{server.url}")

        while not stop.wait(0.5):
            if not server_thread.is_alive():
                request_stop("server stopped")
        log.info("shutting down: %s", why[0] if why else "unknown")
        return 0
    except KeyboardInterrupt:
        log.info("shutting down: Ctrl+C")
        return 0
    except Exception as exc:  # startup/shutdown code only; request errors are handled by the server
        log.exception("unexpected error in the launcher")
        show_error(f"{APP_NAME} stopped because of an unexpected problem ({type(exc).__name__}).")
        return 1
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)
        log.info("stopped")
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_roster_tool", False):
                handler.flush()
