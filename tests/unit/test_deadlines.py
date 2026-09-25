from dataclasses import replace
from datetime import datetime
import pytest
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.deadlines.models import RawRecord
from canvas_ddl.deadlines.normalizer import DeadlineNormalizer, submission_status
from canvas_ddl.deadlines.classifier import DeadlineClassifier
from canvas_ddl.deadlines.deduplicator import DeadlineDeduplicator

NOW = datetime.fromisoformat("2026-09-17T13:05:00+08:00")
COURSE = Course("12345", "CS3244", "Machine Learning")
NORMALIZER = DeadlineNormalizer("https://canvas.example", "Asia/Singapore")


def record(kind="canvas_assignment", **payload):
    return RawRecord(kind, COURSE, {"id": 8291, "name": "Assignment 2", "due_at": "2026-09-23T15:59:00Z", **payload}, NOW)


@pytest.mark.parametrize("title,type_", [("Midterm Exam", "exam"), ("Final Examination", "exam"),
    ("Practical Exam", "exam"), ("Oral exam", "exam"), ("Test 1", "exam"), ("Final", "exam"),
    ("Assignment 2", "assignment"), ("Quiz 3", "assignment"), ("Final Project", "assignment"),
    ("Latest version", "assignment"), ("Testing code", "assignment")])
def test_keywords(title, type_):
    d = NORMALIZER.normalize(record(name=title), {})
    assert DeadlineClassifier().classify(d).type == type_


def test_explicit_metadata_quiz_discussion_and_configuration():
    d = NORMALIZER.normalize(record(name="Midterm Exam", deadline_type="quiz"), {})
    assert DeadlineClassifier().classify(d).type == "quiz"
    for field, expected in [("online_quiz", "quiz"), ("discussion_topic", "discussion")]:
        d = NORMALIZER.normalize(record(submission_types=[field]), {})
        assert DeadlineClassifier().classify(d).type == expected
    d = NORMALIZER.normalize(record(name="CA1"), {})
    assert DeadlineClassifier(("CA1",)).classify(d).type == "exam"
    d = NORMALIZER.normalize(record(name="Final Project"), {})
    assert DeadlineClassifier(("final",)).classify(d).type == "assignment"
    d = NORMALIZER.normalize(record(is_quiz_assignment=True), {})
    assert DeadlineClassifier().classify(d).type == "quiz"


@pytest.mark.parametrize("submission,expected", [
    (None, "unknown"), ({"workflow_state": "unsubmitted"}, "not_submitted"),
    ({"workflow_state": "submitted"}, "submitted"),
    ({"workflow_state": "graded", "submitted_at": "2026-09-17T00:00:00Z"}, "submitted"),
    ({"workflow_state": "graded"}, "unknown"),
    ({"workflow_state": "unsubmitted", "missing": True}, "missing"),
    ({"workflow_state": "submitted", "late": True}, "late"),
    ({"workflow_state": "unsubmitted", "late": True}, "not_submitted"),
    ({"workflow_state": "unsubmitted", "excused": True}, "unknown"),
])
def test_status(submission, expected):
    assert submission_status(submission) == expected


def test_null_due_does_not_become_availability_deadline():
    assert NORMALIZER.normalize(record(due_at=None, unlock_at="2026-09-23T00:00:00Z", lock_at="2026-09-24T00:00:00Z"), {}) is None


def test_quiz_and_calendar_use_personal_override_including_null():
    personal = {("12345", "8291"): {"id": 8291, "due_at": "2026-09-24T01:00:00Z", "submission": {"workflow_state": "submitted"}}}
    raw = record("canvas_quiz", assignment_id=8291, title="Quiz 3")
    d = NORMALIZER.normalize(raw, personal)
    assert d.due_at.isoformat() == "2026-09-24T09:00:00+08:00" and d.submission_status == "submitted"
    personal[("12345", "8291")]["due_at"] = None
    assert NORMALIZER.normalize(raw, personal) is None
    raw = record("canvas_calendar_assignment", id="assignment_8291", start_at="2026-09-23T15:59:00Z", assignment=None)
    assert NORMALIZER.normalize(raw, personal) is None
    assert NORMALIZER.normalize(raw, {}).due_at.isoformat() == "2026-09-23T23:59:00+08:00"


def test_calendar_all_day_and_timezone():
    raw = record("canvas_calendar_event", name=None, title="Midterm", due_at=None,
                 start_at="2026-09-23T00:00:00Z", end_at=None, all_day=True, all_day_date="2026-09-23")
    d = NORMALIZER.normalize(raw, {})
    assert d.date_only and str(d.all_day_date) == "2026-09-23"
    assert d.start_at.isoformat() == "2026-09-23T00:00:00+08:00"
    assert d.source_url.startswith("https://canvas.example/calendar?")


@pytest.mark.parametrize("value", ["bad", "2026-09-23T00:00:00", 999])
def test_malformed_date(value):
    with pytest.raises(ApplicationError):
        NORMALIZER.normalize(record(due_at=value), {})


def exam(kind, identifier, when="2026-09-23T06:00:00Z", course=COURSE, title="Midterm Exam"):
    payload = {"id": identifier, "name": title, "title": title, "due_at": when}
    if kind == "canvas_calendar_event":
        payload.update(due_at=None, start_at=when)
    d = NORMALIZER.normalize(RawRecord(kind, course, payload, NOW), {})
    return DeadlineClassifier().classify(d)


def test_assignment_calendar_duplicate_and_provenance():
    unique = DeadlineDeduplicator().deduplicate([exam("canvas_assignment", 1), exam("canvas_calendar_event", 2)])
    assert len(unique) == 1 and len(unique[0].sources) == 2
    assert unique[0].source_type == "canvas_assignment"


def test_distinct_dates_courses_similar_titles_and_same_source():
    original = exam("canvas_assignment", 1)
    for other in [exam("canvas_calendar_event", 2, "2026-09-24T06:00:00Z"),
                  exam("canvas_calendar_event", 2, course=Course("9", "ST3131", "Statistics")),
                  exam("canvas_calendar_event", 2, title="Midterm Exam 2"), exam("canvas_assignment", 2)]:
        assert len(DeadlineDeduplicator().deduplicate([original, other])) == 2


def test_direct_link_and_override_precedence():
    a = replace(exam("canvas_assignment", 1), identities=("assignment:1",), submission_status="submitted")
    q = replace(exam("canvas_quiz", 99, "2026-09-22T00:00:00Z"), identities=("assignment:1", "quiz:99"))
    unique = DeadlineDeduplicator().deduplicate([q, a])
    assert len(unique) == 1 and unique[0].due_at == a.due_at
    assert unique[0].submission_status == "submitted" and len(unique[0].sources) == 2


def test_ambiguous_merge_does_not_bridge_distinct_assignments():
    records = [exam("canvas_assignment", 1), exam("canvas_assignment", 2), exam("canvas_calendar_event", 3)]
    assert len(DeadlineDeduplicator().deduplicate(records)) == 3

