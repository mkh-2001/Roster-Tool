# Packaging Roster Tool

Staff double-click **Roster Tool**, and it opens in their default web browser.
They upload a roster (PDF, CSV or XLSX), create a Help Sheet or Event CSV, then
close the tab. They need no Python, Terminal, URL or internet connection.

| Platform | Status |
|---|---|
| **macOS (Apple Silicon)** | **Built and validated:** `Roster Tool.app` (arm64), built with the official python.org Python 3.13.15. **Requires macOS 13.0 or later**, measured from the bundled binaries (see below); so far it has been run on macOS 26.6. The automated smoke test passes, including with all non-loopback network access blocked, a normal macOS launch and a real-browser tab-close test. |
| **Windows (x64)** | **Built and validated:** `Roster Tool\Roster Tool.exe` (one folder), built on Windows 11 Home 10.0.26200 with the python.org Python 3.12.2. **Requires Windows 10 or later**, measured from the bundled binaries (PDFium sets the floor); only Windows 11 has been tested. The automated smoke test passes (23/23 with a real roster), as do a double-click launch, a real-browser tab-close test and a loopback-only check. Outstanding: the offline (network-disconnected) test. Unsigned. Follow [WINDOWS_BUILD.md](WINDOWS_BUILD.md). |

## How the packaged app works

* **Tool:** PyInstaller 6.22.3, one-folder mode. It bundles the Python
  runtime, `pypdfium2` and its native PDFium library, ReportLab, Pillow,
  openpyxl, the UI files and fonts, and the license notices. Nothing is
  unpacked to a temporary folder at launch.
* **Launch** (`roster_tool/desktop.py`, the same code as `python -m roster_tool`):
  1. A privacy-safe log starts.
  2. Self-checks run: required files, PDF engine, fonts, XLSX reader.
  3. The server binds to **127.0.0.1** on a random free port with a per-launch token.
  4. The launcher checks that the server answers.
  5. Only then is the default browser opened, so there is no race.

  Typical time from launch to a ready server is about 0.1 s.
* **One process.** The server, the launcher and the lifecycle monitor are a
  single process with no child processes. When it stops, nothing can be left behind.
* **Same security model as from source:**
  * loopback only, never `0.0.0.0`;
  * a token on every API call;
  * Host, Origin and Sec-Fetch-Site checks;
  * a strict Content Security Policy;
  * no outbound connections, telemetry or debug server.

### When the app stops

| Situation | What happens |
|---|---|
| Employee closes the Roster Tool tab (or the whole browser) | The tab sends "goodbye", and the app exits **about 10 s** later. A page reload within that time keeps it running. |
| Employee clicks **Quit Roster Tool** | It exits immediately. The tab shows "Roster Tool has closed." |
| Browser crashes, or the tab is discarded or frozen | The missing heartbeats are noticed, and the app exits within about **5 minutes**. |
| The browser never opened the page | The app exits after **3 minutes**. |
| Startup fails (missing file, PDF engine, port, page not answering) | A native error dialog explains the problem and shows the log location. The app exits. |
| Browser cannot be opened automatically | A dialog shows the local address to open manually. The app keeps running. |
| Computer sleeps | The app keeps running. Sleep is not mistaken for a closed tab. |
| Logout or shutdown | The operating system ends the single process, so nothing is orphaned. |

On macOS the app has **no Dock icon** (`LSUIElement`); its window is the
browser tab. If Roster Tool is already running, double-clicking it again
**does not open a second tab**. macOS keeps a single instance and simply
activates it. Switch to the open Roster Tool tab, or close it and wait a few
seconds before launching again.

### Diagnostic log (no roster data)

The log contains startup steps, timings, requests (method, route, status),
tab connect/close events and errors. It never contains names, W numbers,
roster contents, CSV rows or workbook cells. It is rotated at 256 KB, with at
most 3 files (about 768 KB in total).

| Platform | Location |
|---|---|
| macOS | `~/Library/Logs/Roster Tool/roster-tool.log` |
| Windows | `%LOCALAPPDATA%\Roster Tool\Logs\roster-tool.log` |

To watch the log live on macOS, run
`"/Applications/Roster Tool.app/Contents/MacOS/Roster Tool"` from Terminal.
On Windows, use the debug build (see WINDOWS_BUILD.md).

