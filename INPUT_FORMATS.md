# Roster Input Formats

Roster Tool accepts a class roster as **PDF, CSV or Excel (.xlsx)**. Every
format is turned into the same normalized roster: course title, section, and
students tagged as Current Students or Wait List. Validation, the Help Sheet and
the Event CSV work identically whatever the source format was.

> **Validation status (Nov 2026):**
> * **PDF**: verified against a real Los Rios "Print Roster" PDF.
> * **CSV and XLSX: no real Los Rios CSV or XLSX roster export has been
>   validated yet.** The recognised column names, Status values and layouts
>   below are **representative assumptions**. They are tested only with
>   invented fixtures. When a real export becomes available, validate it as
>   described in REAL_ROSTER_INTEGRATION.md and adjust `TableRules` in
>   `roster_tool/inputs/table.py` if needed. Until then, an export using
>   different headers or status words is **rejected with a clear message**,
>   not guessed.

```
uploaded file (bytes + its extension as a hint)
  └─► format detection        roster_tool/inputs/__init__.py
        ├─► PDF adapter        inputs/pdf_input.py  → pdf_extractor + roster_parser (unchanged)
        ├─► CSV adapter        inputs/csv_input.py  ┐
        └─► XLSX adapter       inputs/xlsx_input.py ┴→ inputs/table.py (rows → roster)
  └─► normalize → validate → Help Sheet / Event CSV          (pipeline.process_roster)
```

## Supported and unsupported files

| Type | Supported | Notes |
|---|---|---|
| `.pdf` (digital, with a text layer) | Yes | The real Los Rios "Print Roster" layout and generic tables or lists. |
| `.csv` | Yes | UTF-8 (with or without BOM), UTF-16 with BOM, or Windows-1252. Comma, semicolon or tab delimited. |
| `.xlsx` | Yes | Read locally with `openpyxl`. Excel does not need to be installed. |
| Image-only / scanned PDF | **No** | Rejected with a clear message. There is no OCR. |
| Images (`.png`, `.jpg`, …) | **No** | Rejected; there is no OCR. |
| `.xls`, `.xlsm`, `.xlsb` | **No** | Save as `.xlsx` or CSV. Reading legacy `.xls` would need another library, so it is out of scope for now. |
| `.ods`, `.numbers`, `.docx`, `.txt`, … | **No** | Rejected as unsupported. |

## Format detection

1. **Content first.** Each format has a cheap signature check:
   * PDF: `%PDF-` in the first 1 KB.
   * XLSX: a ZIP archive containing `[Content_Types].xml` and `xl/workbook.xml`.
   * CSV: the bytes decode as text with no binary control characters.

   Binary signatures are checked before text.
2. **The extension is only a hint, and it must agree.** A `.pdf` that is
   really CSV text, or a `.csv` that is really a workbook, is rejected
   ("This file is not a PDF file: …"). The UI sends **only the extension**,
   never the file's name.
3. Recognisable but unsupported content gets a specific message: legacy or
   password-protected Office files (OLE2), images, other ZIP documents, and
   damaged ZIP/XLSX files.

**The filename is never roster data** in any format: course, section, class
number, term, instructor, counts and enrollment status all come from the file's
contents. `process_roster(data, format_hint)` has no filename parameter, and
tests prove that renaming a file never changes the parsed roster.

## PDF

This is the existing path, unchanged: PDFium text extraction, then
`roster_parser.TableRosterParser` (see REAL_ROSTER_INTEGRATION.md). A PDF
without a usable text layer raises `NoTextLayerError` ("it may be a scan"). A
future local OCR adapter could be added as another input format without
touching anything downstream.

## CSV and XLSX (spreadsheet rosters)

Both adapters produce rows of text cells, and `inputs/table.py` interprets
them using **the PDF parser's own rules** for:
* populations
* the confirmed Los Rios W-number rule (`1234567` → `W1234567`, only in the
  recognised Student ID column of a Current Students / Wait List row)
* `N. ` row-number prefixes
* pronoun removal
* `Last, First` name splitting (ambiguous names are flagged)
* per-population count checks
* metadata labels, including "first class number in `Meetings:`"

### Header row (required)

A header row must appear in the first 30 rows, and must contain a **student ID**
column plus either **First Name + Last Name** or a single **Name** column.
Header matching ignores case, `_`, a trailing `:` and extra spaces.

