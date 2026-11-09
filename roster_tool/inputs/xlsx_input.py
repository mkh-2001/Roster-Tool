"""XLSX adapter: openpyxl (read-only, cached values only) -> rows -> shared table interpreter.

Safety: the archive's uncompressed size is checked before opening; formulas
are never evaluated (only the values Excel saved are read); external links are
not loaded; macros are ignored. Hidden sheets are ignored.

Sheet choice is deterministic: the one visible sheet with a recognizable roster
header row. If several sheets qualify, a single sheet whose name contains
"roster" wins; otherwise the upload is rejected as ambiguous.
"""

from __future__ import annotations

import datetime
import io
import re
import time
import warnings
import zipfile

from openpyxl import load_workbook

from ..errors import MalformedInputError, RosterInputError, RosterStructureError
from .base import InputFormat, InputResult
from .table import TABLE

MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_ROWS = 5000            # non-empty rows per sheet
MAX_COLS = 60
END_OF_DATA_BLANK_ROWS = 2000  # this many blank rows in a row ends a sheet
ROSTER_SHEET_NAME = re.compile(r"roster", re.IGNORECASE)


def matches(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError):
        return False
    return "[Content_Types].xml" in names and "xl/workbook.xml" in names


def _check_archive(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_MEMBERS or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
        raise MalformedInputError("This Excel file expands to an unusually large size, so it was not opened.")
    if any(i.flag_bits & 0x1 for i in infos):
        raise MalformedInputError("This Excel file is encrypted. Save an unprotected copy and upload that.")


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value).strip()


def _sheet_rows(ws, limit: int | None = None) -> list[list[str]]:
    rows: list[list[str]] = []
    non_empty = blank_run = 0
    for values in ws.iter_rows(values_only=True, max_col=MAX_COLS):
        if limit is not None and len(rows) >= limit:
            break
        cells = [_cell_text(v) for v in values]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            non_empty += 1
            blank_run = 0
            if non_empty > MAX_ROWS:
                raise MalformedInputError(f"A worksheet has more than {MAX_ROWS} rows; that is too large for a class roster.")
        else:
            blank_run += 1
            if blank_run >= END_OF_DATA_BLANK_ROWS:
                break
        rows.append(cells)
    return rows


def _choose(candidates: list, all_titles: list[str]):
    if not candidates:
        raise RosterStructureError(
            f"No worksheet in this workbook looks like a roster (checked: {', '.join(all_titles) or 'none'}; "
            "hidden sheets are ignored). The roster sheet needs a header row with a student ID column and name column(s)."
        )
    if len(candidates) == 1:
        return candidates[0]
    named = [ws for ws in candidates if ROSTER_SHEET_NAME.search(ws.title)]
    if len(named) == 1:
        return named[0]
    raise RosterStructureError(
        f"More than one worksheet looks like a roster ({', '.join(ws.title for ws in candidates)}). "
        "Remove the extra sheets, or name the roster sheet 'Roster', then upload again."
    )


def read(data: bytes) -> InputResult:
    t0 = time.perf_counter()
    _check_archive(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # openpyxl style warnings are noise here
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception:
        raise MalformedInputError(
            "This Excel workbook could not be opened. It may be damaged, or not a real .xlsx file."
        ) from None
    t1 = time.perf_counter()
    try:
        titles = [ws.title for ws in workbook.worksheets]
        visible = [ws for ws in workbook.worksheets if ws.sheet_state == "visible"]
        candidates = [ws for ws in visible if TABLE.has_header(_sheet_rows(ws, limit=TABLE.rules.header_scan_rows))]
        sheet = _choose(candidates, titles)
        title = sheet.title
        rows = _sheet_rows(sheet)
    except RosterInputError:
        raise
    except Exception:
        raise MalformedInputError("This Excel workbook could not be read. It may be damaged.") from None
    finally:
        workbook.close()
    t2 = time.perf_counter()
    parsed = TABLE.interpret(rows, f"XLSX sheet {title!r}")
    parsed.diagnostics.insert(0, f"workbook: {len(titles)} sheet(s), {len(candidates)} roster candidate(s)")
    timings = {"workbook_open": (t1 - t0) * 1000, "sheet_read": (t2 - t1) * 1000,
               "table_parse": (time.perf_counter() - t2) * 1000}
    return InputResult(parsed, timings_ms=timings)


FORMAT = InputFormat("xlsx", "Excel (.xlsx)", ("xlsx",), "an Excel .xlsx workbook", matches, read)
