"""Roster input formats: detect what an uploaded file is, then read it.

    bytes (+ optional extension hint)
      -> detect_format()      content signature check; the extension must agree
      -> InputFormat.read()   format-specific adapter
      -> InputResult          roster (+ diagnostics), not yet normalized/validated

The extension is only a hint that must agree with the content; the filename
itself is never seen here and never becomes roster data.

To support a new format, add an adapter module exposing FORMAT (see
base.InputFormat) and list it in FORMATS below. The server, UI file picker,
validation and generators pick it up without changes.
"""

from __future__ import annotations

import io
import re
import zipfile

from ..errors import FormatMismatchError, MalformedInputError, RosterInputError, UnsupportedInputError
from . import csv_input, pdf_input, xlsx_input
from .base import InputFormat, InputResult

FORMATS: tuple[InputFormat, ...] = (pdf_input.FORMAT, csv_input.FORMAT, xlsx_input.FORMAT)  # display order
_DETECTION_ORDER = sorted(FORMATS, key=lambda f: f.text_based)  # binary signatures before text
BY_EXTENSION = {ext: fmt for fmt in FORMATS for ext in fmt.extensions}
SUPPORTED_EXTENSIONS = tuple(f".{ext}" for fmt in FORMATS for ext in fmt.extensions)
SUPPORTED_LABEL = ", ".join(f.label for f in FORMATS[:-1]) + f" or {FORMATS[-1].label}"

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls/.doc, and password-protected Office files
_IMAGE_SIGNATURES = (b"\x89PNG", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"BM")
_UNSUPPORTED_EXTENSIONS = {
    "xls": "Older Excel .xls workbooks are not supported. In Excel, save the roster as .xlsx or CSV.",
    "xlsm": "Macro-enabled Excel files are not supported. Save the roster as a plain .xlsx or CSV.",
    "xlsb": "Binary Excel files are not supported. Save the roster as .xlsx or CSV.",
}
_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "webp"}

__all__ = ["FORMATS", "SUPPORTED_EXTENSIONS", "SUPPORTED_LABEL", "InputFormat", "InputResult",
           "RosterInputError", "detect_format", "read_roster"]


def clean_hint(hint: str | None) -> str | None:
    """'.CSV' / 'csv' -> 'csv'. Anything that isn't a plain extension is ignored."""
    if not hint:
        return None
    ext = hint.strip().lower().lstrip(".")
    return ext if re.fullmatch(r"[a-z0-9]{1,10}", ext) else None


def detect_format(data: bytes, hint: str | None = None) -> InputFormat:
    if not data:
        raise UnsupportedInputError("The file is empty.")
    ext = clean_hint(hint)
    if ext is not None and ext not in BY_EXTENSION:
        raise UnsupportedInputError(_unsupported_extension(ext))
    fmt = next((f for f in _DETECTION_ORDER if f.matches(data)), None)
    if fmt is None:
        raise _unrecognized_content(data)
    if ext is not None and BY_EXTENSION[ext] is not fmt:
        expected = BY_EXTENSION[ext]
        raise FormatMismatchError(
            f"This file is not a {expected.label} file: its name ends in .{ext}, but its contents look like "
            f"{fmt.description}. Choose the original roster file."
        )
    return fmt


def read_roster(data: bytes, hint: str | None = None) -> tuple[InputFormat, InputResult]:
    fmt = detect_format(data, hint)
    return fmt, fmt.read(data)


def _unsupported_extension(ext: str) -> str:
    if ext in _UNSUPPORTED_EXTENSIONS:
        return _UNSUPPORTED_EXTENSIONS[ext]
    if ext in _IMAGE_EXTENSIONS:
        return ("Image files are not supported (this tool does not do OCR). Use the roster PDF downloaded "
                "from the college system, or a CSV or .xlsx export.")
    return f"'.{ext}' files are not supported. Upload a {SUPPORTED_LABEL} roster."


def _unrecognized_content(data: bytes) -> RosterInputError:
    if data.startswith(_OLE2):
        return UnsupportedInputError(
            "This looks like an older Excel .xls workbook or a password-protected Office file, which can't be read. "
            "In Excel, save an unprotected copy as .xlsx or CSV."
        )
    if data.startswith(b"PK\x03\x04"):
        try:
            zipfile.ZipFile(io.BytesIO(data)).close()
        except (zipfile.BadZipFile, ValueError):
            return MalformedInputError("This file looks like a damaged or incomplete Excel/ZIP file. Try saving or downloading it again.")
        return UnsupportedInputError(
            "This is a ZIP-based document but not an Excel .xlsx workbook (for example a Word, Numbers or "
            "OpenDocument file). Save the roster as .xlsx, CSV or PDF."
        )
    if data.startswith(_IMAGE_SIGNATURES) or (data[8:12] == b"WEBP") or (data[4:12] in (b"ftypheic", b"ftypheix")):
        return UnsupportedInputError(
            "Image files are not supported (this tool does not do OCR). Use the roster PDF downloaded from the "
            "college system, or a CSV or .xlsx export."
        )
    return UnsupportedInputError(f"This file is not a supported roster file ({SUPPORTED_LABEL}).")
