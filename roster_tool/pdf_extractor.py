"""PDF -> positioned text lines. Knows nothing about rosters.

Uses PDFium (via pypdfium2) to read the PDF's existing text layer. No OCR.

Output structure:
    ExtractedDocument.pages[*].lines[*].cells[*]

A *line* is a set of characters sharing a baseline. A line is split into
*cells* wherever there is a wide horizontal gap (table columns, or the wide
spacing between an ID and a name). Inside a cell, words are joined by single
spaces. This lets the roster parser work with table columns without knowing
anything about PDF internals.

PDFium is not thread-safe, so all PDFium use is serialized with a lock.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from ctypes import c_double
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .errors import RosterInputError

MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PAGES = 200

# Gaps are measured in multiples of the font size ("em").
WORD_GAP_EM = 0.45  # a gap this wide is always a word break
CELL_GAP_EM = 1.0   # a gap this wide starts a new cell (column)
BASELINE_TOL_EM = 0.3  # chars whose baselines differ less than this share a line
DUPLICATE_GLYPH_EM = 0.15  # same glyph drawn twice ("fake bold") within this offset

_PDFIUM_LOCK = threading.Lock()


# --- Errors (messages are safe to show to the employee) --------------------

class PdfExtractionError(RosterInputError):
    user_message = "This PDF could not be read."


class NotAPdfError(PdfExtractionError):
    user_message = "This file is not a PDF."


class PdfTooLargeError(PdfExtractionError):
    user_message = f"This PDF is larger than the {MAX_PDF_BYTES // (1024 * 1024)} MB limit."


class EncryptedPdfError(PdfExtractionError):
    user_message = "This PDF is password-protected. Please download an unprotected copy of the roster."


class CorruptPdfError(PdfExtractionError):
    user_message = "This PDF appears to be damaged or incomplete. Try downloading the roster again."


class NoTextLayerError(PdfExtractionError):
    user_message = (
        "This PDF has no readable text (it may be a scan or a picture of a roster). "
        "Please use the roster PDF downloaded directly from the college system."
    )


# --- Result types ------------------------------------------------------------

@dataclass(frozen=True)
class TextCell:
    text: str
    x0: float
    x1: float


@dataclass
class TextLine:
    page: int  # 1-based
    y: float   # baseline, PDF units from the bottom of the page
    font_size: float
    cells: list[TextCell]

    @property
    def text(self) -> str:
        return " ".join(c.text for c in self.cells)


@dataclass
class ExtractedPage:
    number: int
    width: float
    height: float
    lines: list[TextLine]


@dataclass
class ExtractedDocument:
    pages: list[ExtractedPage]
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def lines(self) -> list[TextLine]:
        return [line for page in self.pages for line in page.lines]

    def debug_text(self) -> str:
        """Human-readable dump: one line per text line, cells separated by ' | '."""
        out = []
        for page in self.pages:
            out.append(f"=== page {page.number} ({page.width:.0f} x {page.height:.0f} pt) ===")
            for line in page.lines:
                out.append(f"[y={line.y:7.1f}] " + " | ".join(c.text for c in line.cells))
        return "\n".join(out)


# --- Extraction --------------------------------------------------------------

@dataclass
class _Glyph:
    ch: str
    idx: int
    x0: float
    x1: float
    baseline: float
    size: float
    space_before: bool  # PDFium saw whitespace right before this glyph
    prev_idx: int       # index of the previous non-space glyph in PDFium order


def check_pdf_bytes(data: bytes) -> None:
    """Cheap checks before handing bytes to PDFium."""
    if not data:
        raise NotAPdfError()
    if len(data) > MAX_PDF_BYTES:
        raise PdfTooLargeError()
    # The PDF header must appear near the start (spec allows leading junk up to 1 KB).
    if b"%PDF-" not in data[:1024]:
        raise NotAPdfError()


def extract_pdf(data: bytes) -> ExtractedDocument:
    check_pdf_bytes(data)
    with _PDFIUM_LOCK:
        t0 = time.perf_counter()
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
                raise EncryptedPdfError() from None
            raise CorruptPdfError() from None
        t_open = time.perf_counter()
        try:
            n_pages = len(pdf)
            if n_pages == 0:
                raise CorruptPdfError()
            if n_pages > MAX_PAGES:
                raise PdfExtractionError(f"This PDF has more than {MAX_PAGES} pages; it does not look like a class roster.")
            pages = []
            for i in range(n_pages):
                page = pdf[i]
                try:
                    pages.append(_extract_page(page, i + 1))
                finally:
                    page.close()
        except pdfium.PdfiumError:
            raise CorruptPdfError() from None
        finally:
            pdf.close()
        t_done = time.perf_counter()

    if not any(page.lines for page in pages):
        raise NoTextLayerError()
    return ExtractedDocument(
        pages=pages,
        timings_ms={"pdf_open": (t_open - t0) * 1000, "text_extract": (t_done - t_open) * 1000},
    )


def _extract_page(page: pdfium.PdfPage, number: int) -> ExtractedPage:
    width, height = page.get_size()
    textpage = page.get_textpage()
    try:
        glyphs = _read_glyphs(textpage)
    finally:
        textpage.close()
    return ExtractedPage(number=number, width=width, height=height, lines=_build_lines(glyphs, number))


def _read_glyphs(textpage: pdfium.PdfTextPage) -> list[_Glyph]:
    tp = textpage.raw
    n = textpage.count_chars()
    left, right, bottom, top = c_double(), c_double(), c_double(), c_double()
    ox, oy = c_double(), c_double()
    glyphs: list[_Glyph] = []
    space_pending = True
    prev_idx = -1
    i = 0
    while i < n:
        cp = pdfium_c.FPDFText_GetUnicode(tp, i)
        idx = i
        i += 1
        if 0xD800 <= cp <= 0xDBFF and i < n:  # UTF-16 surrogate pair
            low = pdfium_c.FPDFText_GetUnicode(tp, i)
            if 0xDC00 <= low <= 0xDFFF:
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
                i += 1
        if cp == 0 or cp in (0xFFFE, 0xFFFF):
            continue
        ch = chr(cp)
        if ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"):
            space_pending = space_pending or ch.isspace() or ch in "\r\n"
            continue
        if not pdfium_c.FPDFText_GetCharBox(tp, idx, left, right, bottom, top):
            continue
        if not pdfium_c.FPDFText_GetCharOrigin(tp, idx, ox, oy):
            continue
        size = pdfium_c.FPDFText_GetFontSize(tp, idx) or (top.value - bottom.value) or 10.0
        # NFKC expands ligatures (U+FB01 'fi' -> 'f' 'i'); accents are kept.
        for part in unicodedata.normalize("NFKC", ch):
            glyphs.append(_Glyph(part, idx, left.value, right.value, oy.value, size, space_pending, prev_idx))
            space_pending = False
        prev_idx = idx
    return glyphs


def _build_lines(glyphs: list[_Glyph], page_number: int) -> list[TextLine]:
    if not glyphs:
        return []
    # 1) Group by baseline, top of page first.
    ordered = sorted(glyphs, key=lambda g: (-g.baseline, g.x0))
    groups: list[list[_Glyph]] = []
    for g in ordered:
        if groups:
            ref = groups[-1][0]
            if abs(ref.baseline - g.baseline) <= BASELINE_TOL_EM * max(ref.size, g.size):
                groups[-1].append(g)
                continue
        groups.append([g])

    # 2) Within a line, split into words and cells by horizontal gaps.
    lines = []
    for group in groups:
        group.sort(key=lambda g: (g.x0, g.idx))
        sizes = sorted(g.size for g in group)
        font_size = sizes[len(sizes) // 2]
        cells: list[TextCell] = []
        words: list[str] = []
        word = ""
        cell_x0 = group[0].x0
        prev: _Glyph | None = None
        for g in group:
            if prev is not None:
                em = max(prev.size, g.size)
                gap = g.x0 - prev.x1
                if g.ch == prev.ch and abs(g.x0 - prev.x0) < DUPLICATE_GLYPH_EM * em and g.idx != prev.idx:
                    continue  # same glyph overprinted to fake bold
                if gap > CELL_GAP_EM * em:
                    words.append(word)
                    cells.append(TextCell(" ".join(words), cell_x0, prev.x1))
                    words, word, cell_x0 = [], "", g.x0
                elif gap > WORD_GAP_EM * em or (g.space_before and g.prev_idx == prev.idx):
                    words.append(word)
                    word = ""
            word += g.ch
            prev = g
        words.append(word)
        cells.append(TextCell(" ".join(words), cell_x0, prev.x1))
        lines.append(TextLine(page=page_number, y=group[0].baseline, font_size=font_size, cells=cells))
    return lines
