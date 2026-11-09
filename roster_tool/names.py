"""Split a student's full-name text into first and last name.

Two source conventions are supported:

* "Last, First Middle"  -> unambiguous; split at the first comma.
* "First Middle Last"   -> ambiguous when there are 3+ parts
                           ("Maria de la Cruz", "Mary Ann Smith"), so a
                           deterministic rule is applied AND the row is flagged
                           for the employee to confirm.

Spelling, capitalization, hyphens and apostrophes are never changed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import clean_text

# Generational suffixes are kept with the last name ("Smith Jr., John").
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}

# Lowercase particles that usually begin a multi-part last name.
PARTICLES = {
    "da", "das", "de", "del", "della", "der", "den", "di", "do", "dos", "du",
    "la", "le", "van", "von", "ter", "ten", "bin", "binti", "ibn", "al", "el",
}

NOTE_MULTI_PART = "Name has more than two parts; check which part is the last name."
NOTE_ONE_PART = "Only one name part was found."
NOTE_NO_COMMA = "Other names use 'Last, First' but this one has no comma; check the split."
NOTE_EXTRA_COMMA = "Name contains more than one comma; check the split."


@dataclass(frozen=True)
class NameSplit:
    first: str
    last: str
    note: str | None = None


def _is_suffix(token: str) -> bool:
    return token.lower().rstrip(",") in SUFFIXES


def split_last_first(text: str) -> NameSplit:
    """'Last, First Middle' (also 'Last,First'). Text must contain a comma."""
    last, _, rest = clean_text(text).partition(",")
    last, rest = last.strip(), rest.strip()
    note = None
    if "," in rest:
        parts = [p.strip() for p in rest.split(",") if p.strip()]
        if len(parts) == 2 and _is_suffix(parts[1]):  # "Smith, John, Jr."
            return NameSplit(parts[0], f"{last} {parts[1]}")
        rest = ", ".join(parts)
        note = NOTE_EXTRA_COMMA
    tokens = rest.split(" ")
    if len(tokens) > 1 and _is_suffix(tokens[-1]):  # "Smith, John Jr."
        return NameSplit(" ".join(tokens[:-1]), f"{last} {tokens[-1]}", note)
    return NameSplit(rest, last, note)


def split_first_last(text: str) -> NameSplit:
    """'First Middle Last'. Ambiguous for 3+ parts, which are flagged."""
    tokens = [t for t in clean_text(text).split(" ") if t]
    suffix = ""
    if len(tokens) > 2 and _is_suffix(tokens[-1]):
        suffix = " " + tokens.pop()
    if not tokens:
        return NameSplit("", "")
    if len(tokens) == 1:
        return NameSplit("", tokens[0] + suffix, NOTE_ONE_PART)
    if len(tokens) == 2:
        return NameSplit(tokens[0], tokens[1] + suffix)
    split_at = len(tokens) - 1
    for i in range(1, len(tokens) - 1):
        if tokens[i].lower() in PARTICLES:
            split_at = i
            break
    return NameSplit(" ".join(tokens[:split_at]), " ".join(tokens[split_at:]) + suffix, NOTE_MULTI_PART)


def split_name(text: str, order: str = "auto") -> NameSplit:
    """order: 'auto' (comma => Last, First), 'last_first', or 'first_last'."""
    text = clean_text(text)
    if order not in ("auto", "last_first", "first_last"):
        raise ValueError(f"unknown name order: {order}")
    if "," in text and order in ("auto", "last_first"):
        return split_last_first(text)
    result = split_first_last(text)
    if order == "last_first" and text:
        return NameSplit(result.first, result.last, NOTE_NO_COMMA)
    return result
