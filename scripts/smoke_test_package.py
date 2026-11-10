"""Smoke-test the PACKAGED Roster Tool (the built app, not the source tree). Fake fixtures only.

    python scripts/smoke_test_package.py                    # dist/Roster Tool.app or dist/Roster Tool/Roster Tool.exe
    python scripts/smoke_test_package.py --no-network       # macOS: block ALL non-loopback network (sandbox-exec)
    python scripts/smoke_test_package.py --launchservices   # macOS: launch like a double-click (opens your default browser)
    python scripts/smoke_test_package.py --private-roster private_rosters/roster.pdf   # + real roster, redacted output

Each launch runs the packaged executable from an empty folder with a cleaned
environment (no PYTHONPATH/PYTHONHOME/VIRTUAL_ENV; virtualenv removed from
PATH) and a private log folder, then checks the full workflow, the network
exposure, the files the process has open, and all three shutdown paths (Quit,
last tab closed, no tab ever opened). Prints counts and pass/fail only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_builder import CSV_FIXTURE, FIXTURE_DIR, SECTIONED, XLSX_FIXTURE  # noqa: E402

EXACT_HEADER = "Email,First Name,Last Name,SSO ID,Student ID"
SANDBOX_NO_NETWORK = """(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
"""
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # loopback only, never a proxy
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def executable(app: Path | None) -> Path:
    if sys.platform == "darwin":
        bundle = app or ROOT / "dist" / "Roster Tool.app"
        return bundle / "Contents" / "MacOS" / "Roster Tool"
    return app or ROOT / "dist" / "Roster Tool" / "Roster Tool.exe"


def clean_env(log_dir: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PYTHON") and k not in ("VIRTUAL_ENV", "ROSTER_TOOL_LOG_DIR")}
    # Drop every PATH entry inside the repository (any virtualenv there, e.g. .venv or .venv-macos-build).
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if not p.startswith(str(ROOT)))
    env.update(ROSTER_TOOL_LOG_DIR=str(log_dir), ROSTER_TOOL_NO_BROWSER="1", ROSTER_TOOL_AUTO_EXIT="1", **extra)
    return env


def wait_for_url(log_file: Path, after: int = 0, timeout: float = 60) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_file.exists():
            found = re.findall(r"listening on (http://127\.0\.0\.1:\d+/)", log_file.read_text(encoding="utf-8"))
            if len(found) > after:
                return found[after]
        time.sleep(0.2)
    return None


def request(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers or {})
    try:
        with OPENER.open(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


class Session:
    def __init__(self, url: str):
        self.url = url
        status, body, headers = request(url)
        self.html = body.decode("utf-8") if status == 200 else ""
        match = re.search(r'name="roster-token" content="([^"]+)"', self.html)
        self.token = match.group(1) if match else ""
        self.csp = headers.get("Content-Security-Policy", "")

    def post(self, path: str, payload=None, raw: bytes | None = None, file_type: str | None = None):
        headers = {"X-Roster-Token": self.token}
        if raw is not None:
            headers.update({"Content-Type": "application/octet-stream", "X-Roster-File-Type": file_type or ""})
            body = raw
        else:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload or {}).encode()
        status, data, _ = request(self.url.rstrip("/") + path, body, headers)
        try:
            return status, json.loads(data)
        except ValueError:
            return status, {}


def process_alive(proc: subprocess.Popen, timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        return True


def listening_addresses(pid: int) -> list[str]:
    if sys.platform == "darwin":
        out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout
        return re.findall(r"TCP (\S+) \(LISTEN\)", out)
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    return [cols[1] for cols in (line.split() for line in out.splitlines())
            if len(cols) >= 5 and cols[3] == "LISTENING" and cols[4] == str(pid)]


def remote_connections(pid: int) -> list[str]:
    if sys.platform != "darwin":
        return []
    out = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], capture_output=True, text=True).stdout
    return [m for m in re.findall(r"->(\S+)", out) if not m.startswith(("127.0.0.1:", "[::1]:"))]


def files_from_source_tree(pid: int) -> list[str]:
    if sys.platform != "darwin":
        return []
    out = subprocess.run(["lsof", "-nP", "-p", str(pid)], capture_output=True, text=True).stdout
    # Anything open inside the repository (source tree, tests, any virtualenv) other than the built app itself.
    return [line for line in out.splitlines() if str(ROOT) in line and str(ROOT / "dist") not in line]


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET; UDP connect sends nothing
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def full_workflow(session: Session, pid: int, private_roster: Path | None) -> None:
    check(bool(session.token) and "default-src 'none'" in session.csp, "UI page served with per-launch token and CSP")
    check('accept=".pdf,.csv,.xlsx"' in session.html and "__ROSTER_" not in session.html,
          "UI lists PDF/CSV/XLSX; all placeholders filled")
    rosters = {}
    for label, data, ftype in (("PDF", (FIXTURE_DIR / SECTIONED.filename).read_bytes(), "pdf"),
                               ("CSV", (FIXTURE_DIR / CSV_FIXTURE).read_bytes(), "csv"),
                               ("XLSX", (FIXTURE_DIR / XLSX_FIXTURE).read_bytes(), "xlsx")):
        status, body = session.post("/api/roster", raw=data, file_type=ftype)
        students = body.get("roster", {}).get("students", [])
        current = sum(s["status"] == "current" for s in students)
        waiting = sum(s["status"] == "waitlist" for s in students)
        check(status == 200 and current == 14 and waiting == 3 and body.get("issues") == [],
              f"{label} fake fixture -> 14 current + 3 wait list, no issues")
        rosters[label] = body.get("roster")
    status, body = session.post("/api/help-sheet", {"roster": rosters["XLSX"], "tutor_name": "Smoke Test",
                                                     "days": ["Tuesday", "Thursday"]})
    pdf = base64.b64decode(body.get("pdf_base64", "")) if status == 200 else b""
    check(pdf.startswith(b"%PDF") and body.get("page_count") == 1, "Help Sheet PDF generated (1 page, Tue/Thu)")
    status, body = session.post("/api/csv", {"roster": rosters["CSV"]})
    lines = body.get("csv", "").split("\r\n")
    check(status == 200 and lines[0] == EXACT_HEADER and len([ln for ln in lines if ln]) == 18,
          "Event CSV generated: exact header + 17 student rows")
    status, _ = session.post("/api/roster", raw=(FIXTURE_DIR / CSV_FIXTURE).read_bytes(), file_type="pdf")
    check(status == 422, "mismatched file type rejected (CSV named .pdf)")

    addrs = listening_addresses(pid)
    check(bool(addrs) and all(a.startswith("127.0.0.1:") for a in addrs), f"listens on 127.0.0.1 only ({addrs})")
    ip = lan_ip()
    if ip:
        port = int(session.url.rsplit(":", 1)[1].strip("/"))
        try:
            socket.create_connection((ip, port), timeout=2).close()
            reachable = True
        except OSError:
            reachable = False
        check(not reachable, "not reachable from the network (LAN address refused)")
    check(remote_connections(pid) == [], "no outbound network connections")
    check(files_from_source_tree(pid) == [], "nothing loaded from the source tree or .venv")

    if private_roster:
        from roster_tool.pipeline import process_roster
        data = private_roster.read_bytes()
        status, body = session.post("/api/roster", raw=data, file_type=private_roster.suffix.lstrip("."))
        packaged = hashlib.sha256(json.dumps(body.get("roster"), sort_keys=True).encode()).hexdigest()
        source = hashlib.sha256(json.dumps(process_roster(data, private_roster.suffix).roster.to_dict(),
                                           sort_keys=True).encode()).hexdigest()
        students = body.get("roster", {}).get("students", [])
        errors = sum(i["severity"] == "error" for i in body.get("issues", []))
        check(status == 200 and packaged == source,
              f"real roster: packaged result identical to source result "
              f"({sum(s['status'] == 'current' for s in students)} current, "
              f"{sum(s['status'] == 'waitlist' for s in students)} wait list, {errors} errors)")


def launch(exe: Path, work: Path, log_dir: Path, sandbox: bool, **extra) -> subprocess.Popen:
    cmd = [str(exe)]
    if sandbox:
        cmd = ["/usr/bin/sandbox-exec", "-p", SANDBOX_NO_NETWORK] + cmd
    return subprocess.Popen(cmd, cwd=work, env=clean_env(log_dir, **extra),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, help="path to the .app (macOS) or .exe (Windows)")
    parser.add_argument("--no-network", action="store_true", help="macOS: block all non-loopback network")
    parser.add_argument("--launchservices", action="store_true", help="macOS: also launch like a double-click")
    parser.add_argument("--private-roster", type=Path, help="real roster to compare (redacted output)")
    args = parser.parse_args()
    exe = executable(args.app)
    if not exe.exists():
        print(f"Packaged app not found: {exe}. Build it first: python scripts/build_app.py")
        return 2
    sandbox = args.no_network and sys.platform == "darwin"

    with tempfile.TemporaryDirectory() as tmp:
        work, log_dir = Path(tmp) / "work", Path(tmp) / "logs"
        work.mkdir()
        log_file = log_dir / "roster-tool.log"
        print(f"Packaged app: {exe}" + ("  [network blocked except loopback]" if sandbox else ""))

        print("\nLaunch 1: full workflow, then Quit")
        t0 = time.time()
        proc = launch(exe, work, log_dir, sandbox)
        url = wait_for_url(log_file)
        check(url is not None, f"started and serving in {time.time() - t0:.1f} s")
        if url:
            session = Session(url)
            full_workflow(session, proc.pid, args.private_roster)
            session.post("/api/shutdown", {})
        check(not process_alive(proc, 15) and proc.returncode == 0, "Quit: process exited cleanly (code 0)")

        print("\nLaunch 2: relaunch; browser tab opens then closes")
        proc = launch(exe, work, log_dir, sandbox, ROSTER_TOOL_GOODBYE_GRACE="2")
        url = wait_for_url(log_file, after=1)
        check(url is not None, "relaunched")
        if url:
            session = Session(url)
            client = "smoke-test-tab-1"
            status, _ = session.post("/api/heartbeat", {"client": client})
            check(status == 200, "tab heartbeat accepted")
            check(process_alive(proc, 4), "keeps running while a tab is open")
            session.post("/api/goodbye", {"client": client})
        check(not process_alive(proc, 15) and proc.returncode == 0, "last tab closed: process exited by itself")

        print("\nLaunch 3: no browser tab ever connects")
        proc = launch(exe, work, log_dir, sandbox, ROSTER_TOOL_STARTUP_TIMEOUT="3")
        check(wait_for_url(log_file, after=2) is not None, "started")
        check(not process_alive(proc, 20) and proc.returncode == 0, "no tab within the startup timeout: exited")

        log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
        leaked = [r.last for r in SECTIONED.current + SECTIONED.waitlist if r.last in log_text]
        leaked += [r.w_number[1:] for r in SECTIONED.current if r.w_number[1:] in log_text]
        check(log_text != "" and not leaked, "diagnostic log has no student names or IDs")

    if args.launchservices and sys.platform == "darwin":
        print("\nLaunch 4: like a double-click (LaunchServices `open`; your default browser opens)")
        default_log = Path.home() / "Library" / "Logs" / "Roster Tool" / "roster-tool.log"
        before = len(re.findall(r"listening on", default_log.read_text(encoding="utf-8"))) if default_log.exists() else 0
        subprocess.run(["open", str(exe.parents[2])], check=True)
        url = wait_for_url(default_log, after=before)
        check(url is not None, "started via LaunchServices")
        deadline = time.time() + 30
        while url and time.time() < deadline and "browser tab connected" not in default_log.read_text(encoding="utf-8").split(url)[-1]:
            time.sleep(0.5)
        connected = bool(url) and "browser tab connected" in default_log.read_text(encoding="utf-8").split(url)[-1]
        check(connected, "default browser opened the UI and the tab connected")
        if url:
            Session(url).post("/api/shutdown", {})
            time.sleep(3)

    time.sleep(1)
    if sys.platform == "darwin":
        left = subprocess.run(["pgrep", "-f", "Roster Tool.app/Contents/MacOS/Roster Tool"], capture_output=True, text=True).stdout.split()
    else:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Roster Tool.exe"], capture_output=True, text=True).stdout
        left = [line for line in out.splitlines() if "Roster Tool.exe" in line]
    check(left == [], "no Roster Tool processes left running")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (": SMOKE TEST FAILED" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
