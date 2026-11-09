# Real Roster Integration

**Status:** the parser has only been tested against FAKE PDFs that we generated
ourselves (`tests/fixtures/`). Those passing tests show that the architecture
works. They do **not** show that the parser can read the real roster system's
PDFs. Follow this guide the first time a real roster is available, and again
whenever the roster system changes its layout.

> **Never commit real student data.** Real PDFs, text dumps and expected
> results belong in `private_rosters/` (git-ignored) on a staff computer only.
> Do not paste `inspect_pdf.py` output into tickets, chats or emails.

## Pre-real-roster baseline (2026-11-09)

This is the state to protect while adapting the parser.

| Item | Baseline |
|---|---|
| Test suite | 154 passed, 1 skipped (`test_private_rosters.py`, no real rosters present). `test_portability.py` (2 tests) was added after the folder rename. |
| Upload to roster (fake fixtures) | ~2 ms (16 students), ~12 ms (95 students, 3 pages), ~37 ms (300 students, 8 pages) |
| Help sheet (3 days) | ~3 to 23 ms |
| CSV | < 0.5 ms |
| Real-roster support | **Not verified** |

The performance is sufficient, so there should be no further optimization or architectural changes.

## Validation protocol and completion criteria

When a real roster PDF is provided:

1. **Inspect first** (section 1): the text layer, line and cell structure, labels and name format.
2. **Do not change the parser until the extracted structure is understood.**
3. **Report** what the current parser gets right and wrong, before changing anything.
4. **Adapt only the extraction and parser layer** (`roster_parser.py`, and `names.py` or `W_NUMBER_RE` if needed).
5. **Do not redesign working downstream components** (validation, generators, server, UI) unless the real data proves it necessary.
6. **Add sanitized regression fixtures and tests** for every new structure found (section 4b). Never commit real student information.
7. **Verify** course title, section, every W number, every first and last name, the student count, the help-sheet output and the Event CSV output.
8. **Run the complete test suite.** The baseline tests must keep passing.
9. **Re-run the benchmark** (section 10).
10. **Real-roster support is complete only** when the extracted student list and W numbers have been checked against the source PDF itself.

Two constraints also apply:

* **No OCR** unless a real source roster shows that it is actually required (i.e. it has no usable text layer).
* **No packaging (Phase 10)** until this validation is complete.

---

## 1. Inspect the extracted PDF text

```bash
.venv/bin/python scripts/inspect_pdf.py private_rosters/roster.pdf
.venv/bin/python scripts/inspect_pdf.py private_rosters/roster.pdf --students   # also list parsed students
```

The first section shows exactly what the parser receives: one output line per
line of text, with ` | ` wherever there is a wide horizontal gap (a table
column boundary). Check the following:

| Look for | Why it matters |
|---|---|
| `EXTRACTION FAILED: NoTextLayerError` | The PDF is a scan (image only). This tool does not do OCR, so this needs a separate decision. |
| Is each student on **one line**? | If long names wrap onto a second line, those lines are kept as "no W number" rows and block output. The parser needs a merge rule. |
| How is the ID shown? (`W1234567`, `1234567`, `W 1234567`, in an email column?) | This decides the W-number pattern. |
| Name format: `Last, First`, `Last,First Middle`, `First Last`, or separate columns? | This decides the name rules. |
| Exact labels for course, section and enrolment count | These decide the metadata rules. |
| Column header text (e.g. `ID`, `Name`, `Student ID`) | Header detection maps columns by x-position. |
| Page headers and footers on every page | These must not be read as students. |

## 2. Where the parser lives

| File | Contains |
|---|---|
| `roster_tool/pdf_extractor.py` | PDF to lines and cells. Not roster-specific; normally **no changes needed**. |
| `roster_tool/roster_parser.py` | **Roster interpretation.** `ParseRules` plus `TableRosterParser`. This is where adaptation happens. |
| `roster_tool/names.py` | Splitting a full name into first and last. |
| `roster_tool/models.py` | `W_NUMBER_RE`, the single definition of a valid W number, used by both the parser and the validation. |

Everything after the parser (validation, CSV, help sheet, UI) works only with
`models.Roster`. When the parser is changed correctly, nothing else needs to change.

## 3. How the parsing rules are defined

Every layout-specific choice is a field on `ParseRules` (`roster_parser.py`):

| Rule | Default (guess) | Change it when |
|---|---|---|
| `w_number` (from `models.W_NUMBER_RE`) | `W` + 7 digits | Real IDs have a different length or format. |
| `id_candidate` | Loose "looks like an ID" pattern | Real IDs look different. Keep it looser than `w_number` so malformed IDs are surfaced instead of dropped. |
| `name_order` | `auto` (comma means Last, First) | The roster always uses one order. Pin it to `last_first` or `first_last`. |
| `course_labels`, `title_labels`, `section_labels`, `count_labels`, `other_labels` | Common label words | The real labels differ. Labels are matched as `Label:`. |
| `course_line`, `section_fallback` | `SUBJ 123 …` and `Class Nbr 12345` | There are no labels. |
| `ignore_cell` | `Page x of y`, `Printed …`, `Class Roster…` | Other page furniture appears in the table. |
| `header_id`, `header_name`, `header_last`, `header_first`, `header_number` | Typical header words | The real column headers differ. |
| `table_gap_pitches` | 1.6 | Rows are unevenly spaced. |

