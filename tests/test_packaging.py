"""Packaging / desktop-launch behaviour that can be tested from source.

(The built app itself is checked by scripts/smoke_test_package.py.)
"""

import importlib.metadata
import json
import logging
import logging.handlers
import re
import shutil
import socket
import subprocess
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import roster_tool
from roster_tool import desktop, lifecycle, resources
from roster_tool.lifecycle import SessionMonitor
from roster_tool.server import RosterToolServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_app  # noqa: E402


# --- Resources and version ------------------------------------------------------------

def test_resources_resolve_inside_the_package():
    assert resources.STATIC_DIR == resources.PACKAGE_DIR / "static"
    assert resources.FONT_DIR == resources.PACKAGE_DIR / "assets" / "fonts"
    assert resources.missing_resources() == []
    assert resources.is_frozen() is False


def test_missing_resources_are_reported_relative(monkeypatch):
    monkeypatch.setattr(resources, "REQUIRED_FILES", resources.REQUIRED_FILES + (resources.STATIC_DIR / "gone.js",))
    assert resources.missing_resources() == [str(Path("static") / "gone.js")]


def test_frozen_detection(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resources.is_frozen() is True


def test_version_has_a_single_source():
    assert re.fullmatch(r"\d+\.\d+\.\d+", roster_tool.__version__)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"] and "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "roster_tool.__version__"}
    spec = (ROOT / "packaging" / "roster_tool.spec").read_text()
    assert "from roster_tool import APP_NAME, __version__" in spec


# --- Local server -------------------------------------------------------------------

def test_binding_does_no_reverse_dns_lookup(monkeypatch):
    def no_lookups(*_):
        raise AssertionError("reverse DNS lookup attempted")
    monkeypatch.setattr(socket, "getfqdn", no_lookups)
    server = RosterToolServer()
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert server.server_name == "127.0.0.1"
    finally:
        server.server_close()


