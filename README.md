# Roster Tool

A small offline utility for college staff. You upload a class roster PDF once
and it produces either:

1. **Help Sheet**: a printable US Letter sheet with one checkbox list per meeting day.
2. **Event Invites CSV**: `Email,First Name,Last Name,SSO ID,Student ID` for the event-invite import.

Everything runs on the local computer. There is no cloud, no API calls, no
telemetry, no database and no internet requirement.

> **Status:** the core workflow is complete and tested, but **only against fake
> roster PDFs we generated**. Before relying on it, validate the parser against
> real roster PDFs using [REAL_ROSTER_INTEGRATION.md](REAL_ROSTER_INTEGRATION.md).
> Desktop packaging is planned in [PACKAGING.md](PACKAGING.md).

## Run it (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m roster_tool            # opens the UI in your browser (127.0.0.1 only)
```

| Task | Command |
|---|---|
| Run all tests | `.venv/bin/python -m pytest` |
| Benchmark | `.venv/bin/python scripts/benchmark.py [roster.pdf]` |
| See what the parser sees | `.venv/bin/python scripts/inspect_pdf.py roster.pdf [--students]` |
| Regenerate fake fixtures | `.venv/bin/python scripts/make_fixtures.py` |

## How it works

```
roster PDF ──► pdf_extractor ──► roster_parser ──► models.Roster ──► validation
               (PDFium text,      (ROSTER-SPECIFIC     (normalized)        │
                lines + cells)     rules: ParseRules)                      │
                                                     ┌─────────────────────┴───────┐
                                                     ▼                             ▼
                                        help_sheet_generator            event_csv_generator
                                        (ReportLab PDF ► Print/Download) (exact CSV ► Download)
```

* **Parse once.** The browser keeps the normalized roster (with any edits) and
  sends it back for each output. The server validates it again every time.
* **Only `roster_parser.py` knows the PDF layout.** Supporting the real roster
  should mean changing that file (mostly `ParseRules`) and nothing else.
* **Errors block output and are never silently fixed.** Missing, malformed or
  duplicate W numbers, empty names, and a student count that disagrees with
  the PDF all block output until corrected. Uncertain items (ambiguous name
  splits, guessed course or section) are shown as warnings.

| Module | Responsibility |
|---|---|
| `roster_tool/models.py` | `Student` and `Roster`, text normalization, the W-number pattern |
| `roster_tool/pdf_extractor.py` | PDF to positioned text lines and cells (no roster knowledge) |
| `roster_tool/roster_parser.py` | Text to roster: **the part to adapt for the real roster** |
| `roster_tool/names.py` | First and last name splitting |
| `roster_tool/validation.py` | Sanity checks (errors block output, warnings inform) |
| `roster_tool/event_csv_generator.py` | Event Invites CSV |
| `roster_tool/help_sheet_generator.py` | Help sheet PDF layout and pagination |
| `roster_tool/pipeline.py` | Extract, parse, normalize and validate, with stage timings |
| `roster_tool/server.py` | Local-only HTTP server for the UI |
| `roster_tool/static/` | UI (plain HTML, CSS and JS, no framework, no CDN) |

## Technology choices

| Need | Choice | Why |
|---|---|---|
| Language | Python 3.12+ (developed on 3.13) | Mature PDF libraries and simple packaging with PyInstaller. |
| PDF text | **pypdfium2** (PDFium) | Native speed (a few ms per page), robust with malformed files, Apache/BSD licence. PyMuPDF was avoided because of its AGPL licence, and pdfminer/pdfplumber because they are about 10–50× slower. |
| PDF output | **ReportLab** | Exact point-level layout, fast, BSD licence. |
| Font | DejaVu Sans Condensed (bundled, [licence](roster_tool/assets/fonts/DejaVu-LICENSE.txt)) | Prints accented names, and the condensed width fits three columns. |
| CSV | stdlib `csv` | RFC 4180 quoting. UTF-8 without a BOM so the header matches exactly. CRLF line endings. |
| Server | stdlib `http.server` | Only 4 API routes, no dependency, no debug mode to leave on. |
| Tests | pytest | |

## Output formats

**CSV**, locked down by `tests/test_event_csv.py`:

```
Email,First Name,Last Name,SSO ID,Student ID
W1234567@apps.losrios.edu,Hewad,Ahmadzai,W1234567,W1234567
```

**Help sheet:** course title, section and tutor name at the top. Then one
column per selected day, each listing `☐ W1234567 Last, First` for every
student, with a `Date: ____` line.
* Margins are 0.4″.
* There are at most 3 day-columns per page. With 4 or more days, the days are
  split across pages (4 days → 2+2, 5 → 3+2).
* A single day with a long list continues in a second column on the same sheet.
* Row height adjusts within a comfortable range to avoid an extra page.
  Otherwise the sheet paginates. Students are never dropped and names are never truncated.

## Privacy and security

* The server binds to `127.0.0.1` on a random port and is not reachable from the
  network. API calls require a per-launch secret token. The Host and Origin
  headers are checked, and a strict Content Security Policy is applied.
* PDFs are processed in memory. No temporary files are written and no roster is stored.
* Logs record only method, route, status and duration. Names and W numbers are never logged.
* Tests cover loopback-only binding, blocked outbound network, no temp files,
  log contents, path traversal and token and origin checks.
* Real rosters must never be committed. `private_rosters/` is git-ignored for
  local validation.

## Performance (MacBook, fake fixtures)

| Roster | Upload to roster | CSV | 3-day help sheet |
|---|---|---|---|
| 16 students, 1 page | ~2 ms | <0.1 ms | ~3 ms |
| 95 students, 3 pages | ~12 ms | ~0.1 ms | ~9 ms |
| 300 students, 8 pages | ~37 ms | ~0.4 ms | ~23 ms |

The target is ≤ 2000 ms. Measure on staff hardware with `scripts/benchmark.py`.

## Known limitations

* Only tested on fake rosters; see REAL_ROSTER_INTEGRATION.md.
* Scanned (image-only) PDFs are refused. There is no OCR, by design.
* A "First Last" roster cannot reliably split names with 3 or more parts, so those rows are flagged for review.
* Names that wrap onto a second line in the source PDF surface as rows needing
  correction. They are not merged automatically until a real roster shows how
  wrapping looks.
