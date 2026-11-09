"""Types shared by the input adapters (kept separate to avoid import cycles)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..roster_parser import ParseResult


@dataclass
class InputResult:
    parsed: ParseResult                 # roster (not yet normalized/validated) + diagnostics
    page_count: int | None = None       # PDFs only
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class InputFormat:
    """One supported roster file format.

    To add a format: write a module with `matches` (a cheap content check)
    and `read` (bytes -> InputResult), then add its FORMAT to
    `roster_tool.inputs.FORMATS`. Nothing downstream needs to change.
    """

    key: str                            # "csv"
    label: str                          # "CSV" (user-facing)
    extensions: tuple[str, ...]         # ("csv",): accepted file extensions (a hint only)
    description: str                    # "CSV text": how its contents are described in messages
    matches: Callable[[bytes], bool]    # does the CONTENT look like this format?
    read: Callable[[bytes], InputResult]
    text_based: bool = False            # text formats are checked after binary signatures
