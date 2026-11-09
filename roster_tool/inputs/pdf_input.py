"""PDF adapter: wraps the existing PDF text extractor and roster parser unchanged."""

from __future__ import annotations

import time

from ..pdf_extractor import extract_pdf
from ..roster_parser import DEFAULT_PARSER
from .base import InputFormat, InputResult


def matches(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def read(data: bytes) -> InputResult:
    doc = extract_pdf(data)  # raises PdfExtractionError (no text layer, encrypted, damaged...)
    t0 = time.perf_counter()
    parsed = DEFAULT_PARSER.parse(doc)
    timings = dict(doc.timings_ms)
    timings["roster_parse"] = (time.perf_counter() - t0) * 1000
    return InputResult(parsed, page_count=len(doc.pages), timings_ms=timings)


FORMAT = InputFormat("pdf", "PDF", ("pdf",), "a PDF document", matches, read)
