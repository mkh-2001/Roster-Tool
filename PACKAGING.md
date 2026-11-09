# Packaging Strategy (Phase 10, not built yet)

Goal: staff double-click **Roster Tool**, upload a PDF, work, close the window.
They should not need Python, a terminal, a URL or internet access.

Packaging is deliberately postponed until the core workflow has been checked
against a real roster. This document records the plan so it can be adjusted
to IT requirements.

## Recommended approach

**PyInstaller, one-folder build (`--onedir`)**, per operating system.

* It bundles Python, `pypdfium2` (with its native PDFium library), `reportlab`,
  the UI files and the fonts. No Python installation is needed on staff computers.
* **One-folder rather than one-file.** One-file builds unpack themselves into a
  temp folder on every launch. That makes startup slower, can leave files
  behind if the app is killed, and triggers antivirus false positives more often.
* **It must be built on each target OS.** PyInstaller cannot cross-compile, so
  a Windows build needs a Windows machine (or CI runner) and a macOS build needs a Mac.
* The expected size is roughly 40–70 MB unpacked.

### Window: two interchangeable launch modes

| Mode | How it works | Pros | Cons |
|---|---|---|---|
| **A. Native window** (preferred) | A small launcher starts the local server and opens it in a **pywebview** window. Closing the window ends the process. | Feels like a normal app, with no browser tab and no visible URL. | One more dependency. Windows needs the Microsoft Edge **WebView2 Runtime**, which ships with Windows 11 and most updated Windows 10 machines. Print and download behaviour inside the web view must be tested, and may need a "Save PDF…" dialog fallback implemented in Python. |
| **B. Default browser** (works today) | What `python -m roster_tool` already does: start the server and open the default browser. | No extra dependency, and printing uses the browser's PDF viewer. | It's a browser tab, so the app needs a shutdown mechanism, either a "Quit" button or auto-exit once the page has been closed for N seconds (a small heartbeat endpoint). |

Both modes use the same server and UI unchanged, so the choice can follow IT policy.

### Build sketch (to be validated)

```bash
pip install pyinstaller            # plus pywebview for mode A
pyinstaller --name "Roster Tool" --onedir --windowed \
  --add-data "roster_tool/static:roster_tool/static" \
  --add-data "roster_tool/assets:roster_tool/assets" \
  --collect-all pypdfium2 \
  roster_tool/__main__.py
```

On Windows, `--add-data` uses `;` instead of `:`. The code finds `static/`
and `assets/` relative to its own files, which matches PyInstaller's
`_internal/roster_tool/...` layout.

## Signing and distribution (IT decisions)

* **Windows:** an unsigned app triggers SmartScreen warnings. An Authenticode
  certificate (often the college's) avoids this. IT may prefer to deploy
  through Intune/SCCM, or wrap the folder in an MSI.
* **macOS:** a Developer ID signature plus notarization avoids Gatekeeper
  blocks. IT may deploy through Jamf.
* There is no installer requirement: a zipped folder with a shortcut works if
  IT allows it.

## Offline and privacy requirements that still apply after packaging

* The server binds to `127.0.0.1` only and makes no outbound requests.
  Packaging must not add update checks, crash reporters or telemetry.
* Run the packaged app with **networking disabled** as an acceptance test.
* Keep the console hidden (`--windowed`), but keep logs free of student data.
  They already are.

## Post-packaging acceptance checklist

- [ ] Fresh staff-like machine without Python: the app opens by double-click
- [ ] Wi-Fi off and cable unplugged: upload, help sheet, print, PDF download and CSV download all work
- [ ] Printed help sheet on Letter paper has nothing clipped
- [ ] Closing the window ends the process (nothing left in Task Manager or Activity Monitor)
- [ ] No files left in the temp folder after closing
- [ ] Antivirus does not quarantine the app
- [ ] `netstat` / `lsof` shows the app listening on `127.0.0.1` only

## Open question for the team

Which operating systems do staff computers run (Windows, macOS or both), and
does IT allow unsigned internal apps or require signing and managed deployment?
