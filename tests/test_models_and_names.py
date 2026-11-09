import pytest

from roster_tool.models import Roster, Student, clean_text, is_valid_w_number, normalize_roster, output_stem
from roster_tool.names import NOTE_MULTI_PART, NOTE_ONE_PART, split_name


# --- Normalized model --------------------------------------------------------

def test_display_name_is_last_comma_first():
    assert Student("W1234567", "Hewad", "Ahmadzai").display_name == "Ahmadzai, Hewad"


def test_roster_to_dict_matches_spec_shape():
    roster = Roster("PHIL 320 Logic and Critical Reasoning", "10141", [Student("W1234567", "Hewad", "Ahmadzai")])
    d = roster.to_dict()
    assert d["course_title"] == "PHIL 320 Logic and Critical Reasoning"
    assert d["section"] == "10141"
    assert d["students"][0] == {
        "w_number": "W1234567", "first_name": "Hewad", "last_name": "Ahmadzai",
        "display_name": "Ahmadzai, Hewad", "review_note": None, "source_page": None,
    }


def test_roundtrip_through_dict():
    roster = Roster("C", "1", [Student("W1234567", "Hewad", "Ahmadzai", "check", 2)], 1, "stated", ["section"])
    assert Roster.from_dict(roster.to_dict()) == roster


def test_w_number_preserved_exactly():
    s = Student.from_dict({"w_number": "  W0123456 ", "first_name": "A", "last_name": "B"})
    assert s.w_number == "W0123456"  # trimmed only; leading W and leading zero kept
    s = Student.from_dict({"w_number": "w0123456", "first_name": "A", "last_name": "B"})
    assert s.w_number == "w0123456"  # questionable IDs are NOT silently "fixed"


@pytest.mark.parametrize("value,ok", [
    ("W1234567", True), ("W0000001", True), ("1234567", False), ("w1234567", False),
    ("W123456", False), ("W12345678", False), ("W 1234567", False), ("", False),
])
def test_w_number_format(value, ok):
    assert is_valid_w_number(value) is ok


def test_clean_text_keeps_spelling():
    assert clean_text("  O'Brien  Smith-Jones ") == "O'Brien Smith-Jones"
    assert clean_text("Nguyễn") == "Nguyễn"  # NFC composes, does not strip accents
    assert clean_text("JuvWan") == "JuvWan"  # unusual capitalization preserved


@pytest.mark.parametrize("bad", [
    None, [], {"students": "x"}, {"students": [{"w_number": 5}]},
    {"students": [], "expected_count": -1}, {"students": [], "expected_count": True},
    {"students": [{"first_name": "x" * 1000}]},
])
def test_from_dict_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        Roster.from_dict(bad)


def test_normalize_roster_is_idempotent():
    r = Roster("  PHIL  320 ", " 10141 ", [Student(" W1234567 ", " Hewad ", "Ahmadzai  ")])
    normalize_roster(r)
    snapshot = r.to_dict()
    normalize_roster(r)
    assert r.to_dict() == snapshot
    assert (r.course_title, r.section, r.students[0].w_number) == ("PHIL 320", "10141", "W1234567")


def test_output_stem_is_filesystem_safe():
    assert output_stem(Roster("PHIL 320 Logic", "10141")) == "PHIL_320_10141"
    assert output_stem(Roster("../../etc/passwd", "")) == "etc_passwd"
    assert output_stem(Roster("", "")) == "roster"


# --- Name splitting ----------------------------------------------------------

@pytest.mark.parametrize("text,first,last", [
    ("Ahmadzai, Hewad", "Hewad", "Ahmadzai"),
    ("Ahmadzai,Hewad", "Hewad", "Ahmadzai"),
    ("Garcia-Lopez, Maria Fernanda", "Maria Fernanda", "Garcia-Lopez"),  # hyphen
    ("O'Brien, Sean", "Sean", "O'Brien"),  # apostrophe
    ("de la Cruz, Isabella", "Isabella", "de la Cruz"),  # multi-part last name
    ("Van Der Berg, Pieter", "Pieter", "Van Der Berg"),
    ("Smith-Jones, Anna-Marie", "Anna-Marie", "Smith-Jones"),
    ("Johnson Jr., Michael", "Michael", "Johnson Jr."),  # suffix already with last name
    ("Johnson, Michael Jr.", "Michael", "Johnson Jr."),  # suffix moved to last name
    ("Johnson, Michael, Jr.", "Michael", "Johnson Jr."),
    ("Nguyễn, Thảo Vy", "Thảo Vy", "Nguyễn"),
    ("Vongphachanh-Rasavong, Khamphoui Alexander", "Khamphoui Alexander", "Vongphachanh-Rasavong"),
])
def test_last_first_names(text, first, last):
    split = split_name(text)
    assert (split.first, split.last, split.note) == (first, last, None)


@pytest.mark.parametrize("text,first,last", [
    ("Hewad Ahmadzai", "Hewad", "Ahmadzai"),
    ("JuvWan Andrews", "JuvWan", "Andrews"),
    ("Anna-Marie Smith-Jones", "Anna-Marie", "Smith-Jones"),
    ("Sean O'Brien", "Sean", "O'Brien"),
])
def test_first_last_two_part_names_are_unambiguous(text, first, last):
    split = split_name(text)
    assert (split.first, split.last, split.note) == (first, last, None)


@pytest.mark.parametrize("text,first,last", [
    ("Isabella de la Cruz", "Isabella", "de la Cruz"),  # particle starts last name
    ("Mary Ann Smith", "Mary Ann", "Smith"),
    ("Pieter van der Berg", "Pieter", "van der Berg"),
])
def test_first_last_multi_part_names_are_flagged(text, first, last):
    split = split_name(text)
    assert (split.first, split.last) == (first, last)
    assert split.note == NOTE_MULTI_PART  # never silently trusted


def test_first_last_suffix_goes_with_last_name():
    split = split_name("Michael Johnson Jr.")
    assert (split.first, split.last, split.note) == ("Michael", "Johnson Jr.", None)


def test_single_name_part_is_flagged():
    split = split_name("Madonna")
    assert (split.first, split.last, split.note) == ("", "Madonna", NOTE_ONE_PART)


def test_explicit_order_overrides_auto():
    assert split_name("Ahmadzai Hewad", "last_first").note is not None
    split = split_name("Hewad, Ahmadzai", "first_last")
    assert split.last == "Ahmadzai"  # comma ignored in first_last mode
