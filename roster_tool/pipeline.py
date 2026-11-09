"""PDF bytes -> validated, normalized roster (parse once, reuse everywhere)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .models import Roster, normalize_roster
from .pdf_extractor import extract_pdf
from .roster_parser import DEFAULT_PARSER, RosterParser
from .validation import ValidationIssue, validate_roster


@dataclass
class ProcessResult:
    roster: Roster
    issues: list[ValidationIssue]
    page_count: int
    timings_ms: dict[str, float] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


def process_pdf(data: bytes, parser: RosterParser | None = None) -> ProcessResult:
    """Extract, parse, normalize and validate. Raises PdfExtractionError for unreadable PDFs."""
    t0 = time.perf_counter()
    doc = extract_pdf(data)
    t1 = time.perf_counter()
    parsed = (parser or DEFAULT_PARSER).parse(doc)
    t2 = time.perf_counter()
    roster = normalize_roster(parsed.roster)
    t3 = time.perf_counter()
    issues = validate_roster(roster)
    t4 = time.perf_counter()
    timings = dict(doc.timings_ms)
    timings.update({
        "roster_parse": (t2 - t1) * 1000,
        "normalize": (t3 - t2) * 1000,
        "validate": (t4 - t3) * 1000,
        "total": (t4 - t0) * 1000,
    })
    return ProcessResult(roster, issues, len(doc.pages), timings, parsed.diagnostics)
