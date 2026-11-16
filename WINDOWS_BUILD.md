# Building Roster Tool on Windows

> **Status: built and validated on Windows 11 x64** (16 November 2026) with the
> procedure below. One item is still outstanding: the **offline test (step 4.4)
> was not performed**, so the app's independence from the network is supported
> by the loopback/outbound evidence in step 4.3 rather than by a real
> disconnection test. PyInstaller cannot cross-compile: the Windows package
> must be built on Windows, never derived from the macOS build.
>
> **Rebuild pending (next deployment step):** the validated Windows build, and
> its root-level `Roster Tool - Windows\` copy, predate the
> `chore: final v0.1.0 cleanup` commit. That commit changed three UI messages to
> format-neutral wording, listed charset-normalizer in THIRD_PARTY_NOTICES.md
> and removed unused code, with no behavior change. **Rebuild from that commit
> on the Windows x64 PC** (steps 2–3a; never from the Mac), then re-run
> section 4.

## Reference build actually used

| | |
|---|---|
| Windows | Windows 11 Home, 10.0.26200, 64-bit |
| Architecture | x64 (AMD64) |
| Python | 3.12.2 (64-bit), python.org installer |
| PyInstaller | 6.22.3 (hooks-contrib 2026.9, pefile 2024.8.26, pywin32-ctypes 0.2.3) |
| Package | PyInstaller one-folder: `dist\Roster Tool\` + `Roster-Tool-0.1.0-Windows-x64.zip` |
| Size | 46.3 MB unpacked, 164 files; 23.4 MB zipped |
| Signing | Unsigned (`Get-AuthenticodeSignature` → `NotSigned`) |

**Minimum supported Windows: 10 (x64).** This is measured, not assumed: the
Windows loader refuses an image whose `MajorSubsystemVersion` is newer than the
running OS, and the highest value among the 71 packaged PE binaries is **10.0**,
set by `_internal\pypdfium2_raw\pdfium.dll` (the PDF engine) and by the 39
bundled `api-ms-win-*` UCRT forwarders. `Roster Tool.exe`, `python312.dll` and
`VCRUNTIME140.dll` are only 6.0, so Python and PyInstaller are not the
constraint — PDFium is, and the app cannot read PDF rosters without it.
Independently, `python312.dll` has a load-time import of `PathCchCombineEx` /
`PathCchSkipRoot` from `api-ms-win-core-path-l1-1-0.dll`, which the package does
not ship. **Windows 10 is therefore the technical floor; only Windows 11 was
actually tested.** Do not claim Windows 10 support until it is tested there.

## 1. Requirements

* **Windows 10 or 11, 64-bit (x64).** Build on the oldest Windows version you
  need to support, or the same version.
* **Python 3.12 or 3.13 (64-bit)** from python.org. During installation, tick
  "Add python.exe to PATH". Avoid the Microsoft Store Python for builds.
  3.12.2 is the validated Windows version; the macOS reference build uses 3.13.
* **Git**, to check out the repository.
* **Microsoft Edge, Chrome or another browser** set as the default browser.

## 2. Set up the build environment

In **Command Prompt**, inside the repository folder:

```bat
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

All tests must pass: **309 passed, 1 skipped**. The skip is the optional
real-roster test, which runs only on a machine that has both a roster and a
hand-written `private_rosters\expected.json`; where those exist it is
**310 passed**, with nothing skipped.

> **PowerShell note.** `.venv\Scripts\Activate.ps1` may be blocked by the
> execution policy. There is no need to change the policy or to activate the
> environment: call `.\.venv\Scripts\python.exe` directly, as shown here.

## 3. Build

```bat
.venv\Scripts\python scripts\build_app.py
```

| Output | Contents |
|---|---|
| `dist\Roster Tool\` | `Roster Tool.exe` plus its `_internal\` folder. Distribute the **whole folder**. |
| `dist\Roster-Tool-<version>-Windows-x64.zip` | The same folder, zipped |

The exe has **no console window** (`console=False`) and carries Windows version
metadata (Properties → Details → File/Product version `0.1.0`). The script
verifies the package and rejects it if it contains private rosters, test
fixtures, data files, a `.venv`, git metadata or development tools, or if it
lacks required files, `pdfium.dll` or license notices.

The build warns if the working tree is dirty; release builds should come from a
commit.

Debug build (with a console window that shows the live log):

```bat
.venv\Scripts\python scripts\build_app.py --debug
```

This produces `dist\Roster Tool Debug\Roster Tool Debug.exe`. Use it only for
troubleshooting — it is the supported way to see startup output, since the
production build deliberately has no console.

### 3a. Root-level convenience copy

For discoverability, the project root also carries a copy of the finished
distribution, so a nontechnical employee opening the project folder sees the
app directly instead of having to find it under `dist\`:

```
Roster Builder\
└── Roster Tool - Windows\
      ├── Roster Tool.exe
      └── _internal\
