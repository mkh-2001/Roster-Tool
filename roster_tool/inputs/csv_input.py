"""CSV adapter: standard-library CSV parsing -> rows -> shared table interpreter.

* Encoding: UTF-8 (with or without BOM), UTF-16 with BOM, else Windows-1252
  (what Excel's plain "CSV" export uses on Windows).
* Delimiter: comma; semicolon and tab are tried only if no roster header is
  found with a comma (European Excel exports use semicolons).
* Quoting follows RFC 4180 via the csv module (quoted commas, doubled quotes,
  line breaks inside quotes). Malformed quoting is rejected, not guessed.
"""

from __future__ import annotations

import codecs
import csv
import io
import time
import unicodedata

from ..errors import MalformedInputError, RosterInputError
from .base import InputFormat, InputResult
from .table import TABLE, MissingHeaderError

MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
DELIMITERS = (",", ";", "\t")


def decode_text(data: bytes) -> str | None:
    """The file as text, or None if it does not look like text."""
    if data.startswith(codecs.BOM_UTF8):
        candidates = [("utf-8", data[len(codecs.BOM_UTF8):])]
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates = [("utf-16", data)]
    else:
        if b"\x00" in data[:8192]:
            return None
        candidates = [("utf-8", data), ("cp1252", data)]
    for encoding, raw in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if any(unicodedata.category(ch) == "Cc" and ch not in "\t\r\n\f" for ch in text[:8192]):
            return None  # control characters: binary data, not text
        return text
    return None


def matches(data: bytes) -> bool:
    return decode_text(data) is not None


def _rows(text: str, delimiter: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    rows: list[list[str]] = []
    try:
        for row in reader:
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise MalformedInputError(f"This CSV file has more than {MAX_ROWS} rows; that is too large for a class roster.")
    except csv.Error:
        raise MalformedInputError(
            f"This CSV file is malformed near line {reader.line_num} (for example an unmatched quote)."
        ) from None
    return rows


def read(data: bytes) -> InputResult:
    if len(data) > MAX_CSV_BYTES:
        raise MalformedInputError(f"This CSV file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MB; that is too large for a class roster.")
    t0 = time.perf_counter()
    text = decode_text(data)
    if text is None:
        raise MalformedInputError("This CSV file could not be read as text.")
    t1 = time.perf_counter()
    errors: list[RosterInputError] = []
    for delimiter in DELIMITERS:
        try:
            parsed = TABLE.interpret(_rows(text, delimiter), "CSV")
        except (MissingHeaderError, MalformedInputError) as exc:
            errors.append(exc)  # try the next delimiter; report the comma attempt's problem if all fail
            continue
        timings = {"decode": (t1 - t0) * 1000, "table_parse": (time.perf_counter() - t1) * 1000}
        return InputResult(parsed, timings_ms=timings)
    raise errors[0]


FORMAT = InputFormat("csv", "CSV", ("csv",), "CSV text", matches, read, text_based=True)