| Concept | Recognised headers (examples) |
|---|---|
| Student ID | `Student ID`, `ID`, `ID Number`, `W Number`, `W#`, `Emplid`, `Student Number` |
| First / Last name | `First Name`, `First`, `Given Name` / `Last Name`, `Last`, `Surname`, `Family Name` |
| Middle | `Middle`, `Middle Name`, `Middle Initial`, `MI` (added to First Name, matching the PDF's `First M`) |
| Full name | `Name`, `Student Name`, `Full Name`, `Name (Last, First)` |
| Status | `Status`, `Enrollment Status`, `Enrl Status`, `Population`, `Roster Status` |
| Course | `Course`, `Course Title`, `Course Name`, `Class Title` |
| Section | `Section`, `Section Number`, `Class Nbr`, `Class Number`, `Class #` |
| Pronouns | `Pronouns`, `Preferred Pronouns` (recognised, never added to names) |
| Row number | `#`, `No`, `Seq`, `Row` |

Other columns (Email, Units, Program, …) are ignored. **Two columns for the same
concept** (for example two `First Name` columns, or `Student ID` and `ID`)
are an error, never a guess.

### Populations

* **Status column:**
  * `Enrolled` / `Current` / `Active` / `Registered` → Current Students.
  * `Waitlisted` / `Waiting` / `Wait List` → Wait List.
  * `Dropped` / `Withdrawn` / `Cancelled` / `Permission` → excluded.
  * Any other value, or a blank status, rejects the file and names the row numbers.
* **Section-heading rows**, exactly as in the PDF. A row containing only
  `Current Students`, `Wait List`, `Drops` or `Permission Numbers` switches the
  population, and the header row may repeat after each heading. Rows under
  Drops and Permission Numbers are skipped without being read.
* With neither, every row is a Current Student.
* If both are present and disagree, the file is rejected.

### Course, section and counts

* `Label: value` rows above the header (`Course:`, `Section:`, `Meetings:`,
  …; in a spreadsheet the colon is optional there) are read like the PDF header
  block. Label rows after the header (e.g. `Total Enrolled: 17`) supply a
  stated count.
* Otherwise the `Course` and `Section` / `Class Nbr` columns are used. They
  must contain a **single value**; a file mixing sections is rejected ("Upload
  one class section at a time").

### XLSX specifics

* **Sheet choice is deterministic.** Only visible sheets with a recognisable
  header row are candidates (hidden sheets are ignored):
  * One candidate → it is used.
  * Several candidates → the single one whose name contains "roster" is used.
  * Otherwise the upload is rejected, and the message lists the candidate sheet names.
* **Values only.** Formulas are never evaluated: Excel's saved values are read,
  and a formula without a saved value reads as empty. External links are not
  loaded and macros are ignored.
* **Size guards.** The archive's uncompressed size (at most 100 MB) is checked
  before opening, and each sheet is limited to 5,000 non-empty rows and 60 columns.
* **IDs stored as numbers** (`3000001`) work. A leading zero lost by Excel
  (`0123456` stored as `123456`) is flagged as malformed, never padded. Format
  ID columns as Text in Excel to avoid this.

### CSV specifics

* Parsed with Python's `csv` module (RFC 4180): quoted commas, doubled quotes
  and line breaks inside quotes all work. Malformed quoting is rejected, and the
  message names the line.
* At most 5 MB and 5,000 rows.

## What is rejected or flagged

| Problem | Result |
|---|---|
| Empty file, unsupported type, extension/content mismatch, damaged file | Upload rejected with a clear message |
| No recognisable header, duplicated header concept, unknown or blank status, status contradicting a heading, several sections, ambiguous or no roster sheet | Upload rejected with a clear message |
| Missing, malformed or duplicate student ID; missing first or last name; count mismatch | Row **kept** and marked in the UI; outputs are blocked until it is fixed |
| Name without a comma that has 3+ parts (`Isabella de la Cruz`) | Row kept with a "check the split" warning |

Problem student rows are never silently discarded.

## Adding another input format later

1. Create `roster_tool/inputs/<format>_input.py` with:
   * `matches(data) -> bool`: a cheap content signature check.
   * `read(data) -> InputResult`: produces a `roster_parser.ParseResult`. For
     tabular formats, build rows of strings and call `inputs.table.TABLE.interpret(rows, source)`.
   * `FORMAT = InputFormat(key, label, extensions, description, matches, read, text_based=...)`.
2. Add `FORMAT` to `FORMATS` in `roster_tool/inputs/__init__.py`.
3. Add fake fixtures and tests (see `tests/test_input_formats.py`).

The server, the UI file picker (its accepted types are filled in from
`FORMATS`), validation, and the Help Sheet and Event CSV generators need **no
changes**.

## Dependency

`openpyxl` (pure Python, plus its small `et_xmlfile` dependency) reads `.xlsx`
files without Excel. Nothing heavier (such as pandas) is used. CSV parsing uses
only the standard library.
