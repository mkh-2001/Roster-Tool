# Building Roster Tool on Windows

> **Status: prepared, NOT yet validated on Windows.** The spec
> (`packaging/roster_tool.spec`), the build script and the smoke test all
> support Windows. However, no Windows build has been made or run so far.
> PyInstaller cannot cross-compile, so this must be done on a Windows PC (or a
> Windows CI runner). Do not distribute a Windows build until this procedure
> has passed.

## 1. Requirements

* **Windows 10 or 11, 64-bit (x64).** Build on the oldest Windows version you
  need to support, or the same version.
* **Python 3.13 (64-bit)** from python.org. During installation, tick
  "Add python.exe to PATH". Avoid the Microsoft Store Python for builds.
* **Git**, to check out the repository.
* **Microsoft Edge or another browser** set as the default browser, for testing.

## 2. Set up the build environment

In **Command Prompt**, inside the repository folder:

```bat
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

All tests must pass: 296 passed, 1 skipped at the time of writing.

## 3. Build

```bat
.venv\Scripts\python scripts\build_app.py
```

| Output | Contents |
|---|---|
| `dist\Roster Tool\` | `Roster Tool.exe` plus its `_internal\` folder. Distribute the **whole folder**. |
| `dist\Roster-Tool-<version>-Windows-x64.zip` | The same folder, zipped |

The exe has **no console window** (`console=False`) and carries Windows
version metadata (Properties → Details → File/Product version). The script
verifies the package and rejects it if it contains private rosters, test
fixtures, data files, a `.venv`, git metadata or development tools, or if it
lacks required files, `pdfium.dll` or license notices.

Debug build (with a console window that shows the live log):

```bat
.venv\Scripts\python scripts\build_app.py --debug
```

This produces `dist\Roster Tool Debug\Roster Tool Debug.exe`. Use it only for troubleshooting.

## 4. Test

1. **Automated smoke test** (fake fixtures):
   ```bat
   .venv\Scripts\python scripts\smoke_test_package.py
   ```
   Expect all checks to pass. On Windows, listening sockets are checked with
   `netstat`; the open-files and outbound-connection checks run on macOS only
   (see step 4).
2. **Double-click test.** Copy `dist\Roster Tool\` somewhere outside the
   repository, for example the Desktop, then double-click `Roster Tool.exe`:
   * The default browser opens Roster Tool within a second or two.
   * **No console or black window appears.** Check the taskbar too.
   * Upload a PDF, a CSV and an XLSX fixture from `tests\fixtures\`. Each shows
     14 current students and 3 on the wait list.
   * Generate a Help Sheet (Print and Download PDF) and an Event CSV (Download).
     The CSV header must be exactly `Email,First Name,Last Name,SSO ID,Student ID`.
   * Close the tab. Within about 10 s, `Roster Tool.exe` must disappear from
     **Task Manager → Details**.
   * Launch again and use **Quit Roster Tool**. The process must exit immediately.
3. **Localhost only.** While the app is running:
   ```bat
   netstat -ano -p TCP | findstr LISTENING
   ```
   Roster Tool's PID must listen on `127.0.0.1:<port>` only, never `0.0.0.0`.
   Windows Defender Firewall should **not** prompt, because loopback-only
   servers do not trigger the prompt.
4. **No network dependency.** Disconnect Wi-Fi and Ethernet, then repeat step 2.
   Everything must still work. Optionally, watch **Resource Monitor → Network**:
   Roster Tool should show no remote connections, only 127.0.0.1.
5. **Clean-machine test (recommended).** Repeat step 2 on a PC or virtual
   machine **without Python installed**. Windows Sandbox works well for this.
6. **Diagnostic log.** `%LOCALAPPDATA%\Roster Tool\Logs\roster-tool.log` should
   contain startup and request lines only, with no student names or IDs.

Record the results (Windows version, pass/fail per step). Only after that is
the Windows build "validated".

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Nothing happens on double-click | Run the debug build to see the live log. Also check `%LOCALAPPDATA%\Roster Tool\Logs\roster-tool.log`. |
| Error dialog "PDF engine could not be loaded" | `pdfium.dll` is missing, or was blocked or quarantined by antivirus. Check `_internal\pypdfium2_raw\`. |
| SmartScreen: "Windows protected your PC" | The exe is unsigned (see the next section). Do not tell staff to click through; sign it or have IT allowlist it. |
| Antivirus quarantines the exe | A known false-positive pattern for frozen Python apps, made less likely here by not using UPX. Submit the file to the vendor, sign it, or ask IT to allowlist it. Never try to evade detection. |
| The browser opens but the page doesn't load | Another security product may block localhost. Check the log for "listening on". |
| Firewall prompt appears | It should not, because the app binds only to 127.0.0.1. Report this; it would indicate a configuration change. |

## 6. Windows security and IT deployment

* **The build is unsigned.** Expect a SmartScreen warning on first run of a
  downloaded copy, and possible antivirus scrutiny. One-folder builds without
  UPX reduce, but do not eliminate, false positives.
* **Code signing (recommended for production).** Sign `Roster Tool.exe` with
  the college's Authenticode certificate, ideally an EV or organization
  certificate kept in hardware or a cloud HSM:
  ```bat
  signtool sign /fd SHA256 /tr <timestamp-url> /td SHA256 /f <cert> "dist\Roster Tool\Roster Tool.exe"
  ```
  Re-zip after signing. Signed executables build SmartScreen reputation and
  are easier for endpoint protection to trust.
* **IT allowlisting and managed deployment.** IT can allowlist by certificate
  or file hash, and deploy the folder with Intune, SCCM or Configuration
  Manager, or another endpoint-management tool. Deploy it to a per-machine
  location (for example `C:\Program Files\Roster Tool\`) with a Start-menu
  shortcut to `Roster Tool.exe`. The app writes only to its log folder under
  `%LOCALAPPDATA%`; it needs no admin rights to run and no registry entries.
* **No installer is required.** An MSI or MSIX wrapper can be added if IT
  prefers one.
