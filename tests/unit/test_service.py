from datetime import datetime
from io import StringIO
from types import SimpleNamespace
import json
import pytest
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.serialization import encode
from canvas_ddl.cli.main import main

NOW = datetime.fromisoformat("2026-09-17T13:05:00+08:00")
COURSES = [Course("12345", "CS3244", "Machine Learning"), Course("54321", "ST3131", "Statistics")]
NEXT_WEEK = TimeIntent("calendar_week", "下周", offset=1)
TODAY = TimeIntent("calendar_day", "今天", offset=0)


class Repository:
    def list_courses(self):
        return COURSES


class Client:
    def __init__(self):
        self.calls = []
        self.fail = False

    def get(self, path, *, params=None):
        self.calls.append((path, params))
        if self.fail:
            raise ApplicationError("PERMISSION_DENIED", "Denied")
        return {"id": 5, "name": "Quiz 3", "due_at": "2026-09-24T01:00:00Z", "is_quiz_assignment": True}

    def close(self):
        pass


class Collector:
    def __init__(self, kind, rows=(), error=None):
        self.source_type, self.rows, self.error = kind, list(rows), error
        self.calls = []

    def collect(self, *, course_id, start, end):
        self.calls.append(course_id)
        if self.error:
            raise ApplicationError(self.error, "Unavailable")
        return self.rows if course_id == "12345" else []


def build(collectors, client=None):
    return DeadlineService(client or Client(), base_url="https://canvas.example", repository=Repository(),
                           collectors=collectors, clock=lambda: NOW)


def assignment(id_, name, when, **extra):
    return {"id": id_, "name": name, "due_at": when, **extra}


def dataset():
    return [Collector("canvas_assignment", [
        assignment(1, "Assignment 2", "2026-09-23T15:59:00Z", submission={"workflow_state": "unsubmitted"}),
        assignment(2, "Midterm Exam", "2026-09-22T06:00:00Z"),
        assignment(3, "Assignment 3", "2026-09-18T10:00:00Z"),
        assignment(4, "Quiz 3", "2026-09-25T02:00:00Z", quiz_id=9),
        assignment(6, "Discussion", "2026-09-19T12:00:00Z", submission_types=["discussion_topic"]),
        assignment(7, "No due", None),
    ]), Collector("canvas_calendar_event", [{"id": 10, "title": "Midterm Exam", "start_at": "2026-09-22T06:00:00Z"}]),
    Collector("canvas_quiz", [{"id": 9, "title": "Quiz 3", "assignment_id": 4, "due_at": "2026-09-20T00:00:00Z"}]),
    Collector("canvas_discussion"), Collector("canvas_calendar_assignment")]


@pytest.mark.parametrize("query,count,title", [
    (DeadlineQuery(time_intent=NEXT_WEEK, types=("assignment",)), 1, "Assignment 2"),
    (DeadlineQuery(time_intent=NEXT_WEEK, types=("exam",)), 1, "Midterm Exam"),
    (DeadlineQuery(time_intent=TimeIntent("rolling_days", "future", days=180), course_reference="CS3244", limit=1), 1, "Assignment 3"),
    (DeadlineQuery(time_intent=TimeIntent("calendar_weekend", "这个周末", offset=0)), 1, "Discussion"),
    (DeadlineQuery(time_intent=TimeIntent("rolling_days", "未来14天", days=14), types=("quiz",)), 1, "Quiz 3"),
    (DeadlineQuery(time_intent=TimeIntent("calendar_day", "明天", offset=1)), 1, "Assignment 3"),
])
def test_six_acceptance_queries(query, count, title):
    data = encode(build(dataset()).query(query))
    assert data["count"] == count == len(data["deadlines"])
    assert data["deadlines"][0]["title"] == title
    assert data["status"] == "ok" and data["data_freshness"] == "live"


def test_dedup_filter_sort_limit_and_course_counts():
    service = build(dataset())
    r = service.query(DeadlineQuery(time_intent=TimeIntent("rolling_days", "未来14天", days=14), limit=2))
    assert r.count == 2 and r.matched_count == 5
    assert r.deadlines[0].title == "Assignment 3"
    assert r.course_counts[0] == ("12345", "CS3244", 5)
    r = service.query(DeadlineQuery(time_intent=NEXT_WEEK, types=("exam",)))
    assert len(r.deadlines[0].sources) == 2
    r = service.query(DeadlineQuery(time_intent=NEXT_WEEK, submission_statuses=("not_submitted",)))
    assert r.count == 1 and r.deadlines[0].title == "Assignment 2"


def test_verified_zero_and_partial_zero_differ():
    r = encode(build([Collector("canvas_assignment")]).query(DeadlineQuery(time_intent=TODAY)))
    assert r["count"] == 0 and r["status"] == "ok" and r["complete"]
    r = encode(build([Collector("canvas_assignment"), Collector("canvas_quiz", error="PERMISSION_DENIED")]).query(DeadlineQuery(time_intent=TODAY)))
    assert r["count"] == 0 and r["status"] == "partial" and not r["complete"]
    assert r["data_freshness"] == "live_partial" and r["warnings"]
    assert r["coverage"][1]["record_count"] is None