**If small rule changes are enough**, edit the defaults in `ParseRules`.

**If the layout is fundamentally different**, write a new class with a
`name` attribute and a `parse(doc: ExtractedDocument) -> ParseResult` method,
then point `DEFAULT_PARSER` at it. Reuse `names.split_name` and keep the
key behaviour: every line that looks like a student becomes a row, even an
incomplete one. Never drop it silently.

## 4. Add a real-roster fixture safely

Do both of the following:

**a) Private regression test (real data stays on the staff computer)**

1. Copy the real PDF into `private_rosters/` (git-ignored).
2. Write `private_rosters/expected.json` by hand from the roster system's own
   screen, **not** from this tool's output (that would only test the tool against itself):
   ```json
   { "roster.pdf": { "course_title": "...", "section": "...", "student_count": 32,
                     "students": [ {"w_number": "W...", "first_name": "...", "last_name": "..."} ] } }
   ```
3. Run `.venv/bin/python -m pytest tests/test_private_rosters.py -v`.

**b) Anonymized layout fixture (safe to commit)**

Recreate the real *layout* with fake people. Use the same column positions,
labels, header and footer text, font sizes and name format. Do this by adding a
draw function and a `FixtureSpec` to `tests/fixture_builder.py`. Do not
"redact" a real PDF: the hidden text layer often still contains the data.

## 5. Verify student count

* Compare `students` in the parse summary with the count shown in the roster system.
* `expected` shows whether the PDF stated a total (`stated`) or numbered its
  rows (`row_numbers`). If they disagree, validation raises `count_mismatch`
  and output is blocked.
* The parser diagnostics should show `0 without an ID` on every page. Any rows
  without an ID mean wrapped text or a missing ID.

## 6. Verify W-number extraction

* Run with `--students` and compare **every** W number against the roster system.
* Check that the leading `W` is kept and there are no extra characters.
* If an email column contains the ID (`w1234567@…`), check that it is **not**
  counted as a second ID (it is excluded by default).
* If real W numbers are not exactly `W` + 7 digits, update `W_NUMBER_RE` and
  `W_NUMBER_DESCRIPTION` in `models.py`, and `id_candidate` if needed.

## 7. Verify first and last names

* Check names with hyphens, apostrophes, two-part surnames, middle names and
  suffixes (Jr., III).
* Count the rows with `review_note`s. With a `Last, First` or separate-column
  roster there should be almost none.
* If the roster uses `First Last`, rows with 3+ name parts are flagged for review
  by design, because that format is genuinely ambiguous.
* Spelling must be exactly as in the source. The tool must not "correct" names.

## 8. Verify course extraction

* `course title` should match the roster exactly, for example `PHIL 320 Logic and Critical Reasoning`.
* `uncertain` lists fields that were guessed without a label. The UI asks the
  employee to confirm them, but a real roster should ideally produce none.

## 9. Verify section extraction

* Confirm with staff **which number** the help sheet should show (a section
  number like `001`, or a class number like `10141`). Rosters often show both.
* Adjust `section_labels` so the right one is picked up.

## 10. Benchmark

```bash
.venv/bin/python scripts/benchmark.py private_rosters/roster.pdf --runs 30
```

The target is `upload -> roster worst case` ≤ 2000 ms. The fake fixtures run in
about 2–40 ms. If a real file is slow, **profile first**:

```bash
.venv/bin/python -m cProfile -s cumtime scripts/benchmark.py private_rosters/roster.pdf --runs 5 | head -40
```

## 11. Add regression tests

1. Add the anonymized layout fixture (4b) and an exact-match test in
   `tests/test_extraction_and_parsing.py`, following
   `test_fixture_parses_exactly`.
2. Add a focused test for each real-world quirk you had to handle (wrapped
   names, odd labels, extra columns).
3. Keep the private test (4a) for each real roster you validate against.
4. Run the full suite: `.venv/bin/python -m pytest`.

### Integration checklist

- [ ] Text layer present (no `NoTextLayerError`)
- [ ] Student count matches the roster system on every test PDF
- [ ] Every W number matches exactly
- [ ] Every first and last name matches, with few or no review notes
- [ ] Course title and section correct, and `uncertain` is empty
- [ ] Page headers and footers not read as students
- [ ] Benchmark under 2 s
- [ ] Anonymized layout fixture and tests committed; real PDFs **not** committed