```

Make it with:

```bat
xcopy /E /I "dist\Roster Tool" "Roster Tool - Windows"
```

**The whole folder is the artifact — `Roster Tool.exe` cannot be moved out of
it by itself.** It is only 5–6 MB; the real runtime (Python, every compiled
extension, PDFium, fonts, the UI) is the ~40 MB `_internal\` folder beside it,
and PyInstaller's one-folder mode resolves everything relative to the exe's
own directory. Proof: copying just the `.exe` alone into an empty folder and
launching it exits immediately (code -1) with no window at all. Always copy
or move `Roster Tool - Windows\` as a whole.

No shortcut is used for this copy, deliberately: a Windows `.lnk`'s target is
an absolute path, which breaks silently if the project (or just this folder)
is copied to another computer or a different drive. The folder itself has no
such dependency — it is exactly the same validated one-folder build, just
placed somewhere more visible, and works unchanged wherever it ends up.

This root-level copy is git-ignored (`/Roster Tool - Windows/` in
`.gitignore`) — it is build output, not source, and will not exist in a
fresh checkout until someone builds and copies it up from `dist\`.

(macOS has the equivalent root-level `Roster Tool.app` copy; see
[PACKAGING.md](PACKAGING.md) — a `.app` bundle is a single relocatable unit,
so unlike the Windows folder there is nothing it needs kept beside it.
**On a Mac only:** a `.app` that sits unpacked on a Windows disk, or goes
through a ZIP made or extracted on Windows, loses its symlinks and executable
bits and will not launch when it comes back. Do not carry the macOS app
through Windows as a folder. Move the Mac-made
`Roster-Tool-<version>-macOS-arm64.zip` as a single file instead; see
"Moving the Mac app" in PACKAGING.md.)

## 4. Test

### 4.1 Automated smoke test (fake fixtures)

```bat
.venv\Scripts\python scripts\smoke_test_package.py
```

Expect **22/22 checks passed** (23/23 with `--private-roster`). It launches the
packaged exe three times from an empty folder with a cleaned environment (no
`PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV`, the repository removed from `PATH`) and
covers the full workflow, the listening address, LAN refusal, outbound
connections, loaded modules, and all three shutdown paths.

On Windows the checks use `netstat` for listening and outbound sockets,
`tasklist` for leftover processes, and the psapi module list to prove nothing
was loaded from the source tree or `.venv` (the check reports how many modules
it inspected, so it cannot pass silently on a platform that cannot look).

To compare a real roster privately — counts only, no names or IDs printed:

```bat
.venv\Scripts\python scripts\smoke_test_package.py --private-roster "private_rosters\<file>.pdf"
```

To test the root-level convenience copy instead of the `dist\` build, point
`--app` at it (this was run and passed 23/23, identically to `dist\`):

```bat
.venv\Scripts\python scripts\smoke_test_package.py --app "Roster Tool - Windows\Roster Tool.exe" --private-roster "private_rosters\<file>.pdf"
```

The "nothing loaded from the source tree or `.venv`" check understands this:
it exempts whichever folder actually contains the exe being tested (passed
through as `bundle_root`), not a hardcoded `dist\` path — so testing a copy
that lives inside the repository root does not falsely flag the copy's own
`_internal\` files as "from source". (It also resolves a relative `--app`
path to absolute before comparing, which matters on Windows since loaded
module paths from `psapi` come back absolute.)

### 4.2 Double-click test

Copy `dist\Roster Tool\` somewhere outside the repository (the Desktop is
fine), then double-click `Roster Tool.exe`:

* The default browser opens Roster Tool within a second or two
  (measured: serving in 0.5–1.0 s, tab connected ~1.5 s).
* **No console or black window appears.** Verify with
  `(Get-Process 'Roster Tool').MainWindowHandle` → `0`, and no `conhost.exe`
  child process.
* Upload a PDF, a CSV and an XLSX fixture from `tests\fixtures\`. Each shows
  14 current students and 3 on the wait list, with no issues.
* Generate a Help Sheet (Print and Download PDF) and an Event CSV (Download).
  The CSV header must be exactly `Email,First Name,Last Name,SSO ID,Student ID`
  with 17 data rows. The Help Sheet requires a tutor name; without one the
  request is refused ("Enter the tutor's name.").
* Close the tab. `Roster Tool.exe` disappears from **Task Manager → Details**
  about 10 s later (the goodbye grace period). Chrome's `pagehide` beacon is
  what triggers this, and it was confirmed working on Windows.
* Launch again and use **Quit Roster Tool**. The process exits immediately.

This full sequence (double-click, real-browser tab close, relaunch, Quit) was
also run directly against the root-level `Roster Tool - Windows\Roster Tool.exe`
with identical results — no copy to the Desktop needed for that check, since
the root-level folder is already a complete, independent copy of the build.

### 4.3 Localhost only

While the app is running:

```bat
netstat -ano -p TCP | findstr LISTENING
```

Roster Tool's PID must listen on `127.0.0.1:<port>` only, never `0.0.0.0`.
Measured: a single endpoint, `127.0.0.1:<port> LISTENING`, and **zero
non-loopback endpoints** of any state. A connection to the machine's own LAN
address on that port is refused (`ConnectionRefusedError`).

**Windows Defender Firewall does not prompt**, and no firewall rule is created
for the app (`Get-NetFirewallApplicationFilter` matches nothing) — a
loopback-only listener never triggers the prompt. If a prompt ever appears,
treat it as a configuration regression and investigate; do not tell staff to
grant network access.

### 4.4 No network dependency — NOT YET DONE

Disconnect Wi-Fi and Ethernet, then repeat 4.2 in full. Everything must still
work, because loopback is unaffected by the adapter being down.

**This was not performed on the reference build.** What is known instead: the
app opens no outbound sockets (4.3), and `test_no_outbound_network_during_full_workflow`
runs extraction, parsing, Event CSV and Help Sheet generation with every
non-loopback connection blocked and passes. That is strong evidence, but it is
not the same as a real disconnection test — treat 4.4 as outstanding.

### 4.5 Clean-machine test

Partly done: the app was run from `%USERPROFILE%\Desktop\` with no virtualenv
and no repository on `PATH`, and the psapi module list confirmed **0 of 73
loaded modules came from the repository or `.venv`** — so it does not depend on
the source tree or on a global Python. Still recommended before wide
deployment: run it on a PC **without Python installed at all**.

### 4.6 Diagnostic log

`%LOCALAPPDATA%\Roster Tool\Logs\roster-tool.log` contains startup and request
lines only — method, route, status and duration — with no student names or IDs.
The smoke test asserts this. The log is size-capped (max ~768 KB in total).

## 5. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Nothing happens on double-click | Run the debug build to see the live log. Also check `%LOCALAPPDATA%\Roster Tool\Logs\roster-tool.log`. |
| Error dialog "PDF engine could not be loaded" | `pdfium.dll` is missing, or was blocked or quarantined by antivirus. Check `_internal\pypdfium2_raw\`. |
| App refuses to start on an older Windows | Windows 10 is the floor (see above); `pdfium.dll` will not load on 8.1 or earlier. |
| Extracting the zip fails on a long path | The deepest file in the package is 95 characters below the app folder (a PDFium build-license text). Extract somewhere short, such as the Desktop or `C:\Program Files\`, or enable Win32 long paths. |
| SmartScreen: "Windows protected your PC" | The exe is unsigned (see the next section). Do not tell staff to click through; sign it or have IT allowlist it. |
| Antivirus quarantines the exe | A known false-positive pattern for frozen Python apps, made less likely here by not using UPX. Submit the file to the vendor, sign it, or ask IT to allowlist it. Never try to evade detection. |
| The browser opens but the page doesn't load | Another security product may block localhost. Check the log for "listening on". |
| Firewall prompt appears | It should not, because the app binds only to 127.0.0.1. Report this; it would indicate a configuration change. |

## 6. Windows security and IT deployment

* **Microsoft Defender raised nothing.** With real-time protection on and
  current signatures, the build and five launches produced no detection and no
  quarantine (`Get-MpThreatDetection` → none).
* **SmartScreen was not triggered locally, and that is expected.** Files built
  on the machine carry no Mark-of-the-Web, so Explorer does not check them.
  A copy that staff **download** (email, web, file share) does get a
  Mark-of-the-Web, and because the exe is unsigned it will show
  "Windows protected your PC" until it earns reputation or is signed. That
  download case was **not** tested here — assume the warning will appear.
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

## 7. Cleanup

`dist\` and `build\` are git-ignored and can be deleted freely; rebuild with
step 3. Delete any copy made for the double-click test. Private rosters belong
only in `private_rosters\` (git-ignored) and must never be copied into the
package, the repository or a build folder — `scripts\build_app.py` fails the
build if they are.
