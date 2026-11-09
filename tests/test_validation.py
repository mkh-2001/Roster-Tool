from conftest import make_roster
from roster_tool.models import Student
from roster_tool.validation import ERROR, WARNING, blocking_errors, validate_roster


def by_code(roster):
    return {i.code: i for i in validate_roster(roster)}


def test_clean_roster_has_no_issues():
    assert validate_roster(make_roster()) == []


def test_empty_roster_is_an_error():
    assert by_code(make_roster(0))["no_students"].severity == ERROR


def test_missing_and_malformed_w_numbers():
    r = make_roster(2)
    r.students[0].w_number = ""
    r.students[1].w_number = "W12345"
    issues = by_code(r)
    assert issues["missing_w_number"].row == 0
    assert issues["malformed_w_number"].row == 1


def test_duplicates_flag_every_row_involved():
    r = make_roster(3)
    r.students[2].w_number = r.students[0].w_number
    dupes = [i for i in validate_roster(r) if i.code == "duplicate_w_number"]
    assert sorted(i.row for i in dupes) == [0, 2]
    assert all(i.severity == ERROR for i in dupes)


def test_empty_names_are_errors():
    r = make_roster(1)
    r.students[0].first_name = ""
    r.students[0].last_name = ""
    issues = by_code(r)
    assert issues["missing_first_name"].severity == ERROR
    assert issues["missing_last_name"].severity == ERROR


def test_suspicious_names_are_warnings():
    r = make_roster(2)
    r.students[0].last_name = "Sm1th"
    r.students[1].first_name = "=HYPERLINK"
    issues = by_code(r)
    assert issues["name_contains_digits"].severity == WARNING
    assert issues["name_unusual_start"].severity == WARNING
    assert blocking_errors(validate_roster(r)) == []


def test_count_mismatch_is_an_error_and_clears_when_fixed():
    r = make_roster(3, expected_count=4, expected_count_source="stated")
    assert by_code(r)["count_mismatch"].severity == ERROR
    r.students.append(Student("W7654321", "Neil", "Balbon"))
    assert "count_mismatch" not in by_code(r)


def test_review_note_is_a_warning():
    r = make_roster(1)
    r.students[0].review_note = "check split"
    issue = by_code(r)["needs_review"]
    assert issue.severity == WARNING and issue.row == 0


def test_messages_never_contain_student_data():
    r = make_roster(3, expected_count=9)
    r.students[0].w_number = "W12"
    r.students[1].w_number = r.students[2].w_number
    r.students[1].first_name = ""
    for issue in validate_roster(r):
        for s in r.students:
            for value in (s.w_number, s.first_name, s.last_name):
                if value:
                    assert value not in issue.message