**Temporary files:** none. Rosters are processed in memory.
**Resource lookup:** done in one place, `roster_tool/resources.py`, which works
both from source and inside the frozen app.

## Building (macOS)

**Use the official python.org Python**, not pyenv or Homebrew. Its runtime is
compiled for macOS 11, which keeps the app's minimum at macOS 13. A pyenv
Python compiled on a recent Mac raises the minimum to that Mac's macOS version;
an earlier pyenv build required macOS 26.6. Install the python.org
"macOS 64-bit universal2" installer for Python 3.13. Only the framework is
needed, and the build script checks and records the resulting minimum anyway.

```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m venv .venv-macos-build
.venv-macos-build/bin/pip install -r requirements-build.txt -r requirements-dev.txt
.venv-macos-build/bin/python -m pytest                      # source tests first
.venv-macos-build/bin/python scripts/build_app.py           # build + verify
.venv-macos-build/bin/python scripts/smoke_test_package.py --no-network --launchservices
```

`requirements.txt` and `requirements-build.txt` pin **exact** versions,
including transitive ones, so every build environment matches the validated set.

| Output | Size |
|---|---|
| `dist/Roster Tool.app` | about 42 MB, 209 files |
| `dist/Roster-Tool-<version>-macOS-arm64.zip` (for distribution; made with `ditto`, so symlinks and permissions survive) | about 20 MB |

`build/` and `dist/` are git-ignored. Only the packaging *source* is committed.

**The build is rejected automatically** if the package:
* is missing a required runtime file, the PDF engine or a license notice;
* contains anything from `private_rosters/` or `tests/`, a `.venv`, git
  metadata, pytest or scripts;
* contains any PDF, CSV or spreadsheet file;
* contains any file byte-identical to a test fixture or private roster
  (checked by SHA-256, never printed);
* on macOS, has an Info.plist minimum system version lower than its binaries need.

**Debug build:** `scripts/build_app.py --debug` makes "Roster Tool Debug". It
has a console window on Windows, shows the live log, and has its own bundle ID.

**Smoke test** (`scripts/smoke_test_package.py`, fake fixtures only). It runs
the *packaged* executable from an empty folder with a cleaned environment (no
virtualenv, no `PYTHON*` variables) and checks:
* startup time and the token/CSP page;
* PDF, CSV and XLSX uploads (14 current + 3 waitlisted each);
* the Help Sheet, and the Event CSV header and rows;
* mismatched-type rejection;
* listening on 127.0.0.1 only, and not reachable via the LAN address;
* no outbound connections, and no files loaded from the source tree or `.venv`;
* Quit, last-tab-closed and never-opened shutdown;
* relaunch;
* no student data in the log, and no leftover processes.

Options:
* `--no-network`: blocks all non-loopback network access (macOS `sandbox-exec`).
* `--launchservices`: also launches the app like a double-click (your default browser opens).
* `--private-roster <file>`: compares a real roster's packaged result with the
  source result. The output is redacted.

## macOS limitations and deployment (for IT)

* **Signing:** the app is **ad-hoc signed only**. PyInstaller signs it
  automatically, which Apple Silicon requires. It is **not** signed with an
  Apple Developer ID and **not notarized**.
  * On the build Mac it opens normally.
  * Copied to another Mac by download, AirDrop, email or a USB drive, it gets
    the quarantine flag. Gatekeeper then blocks it: "Apple could not verify
    'Roster Tool' is free of malware…".
  * **Staff should not be told to bypass this.** Do not tell them to use
    "Open Anyway" or to remove the quarantine flag.
* **For production deployment, IT needs one of:**
  1. **Developer ID signing plus notarization.** This requires an Apple
     Developer Program membership, usually the college's. The steps are:
     * Sign every binary with the Developer ID Application certificate and the
       hardened runtime (`codesign --deep --options runtime --timestamp`).
     * Submit the app to Apple (`xcrun notarytool submit … --wait`).
     * Staple the ticket (`xcrun stapler staple "Roster Tool.app"`).

     The app needs no special entitlements: it uses no camera, microphone,
     contacts or network server outside loopback. Change `BUNDLE_ID` in the
     spec to the college's reverse-DNS identifier first.
  2. **MDM deployment** (for example Jamf or Intune for Mac), signed with an
     internal or Developer ID certificate that IT trusts on managed Macs.
