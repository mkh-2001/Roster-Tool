# Roster Tool

A small offline utility for college staff. You upload a class roster once
(**PDF, CSV or Excel .xlsx**, see [INPUT_FORMATS.md](INPUT_FORMATS.md)) and
it produces either:

1. **Help Sheet**: a printable US Letter sheet with one checkbox list per meeting day.
2. **Event Invites CSV**: `Email,First Name,Last Name,SSO ID,Student ID` for the event-invite import.

Everything runs on the local computer. There is no cloud, no API calls, no
telemetry, no database and no internet requirement.

> **Status:** the core workflow is complete and tested. The parser has been
> adapted to and verified against **one** real Los Rios "Print Roster" PDF; more
> real rosters (especially one with waitlisted students) should be validated
> using [REAL_ROSTER_INTEGRATION.md](REAL_ROSTER_INTEGRATION.md).
> **Desktop app:** a macOS `Roster Tool.app` is built and validated (Apple
> Silicon, macOS 13 or later; unsigned internal build), and a Windows
> `Roster Tool.exe` folder is built and validated on Windows 11 x64 (Windows 10
> is the technical floor but untested; the offline test is still outstanding).
> Both are unsigned internal builds. See [PACKAGING.md](PACKAGING.md) and
> [WINDOWS_BUILD.md](WINDOWS_BUILD.md).
> **Where the built apps are:** the project root holds `Roster Tool.app` (macOS)
> and `Roster Tool - Windows\` (keep `Roster Tool.exe` together with its
> `_internal\` folder). Move the Mac app between computers only as the Mac-made
> `.zip`, never as an unpacked `.app` through Windows (see PACKAGING.md).

## Run it (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m roster_tool            # opens the UI in your browser (127.0.0.1 only)
```

The app stops when its browser tab is closed, when **Quit Roster Tool** is
clicked, or on Ctrl+C. It runs the same launcher as the packaged desktop app.

| Task | Command |
|---|---|
| Run all tests | `.venv/bin/python -m pytest` |
| Build the desktop app (this platform) | macOS: `.venv-macos-build/bin/python scripts/build_app.py` with the python.org Python ([PACKAGING.md](PACKAGING.md)); Windows: [WINDOWS_BUILD.md](WINDOWS_BUILD.md) |
| Smoke-test the built app | `.venv-macos-build/bin/python scripts/smoke_test_package.py [--no-network]` (Windows: WINDOWS_BUILD.md) |
| Benchmark | `.venv/bin/python scripts/benchmark.py [roster.pdf roster.csv roster.xlsx]` |
| See what the parser sees | `.venv/bin/python scripts/inspect_pdf.py roster.pdf [--students]` |
| Regenerate fake fixtures | `.venv/bin/python scripts/make_fixtures.py` |

## How it works

```
roster file ─► format detection ─┬─► PDF:  pdf_extractor ─► roster_parser ─┐
 (PDF/CSV/XLSX)  (inputs/)        ├─► CSV:  csv module ──┐                  │
                                  └─► XLSX: openpyxl ────┴─► inputs/table ──┤
                                                                            ▼
                       models.Roster (normalized: Current Students / Wait List) ─► validation
                                                     ┌──────────────────────────────┴───────┐
                                                     ▼                                      ▼
                                        help_sheet_generator                     event_csv_generator
                                        (ReportLab PDF ► Print/Download)          (exact CSV ► Download)
```

* **Parse once.** The browser keeps the normalized roster (with any edits) and
  sends it back for each output. The server validates it again every time.
* **Source-independent outputs.** Format detection picks an input adapter
  from the file's content (the extension must agree). All adapters produce the
  same `Roster`, so validation, the Help Sheet and the Event CSV never know the
  source format. CSV and XLSX use the PDF parser's rules for populations,
  W numbers, names and metadata.
* **Only the parser and adapters know file layouts.** Supporting a new PDF
  layout means changing `roster_parser.py` (mostly `ParseRules`). Supporting a
  new file type means adding an adapter under `roster_tool/inputs/`.