def _post(server, path, payload, token=True):
    req = urllib.request.Request(server.origin + path, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Roster-Token", server.token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class RecordingMonitor:
    def __init__(self):
        self.events = []

    def seen(self, client):
        self.events.append(("seen", client))

    def goodbye(self, client):
        self.events.append(("goodbye", client))

    def request_exit(self, reason):
        self.events.append(("exit", reason))


@pytest.fixture()
def server():
    srv = RosterToolServer(lifecycle=RecordingMonitor())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_lifecycle_endpoints_require_the_token(server):
    for path in ("/api/heartbeat", "/api/goodbye", "/api/shutdown"):
        assert _post(server, path, {"client": "abcdefgh-1"}, token=False) == 403
    assert server.lifecycle.events == []


def test_lifecycle_endpoints_reach_the_monitor(server):
    assert _post(server, "/api/heartbeat", {"client": "tab-0001"}) == 200
    assert _post(server, "/api/goodbye", {"client": "tab-0001"}) == 200
    assert _post(server, "/api/heartbeat", {"client": "bad id!"}) == 400
    assert _post(server, "/api/shutdown", {}) == 200
    assert server.lifecycle.events == [("seen", "tab-0001"), ("goodbye", "tab-0001"), ("exit", "Quit button")]


def test_quit_without_a_monitor_stops_the_server():
    srv = RosterToolServer()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    assert _post(srv, "/api/shutdown", {}) == 200
    thread.join(5)
    assert not thread.is_alive()
    srv.server_close()


def test_ui_shows_version_and_quit(server):
    with urllib.request.urlopen(server.url, timeout=5) as resp:
        html = resp.read().decode()
    assert f"Version {roster_tool.__version__}" in html and 'id="quit-btn"' in html


# --- When the app exits (fake clock) ---------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def monitor(clock):
    return SessionMonitor(on_exit=lambda reason: None, startup_timeout=180, idle_timeout=300,
                          goodbye_grace=10, clock=clock)


def test_exits_if_no_tab_ever_connects():
    clock = Clock()
    m = monitor(clock)
    clock.now += 179
    assert m.check() is None
    clock.now += 2
    assert m.check() == "no browser tab connected"


def test_stays_while_a_tab_is_open_and_exits_after_it_closes():
    clock = Clock()
    m = monitor(clock)
    m.seen("tab-a")
    for _ in range(20):  # heartbeats every 30 s for 10 minutes
        clock.now += 30
        m.seen("tab-a")
        assert m.check() is None
    m.goodbye("tab-a")
    clock.now += 9
    assert m.check() is None
    clock.now += 2
    assert m.check() == "all Roster Tool tabs closed"


def test_page_reload_does_not_exit():
    clock = Clock()
    m = monitor(clock)
    m.seen("tab-a")
    m.goodbye("tab-a")  # reload: old page says goodbye...
    clock.now += 3
    m.seen("tab-b")      # ...and the reloaded page checks in
    clock.now += 60
    assert m.check() is None


def test_closing_one_of_two_tabs_does_not_exit():
    clock = Clock()
    m = monitor(clock)
    m.seen("tab-a")
    m.seen("tab-b")
    m.goodbye("tab-a")
    clock.now += 60
    m.seen("tab-b")
    assert m.check() is None


def test_silent_tab_is_forgotten_then_app_exits():
    clock = Clock()
    m = monitor(clock)
    m.seen("tab-a")
    clock.now += 299
    assert m.check() is None  # a hidden, throttled tab may be quiet for minutes
    clock.now += 2
    assert m.check() is None  # forgotten now; grace period starts
    clock.now += 11
    assert m.check() == "all Roster Tool tabs closed"


def test_sleep_is_not_mistaken_for_a_closed_tab():
    clock = Clock()
    m = monitor(clock)
    m.seen("tab-a")
    clock.now += 3600  # laptop lid closed for an hour
    m.resumed()
    assert m.check() is None


def test_quit_exits_immediately_and_client_count_is_capped():
    clock = Clock()
    m = monitor(clock)
    for i in range(lifecycle.MAX_CLIENTS + 10):
        m.seen(f"tab-{i:04d}")
    assert len(m._clients) == lifecycle.MAX_CLIENTS
    m.request_exit("Quit button")
    assert m.check() == "Quit button"


def test_timeouts_can_be_shortened_for_tests(monkeypatch):
    monkeypatch.setenv("ROSTER_TOOL_STARTUP_TIMEOUT", "2")
    monkeypatch.setenv("ROSTER_TOOL_GOODBYE_GRACE", "not a number")
    m = SessionMonitor(on_exit=lambda r: None)
    assert m.startup_timeout == 2 and m.goodbye_grace == lifecycle.GOODBYE_GRACE


# --- Launcher ----------------------------------------------------------------------------

@pytest.mark.parametrize("platform_name,expected", [
    ("darwin", Path("Library") / "Logs" / "Roster Tool"),
    ("linux", Path(".local") / "state" / "roster-tool"),
])
def test_log_folder_per_platform(monkeypatch, tmp_path, platform_name, expected):
    monkeypatch.delenv("ROSTER_TOOL_LOG_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert desktop.log_dir() == tmp_path / expected


def test_log_folder_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("ROSTER_TOOL_LOG_DIR", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert desktop.log_dir() == tmp_path / "Roster Tool" / "Logs"


def test_log_is_rotating_and_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("ROSTER_TOOL_LOG_DIR", str(tmp_path))
    try:
        desktop.configure_logging(console=False)
        desktop.configure_logging(console=False)  # reconfiguring never duplicates handlers
        ours = [h for h in logging.getLogger().handlers if getattr(h, "_roster_tool", False)]
        assert len(ours) == 1 and isinstance(ours[0], logging.handlers.RotatingFileHandler)
        assert ours[0].maxBytes == desktop.LOG_MAX_BYTES and ours[0].backupCount == desktop.LOG_BACKUPS
    finally:
        for h in [h for h in logging.getLogger().handlers if getattr(h, "_roster_tool", False)]:
            logging.getLogger().removeHandler(h)
            h.close()


def test_self_check_passes_from_source():
    assert desktop.self_check() == []


def test_probe(server):
    assert desktop.probe(server.url) is True
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    assert desktop.probe(f"http://127.0.0.1:{free_port}/", timeout=1) is False


def _run_main(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("ROSTER_TOOL_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ROSTER_TOOL_NO_BROWSER", "1")
    monkeypatch.setenv("ROSTER_TOOL_AUTO_EXIT", "1")
    monkeypatch.setenv("ROSTER_TOOL_STARTUP_TIMEOUT", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    shown = []
    monkeypatch.setattr(desktop, "display_dialog", shown.append)  # no real dialogs in tests
    try:
        code = desktop.main([])
    finally:
        for h in [h for h in logging.getLogger().handlers if getattr(h, "_roster_tool", False)]:
            logging.getLogger().removeHandler(h)
            h.close()
    return code, shown, (tmp_path / desktop.LOG_FILE).read_text(encoding="utf-8")


def test_launcher_starts_serves_and_exits_cleanly(monkeypatch, tmp_path):
    code, shown, log_text = _run_main(monkeypatch, tmp_path)
    assert code == 0 and shown == []
    assert re.search(r"listening on http://127\.0\.0\.1:\d+/", log_text)
    assert "shutting down: no browser tab connected" in log_text and "stopped" in log_text


def test_launcher_reports_missing_files_instead_of_failing_silently(monkeypatch, tmp_path):
    monkeypatch.setattr(resources, "REQUIRED_FILES", resources.REQUIRED_FILES + (resources.STATIC_DIR / "gone.js",))
    code, shown, log_text = _run_main(monkeypatch, tmp_path)
    assert code == 1 and len(shown) == 1 and "gone.js" in shown[0]
    assert "problem shown to user" in log_text


# --- Build configuration and verification -----------------------------------------------

def test_spec_ships_only_runtime_resources():
    spec = (ROOT / "packaging" / "roster_tool.spec").read_text()
    assert "upx=False" in spec and "console=DEBUG" in spec and '"LSUIElement": True' in spec
    assert '"pytest"' in spec  # explicitly excluded
    sources = re.findall(r"\(str\((ROOT|PKG)[^)]*\)", spec)
    assert sources and not re.search(r"tests|private_rosters|fixtures|scripts", " ".join(sources))


def _fake_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "Roster Tool"
    for rel in build_app.REQUIRED:
        (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rel).write_text(f"stand-in for {rel}")
    engine = build_app.NATIVE_PDF_ENGINE.get(sys.platform)
    if engine:
        (bundle / "pypdfium2_raw" / engine).parent.mkdir(parents=True, exist_ok=True)
        (bundle / "pypdfium2_raw" / engine).write_bytes(b"\0engine")
    return bundle


def test_build_verification_accepts_a_clean_bundle(tmp_path):
    assert build_app.verify_bundle(_fake_bundle(tmp_path)) == []


@pytest.mark.parametrize("add,needle", [
    (lambda b: (b / "export.csv").write_text("x"), "roster-like data file"),
    (lambda b: (b / "tests").mkdir() or (b / "tests" / "x.py").write_text("x"), "forbidden folder"),
    (lambda b: (b / ".venv" / "lib").mkdir(parents=True) or (b / ".venv" / "lib" / "x").write_text("x"), "forbidden folder"),
    (lambda b: shutil.copy(ROOT / "tests" / "fixtures" / "mock_roster_sections.csv", b / "renamed.txt"), "copy of a test fixture"),
    (lambda b: (b / "roster_tool" / "static" / "index.html").unlink(), "missing required file"),
])
def test_build_verification_rejects_bad_contents(tmp_path, add, needle):
    bundle = _fake_bundle(tmp_path)
    add(bundle)
    assert any(needle in p for p in build_app.verify_bundle(bundle))


def test_build_outputs_are_git_ignored():
    for path in ("dist/Roster Tool.app/Contents/Info.plist", "build/pyinstaller/x", "dist/Roster-Tool-0.1.0-macOS-arm64.zip"):
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0, path


def test_third_party_notices_match_installed_versions():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    for dist in ("pypdfium2", "reportlab", "pillow", "openpyxl", "et_xmlfile", "pyinstaller"):
        version = importlib.metadata.version(dist)
        assert re.search(rf"{version}\b", notices), f"{dist} {version} missing from THIRD_PARTY_NOTICES.md"