* **Minimum macOS version: 13.0 (Ventura).** This was established by reading the
  minimum-OS field of every native binary in the built app (76 files, all arm64):

  | Component | Requires |
  |---|---|
  | `libpdfium` (PDF engine, from pypdfium2 5.15.2) | **macOS 13.0** (the limiting component) |
  | Python runtime library (python.org 3.13.15) | macOS 11.0 |
  | Application executable (PyInstaller bootloader) | macOS 11.0 |
  | OpenSSL, 51 standard-library extensions, Pillow and its 18 image libraries, charset-normalizer | macOS 11.0 |

  No binary links to anything outside the app other than macOS system
  libraries. Info.plist declares `LSMinimumSystemVersion` 13.0, so older Macs
  get a clear "requires macOS 13.0 or later" message. **The app has so far been
  run only on macOS 26.6.** Before a broad rollout, run the smoke test on the
  oldest supported version (macOS 13) on a test Mac or virtual machine.
* **Architecture:** Apple Silicon (arm64) only, built natively. Intel Macs
  need a build made on an Intel Mac, or a universal2 Python with universal
  wheels. Universal wheels are not currently available for every dependency.
* **Firewall:** the app listens on 127.0.0.1 only, so it triggers no macOS
  firewall prompt.

## Offline acceptance test (manual, about 5 minutes)

The automated smoke test blocks the network with a sandbox. This manual test
confirms the app also works with the network genuinely switched off. It uses
fake fixtures only.

1. Make sure Roster Tool is not running (Activity Monitor shows no "Roster Tool").
2. **Turn Wi-Fi off** (Control Center → Wi-Fi) and unplug any Ethernet cable.
   Opening any website in your browser should now fail.
3. *(Optional, automated)* In Terminal, from the project folder, run
   `.venv-macos-build/bin/python scripts/smoke_test_package.py --launchservices`.
   It should end with "24/24 checks passed".
4. In Finder, **double-click `dist/Roster Tool.app`**. A Roster Tool tab opens in
   your default browser.
5. Upload `tests/fixtures/mock_roster_sections.pdf`. Expect Students found **14**,
   "+ 3 on the wait list", and no problems listed.
6. Repeat with `mock_roster_sections.csv` and `mock_roster_sections.xlsx`.
   The results should be the same.
7. **Help Sheet:** enter a tutor name, tick Tue and Thu, then click Generate.
   Expect 1 page with a Class / Professor / Tutor / Meetings Location / Term
   header (the Meetings Location field is prefilled and editable), and Current
   Students plus a separate Wait List for each day.
   Download PDF should work.
8. **Event CSV** tab: expect 17 rows and the header
   `Email,First Name,Last Name,SSO ID,Student ID`. Download CSV should work.
9. Click **Quit Roster Tool**. The tab shows "Roster Tool has closed", and
   Activity Monitor shows no "Roster Tool" process.
10. Turn Wi-Fi back on. Record the result (pass/fail per step).

## Windows

See [WINDOWS_BUILD.md](WINDOWS_BUILD.md) for the validated procedure and its
measured results. In summary: build on Windows with the same
`scripts/build_app.py`. The output is a `dist\Roster Tool\` folder with
`Roster Tool.exe` (46 MB, 164 files), which has no console window and carries
Windows version metadata. It binds only to `127.0.0.1`, creates no firewall
rule and raised nothing in Microsoft Defender. Expect SmartScreen scrutiny of a
*downloaded* copy, because the exe is unsigned — locally built copies carry no
Mark-of-the-Web and are not checked. The remedy is Authenticode code signing
and IT allowlisting, never evasion.

## Possible future enhancements (not done)

* **A native window instead of a browser tab** (for example pywebview). It
  would give a Dock/taskbar presence and a single-instance "reopen" on macOS.
  It is not needed now: the browser tab works offline and keeps the app small.
* **An app icon.**
* **A local OCR adapter for scanned PDFs.** This is out of scope. The input
  layer already allows adding one without touching the rest of the app.

## Licensing

The bundled components and their licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), which ships inside the app
together with each component's license texts. ReportLab's unused demo fonts
(some GPL-licensed) are removed from the package. PyInstaller's bootloader is
GPL with an exception that allows distributing the built app.