* **Two student populations.** Each student is tagged **Current Students** or
  **Wait List** (`Student.status`). Dropped students and permission/add codes
  never enter the roster. All roster information comes from the file's
  contents; the filename is never used.
* **Errors block output and are never silently fixed.** Missing, malformed or
  duplicate W numbers, empty names, and a student count that disagrees with
  the count stated or numbered in the roster file all block output until corrected. Uncertain items (ambiguous name
  splits, guessed course or section) are shown as warnings.

| Module | Responsibility |
|---|---|
| `roster_tool/models.py` | `Student` and `Roster`, text normalization, the W-number pattern |
| `roster_tool/inputs/` | Format detection and input adapters (PDF, CSV, XLSX); see [INPUT_FORMATS.md](INPUT_FORMATS.md) |
| `roster_tool/errors.py` | Input errors (messages safe to show; no student data) |
| `roster_tool/pdf_extractor.py` | PDF to positioned text lines and cells (no roster knowledge) |
| `roster_tool/roster_parser.py` | PDF text to roster: **the part to adapt for new PDF layouts** |
| `roster_tool/names.py` | First and last name splitting |
| `roster_tool/validation.py` | Sanity checks (errors block output, warnings inform) |
| `roster_tool/event_csv_generator.py` | Event Invites CSV |
| `roster_tool/help_sheet_generator.py` | Help sheet PDF layout and pagination |
| `roster_tool/pipeline.py` | Detect, read, normalize and validate (`process_roster`), with stage timings |
| `roster_tool/server.py` | Local-only HTTP server for the UI |
| `roster_tool/desktop.py` | Desktop launcher: log, self-checks, server start, opening the browser, error dialogs |
| `roster_tool/lifecycle.py` | When to exit: no Roster Tool tab open, Quit, or timeouts (single process, so nothing is orphaned) |
| `roster_tool/resources.py` | The one place that locates static files and fonts (source and packaged) |
| `packaging/` | PyInstaller spec and frozen entry point; see [PACKAGING.md](PACKAGING.md) |
| `roster_tool/static/` | UI (plain HTML, CSS and JS, no framework, no CDN) |

## Technology choices

| Need | Choice | Why |
|---|---|---|
| Language | Python 3.12+ (developed on 3.13) | Mature PDF libraries and simple packaging with PyInstaller. |
| PDF text | **pypdfium2** (PDFium) | Native speed (a few ms per page), robust with malformed files, Apache/BSD licence. PyMuPDF was avoided because of its AGPL licence, and pdfminer/pdfplumber because they are about 10–50× slower. |
| PDF output | **ReportLab** | Exact point-level layout, fast, BSD licence. |
| XLSX input | **openpyxl** | Pure Python, reads values without Excel and never evaluates formulas. pandas would be far heavier and isn't needed. |
| CSV input | stdlib `csv` | RFC 4180 parsing with no extra dependency. |
| Font | DejaVu Sans Condensed (bundled, [licence](roster_tool/assets/fonts/DejaVu-LICENSE.txt)) | Prints accented names, and the condensed width fits three columns. |
| CSV | stdlib `csv` | RFC 4180 quoting. UTF-8 without a BOM so the header matches exactly. CRLF line endings. |
| Server | stdlib `http.server` | Only 7 small API routes, no dependency, no debug mode to leave on. |
| Tests | pytest | |

## Output formats

**CSV**, locked down by `tests/test_event_csv.py`:

```
Email,First Name,Last Name,SSO ID,Student ID
W1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567
```

The CSV contains Current Students followed by Wait List students as ordinary
rows, with no status column or other distinction. Dropped students are never included.
The CSV's header and rows never contain the Tutor, Professor, Term, Meetings
Location or Section. The section is used only in the suggested file name.

**Help sheet:** a header with five fields, and the section number isn't printed:
* **Class** (course title), **Professor** and **Term** come from the roster.
* **Tutor** is typed in by the employee. It is required to generate the help
  sheet, lasts only for the current session, and is never read from the roster.
