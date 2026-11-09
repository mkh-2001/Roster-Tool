"""Errors raised while reading a roster file (any format).

Every message is safe to show to the employee and never contains student
names or IDs. The server returns `user_message` to the UI and logs nothing
from it.
"""

from __future__ import annotations


class RosterInputError(Exception):
    """Base class: the uploaded file cannot be turned into a roster."""

    user_message = "This file could not be read as a roster."

    def __init__(self, user_message: str | None = None):
        if user_message:
            self.user_message = user_message
        super().__init__(self.user_message)


class UnsupportedInputError(RosterInputError):
    """Not a supported file type (e.g. .xls, an image, a Word file)."""


class FormatMismatchError(RosterInputError):
    """The file's extension does not match its actual contents."""


class MalformedInputError(RosterInputError):
    """The file has the right type but is damaged or cannot be parsed."""


class RosterStructureError(RosterInputError):
    """The file is readable but its roster structure is missing or ambiguous
    (no recognizable header row, ambiguous columns or sheets, unknown status values...)."""