@pytest.mark.parametrize("error", ["CANVAS_UNAVAILABLE", "PERMISSION_DENIED", "SOURCE_UNAVAILABLE"])
def test_total_failure_never_uses_cache(error):
    service = build([Collector("canvas_assignment", error=error)])
    service.stale_cache = [{"title": "Old deadline"}]
    with pytest.raises(ApplicationError, match="None of"):
        service.query(DeadlineQuery(time_intent=TODAY))


def test_authentication_failure_is_fatal():
    service = build([Collector("canvas_assignment"), Collector("canvas_quiz", error="CANVAS_AUTH_FAILED")])
    with pytest.raises(ApplicationError) as error:
        service.query(DeadlineQuery(time_intent=TODAY))
    assert error.value.code == "CANVAS_AUTH_FAILED"


def test_query_scoped_course_and_assignment_plan():
    collectors = dataset()
    build(collectors).query(DeadlineQuery(time_intent=NEXT_WEEK, course_reference="CS3244", types=("assignment",)))
    assert collectors[0].calls == ["12345"] and collectors[1].calls == [] and collectors[2].calls == []


def test_personal_hydration_and_failure():
    c = Client()
    quiz = Collector("canvas_quiz", [{"id": 9, "assignment_id": 5, "title": "Quiz 3", "due_at": "2026-09-20T00:00:00Z"}])
    r = build([quiz], c).query(DeadlineQuery(time_intent=NEXT_WEEK, types=("quiz",)))
    assert r.count == 1 and r.deadlines[0].due_at.isoformat() == "2026-09-24T09:00:00+08:00"
    assert c.calls[0][1]["override_assignment_dates"] == "true"
    c.fail = True
    r = build([quiz], c).query(DeadlineQuery(time_intent=NEXT_WEEK))
    assert not r.complete and r.count == 0


def test_malformed_date_is_partial_and_null_is_not():
    rows = [assignment(1, "bad", "bad"), assignment(2, "no date", None)]
    r = build([Collector("canvas_assignment", rows)]).query(DeadlineQuery(time_intent=NEXT_WEEK))
    assert not r.complete and r.count == 0 and len(r.warnings) == 1


def test_all_day_today_in_rolling_window():
    c = Collector("canvas_calendar_event", [{"id": 1, "title": "Test 1", "all_day": True, "all_day_date": "2026-09-17"}])
    r = build([c]).upcoming(days=7)
    assert r.count == 1 and r.deadlines[0].date_only


@pytest.mark.parametrize("query", [
    DeadlineQuery(time_intent=NEXT_WEEK, types=("made_up",)), DeadlineQuery(time_intent=NEXT_WEEK, limit=0),
    DeadlineQuery(time_intent=NEXT_WEEK, submission_statuses=("overdue",)),
    DeadlineQuery(time_intent=NEXT_WEEK, course_ids=("999",)),
    DeadlineQuery(time_intent=NEXT_WEEK, course_ids=()),
])
def test_invalid_queries(query):
    with pytest.raises(ApplicationError):
        build(dataset()).query(query)


def test_cli_json_and_secret_redaction_even_unexpected_error():
    token = "fixture-secret-never-a-real-token"
    c = Collector("canvas_assignment", [assignment(1, token, "2026-09-23T00:00:00Z")])
    service = build([c])
    output = StringIO()
    intent = json.dumps(NEXT_WEEK.to_dict(), ensure_ascii=False)
    code = main(["deadlines", "--time-intent", intent],
                factory=lambda _: (service, SimpleNamespace(token=token)), stdout=output)
    assert code == 0 and token not in output.getvalue()
    assert json.loads(output.getvalue())["deadlines"][0]["title"] == "[REDACTED]"
    def broken(_):
        raise RuntimeError(token)
    output = StringIO()
    assert main(["courses"], factory=broken, stdout=output) == 2
    assert token not in output.getvalue() and json.loads(output.getvalue())["error"]["code"] == "INTERNAL_ERROR"


def test_cli_bad_args_never_echo_user_values():
    output = StringIO()
    assert main(["deadlines", "--bad-secret-argument"], stdout=output) == 2
    assert "bad-secret-argument" not in output.getvalue()


def test_details_current_submission_and_scope():
    class DetailsClient(Client):
        def get(self, path, *, params=None):
            if "calendar_events" in path:
                return {"id": 6, "context_code": "course_999", "start_at": "2026-09-23T00:00:00Z"}
            return {"id": 1, "name": "Assignment 2", "due_at": "2026-09-24T01:00:00Z", "submission": {"workflow_state": "submitted"}}
    service = build([], DetailsClient())
    d = service.get_deadline("course_12345_assignment_1")
    assert d.submission_status == "submitted" and d.due_at.day == 24
    with pytest.raises(ApplicationError) as error:
        service.get_deadline("course_12345_calendar_event_6")
    assert error.value.code == "PERMISSION_DENIED"