* **Meetings Location** is prefilled from the roster: the text before the
  LEC/LAB code on the `Meetings:` line. It is shown once when every meeting uses
  the same room, otherwise each is listed with its component, e.g.
  `Room 101 (LEC); Room 102 (LAB)`. The employee can type or edit it. It is
  optional and used only on the help sheet. It is never part of the Event CSV
  and never blocks it.
* A value that isn't given is shown as `—`.
* **Header metadata never blocks the Event CSV.** Professor, Term and Meetings
  Location are Help Sheet data, not part of the CSV contract. Tutor is
  Help Sheet input only. When the roster comes back from the browser, a
  Professor, Term or Meetings Location value that is malformed (not text) or
  oversized (over 300 characters) is dropped. It then prints as `—` on the Help
  Sheet, and the roster is still accepted, so validation and the Event CSV are
  unaffected. The strict checks on the data the CSV does use (students, W
  numbers, course title, section) are unchanged. A Meetings Location sent
  with the Help Sheet request is limited to 300 characters. A longer value
  stops only the Help Sheet, with a message.
  (`roster_tool/models.py: _header_text`, tested in
  `tests/test_models_and_names.py` and `tests/test_server_and_privacy.py`.)

Then one
column per selected day, each listing `☐ W1234567 Last, First` for every
Current Student, with a `Date: ____` line. If the roster has waitlisted
students, every day column also has a labelled **Wait List** group below the
Current Students. If it doesn't, no space is used for one.
* Margins are 0.4″.
* There are at most 3 day-columns per page. With 4 or more days, the days are
  split across pages (4 days → 2+2, 5 → 3+2).
* A single day with a long list continues in a second column on the same sheet.
* Student rows use line spacing 2.0 (21.6 pt per row for 9 pt names), which
  fits about 29 rows per column. A name too long for its column wraps, and its
  row grows taller to hold it. Longer lists paginate. Students are never dropped
  and names are never truncated.

## Privacy and security

* The server binds to `127.0.0.1` on a random port and is not reachable from the
  network. API calls require a per-launch secret token. The Host and Origin
  headers are checked, and a strict Content Security Policy is applied.
* Roster files (PDF, CSV, XLSX) are processed in memory. No temporary files
  are written and no roster is stored. XLSX formulas are never evaluated and
  archive sizes are checked before opening.
* Logs record only method, route, status and duration. Names and W numbers are never logged.
* Tests cover loopback-only binding, blocked outbound network, no temp files,
  log contents, path traversal and token and origin checks.
* Real rosters must never be committed. `private_rosters/` is git-ignored for
  local validation.

## Performance (MacBook, fake fixtures)

| Roster | Upload to roster | CSV | 3-day help sheet |
|---|---|---|---|
| 16 students, 1-page PDF | ~2 ms | <0.1 ms | ~3 ms |
| 95 students, 3-page PDF | ~13 ms | ~0.1 ms | ~9 ms |
| 17 students as PDF / CSV / XLSX | ~4 / ~0.5 / ~2 ms | <0.1 ms | ~3 ms |
| 300 students as PDF / CSV / XLSX | ~40 / ~4 / ~7 ms | ~0.4 ms | ~23 ms |

The target is ≤ 2000 ms. Measure on staff hardware with `scripts/benchmark.py`.

## Known limitations

* Verified against one real roster layout so far; see REAL_ROSTER_INTEGRATION.md
  for what real data has and hasn't covered yet.
* Middle initials are kept as part of First Name for now.
* Scanned (image-only) PDFs and images are refused. There is no OCR, by design.
* `.xls` (legacy Excel), `.xlsm`, `.xlsb`, `.ods` and `.numbers` are not
  supported; save them as `.xlsx` or CSV.
* **No real Los Rios CSV or XLSX roster export has been validated yet.** CSV/XLSX
  column and Status mappings are representative assumptions, tested only with
  invented fixtures (see [INPUT_FORMATS.md](INPUT_FORMATS.md)). Unrecognised
  headers or Status values are rejected, not guessed.
* A "First Last" roster cannot reliably split names with 3 or more parts, so those rows are flagged for review.
* Names that wrap onto a second line in the source PDF surface as rows needing
  correction. They are not merged automatically until a real roster shows how
  wrapping looks.
