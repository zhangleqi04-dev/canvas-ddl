from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
import json
import pytest
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.cli.main import main
from canvas_ddl.courses.models import Course
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.deadlines.time_range import TimeRangeResolver
from canvas_ddl.serialization import encode

NOW = datetime.fromisoformat("2026-09-18T18:30:00+08:00")


def resolve(kind, *, now=NOW, **params):
    return TimeRangeResolver().resolve(None, now=now, intent=TimeIntent(kind, "用户的时间描述", **params))


@pytest.mark.parametrize("kind,params,first,last", [
    ("calendar_day", {"offset": 1}, "2026-09-19", "2026-09-19"),
    ("calendar_week", {"offset": 2}, "2026-09-28", "2026-10-04"),
    ("calendar_week", {"offset": 1, "weekday_start": 0, "weekday_end": 2}, "2026-09-21", "2026-09-23"),
    ("calendar_week", {"offset": 0, "weekday_start": 4, "weekday_end": 4}, "2026-09-18", "2026-09-18"),
    ("calendar_weekend", {"offset": 1}, "2026-09-26", "2026-09-27"),
    ("calendar_month", {"offset": 1}, "2026-10-01", "2026-10-31"),
    ("calendar_month", {"offset": 1, "days": 14}, "2026-10-01", "2026-10-14"),
    ("date_range", {"start_date": "2026-10-03", "end_date": "2026-10-05"}, "2026-10-03", "2026-10-05"),
])
def test_structured_calendar_spans(kind, params, first, last):
    r = resolve(kind, **params)
    assert r.start.isoformat() == first + "T00:00:00+08:00"
    assert r.end.isoformat() == last + "T23:59:59.999999+08:00"


def test_year_leap_month_clipping_and_timezone():
    r = resolve("calendar_month", now=datetime.fromisoformat("2023-12-31T23:59:00+08:00"), offset=2, days=31)
    assert r.start.date().isoformat() == "2024-02-01"
    assert r.end.date().isoformat() == "2024-02-29"
    r = resolve("calendar_week", now=datetime.fromisoformat("2026-12-31T23:59:00+08:00"), offset=2)
    assert r.start.date().isoformat() == "2027-01-11"
    r = resolve("calendar_day", now=datetime.fromisoformat("2026-09-20T16:30:00Z"), offset=0, timezone="Asia/Hong_Kong")
    assert r.timezone == "Asia/Hong_Kong" and r.start.date().isoformat() == "2026-09-21"


@pytest.mark.parametrize("now,hours", [("2026-03-08T12:00:00-04:00", 23), ("2026-11-01T12:00:00-05:00", 25)])
def test_dst_calendar_day_and_elapsed_days(now, hours):
    clock = datetime.fromisoformat(now)
    day = resolve("calendar_day", now=clock, offset=0, timezone="America/New_York")
    assert day.end.astimezone(timezone.utc) - day.start.astimezone(timezone.utc) == timedelta(hours=hours, microseconds=-1)
    rolling = resolve("rolling_days", now=clock, days=14, timezone="America/New_York")
    assert rolling.start == clock
    assert rolling.end.astimezone(timezone.utc) - rolling.start.astimezone(timezone.utc) == timedelta(days=14)


def test_before_and_remaining_semantics():
    r = resolve("before", boundary="week", offset=1)
    assert r.start == NOW and r.end.isoformat() == "2026-09-20T23:59:59.999999+08:00"
    r = resolve("before", boundary="week", offset=1, weekday=4)
    assert r.end.isoformat() == "2026-09-24T23:59:59.999999+08:00"
    r = resolve("calendar_month", offset=0, portion="remaining")
    assert r.start == NOW and r.end.date().isoformat() == "2026-09-30"
    with pytest.raises(ApplicationError):
        resolve("before", boundary="day", offset=0)
    with pytest.raises(ApplicationError):
        resolve("calendar_week", offset=0, weekday_start=0, weekday_end=1, portion="remaining")


@pytest.mark.parametrize("value", [
    {}, [], None,
    {"kind": "academic_week", "offset": 7},
    {"kind": "calendar_week", "offset": True},
    {"kind": "calendar_week", "offset": 1.5},
    {"kind": "calendar_week", "offset": 367},
    {"kind": "calendar_week", "offset": 1, "weekday_start": 2},
    {"kind": "calendar_week", "offset": 1, "weekday_start": 4, "weekday_end": 2},
    {"kind": "calendar_week", "offset": 1, "weekday_start": -1, "weekday_end": 7},
    {"kind": "calendar_week", "offset": 1, "days": 7},
    {"kind": "calendar_week", "offset": 1, "portion": "remaining"},
    {"kind": "calendar_day", "offset": 0, "timezone": "bad/zone"},
    {"kind": "calendar_day", "offset": 0, "timezone": "../UTC"},
    {"kind": "rolling_days", "days": 0},
    {"kind": "rolling_days", "days": 367},
    {"kind": "calendar_month", "offset": 0, "days": 10, "portion": "remaining"},
    {"kind": "before", "boundary": "exam", "offset": 1},
    {"kind": "before", "boundary": "month", "offset": 1, "weekday": 1},
    {"kind": "date_range", "start_date": "2026-02-30", "end_date": "2026-03-01"},
    {"kind": "date_range", "start_date": "20260918", "end_date": "2026-09-19"},
    {"kind": "rolling_days", "days": 7, "code": "execute"},
    {"kind": "rolling_days", "days": 7, "timezone": None},
    {"kind": "rolling_days", "days": 7, "original_text": ""},
])
def test_invalid_schema(value):
    if isinstance(value, dict):
        value = {"original_text": "用户描述", **value}
    with pytest.raises(ApplicationError) as e:
        TimeIntent.from_dict(value)
    assert e.value.code == "INVALID_TIME_INTENT"


@pytest.mark.parametrize("text", ['{"kind":"rolling_days","days":7,"days":8,"original_text":"x"}', "not JSON", "[]", "x" * 8001])
def test_bad_json_before_factory_and_no_input_echo(text):
    output = StringIO()
    def forbidden(_):
        pytest.fail("Invalid intent must not build the client or access credentials")
    assert main(["deadlines", "--time-intent", text], factory=forbidden, stdout=output) == 2
    assert json.loads(output.getvalue())["error"]["code"] == "INVALID_TIME_INTENT"
    assert text not in output.getvalue()


def test_range_bounds_and_exclusive_modes():
    intent = TimeIntent("calendar_week", "下周", offset=1)
    for options in ({"expression": "下周"}, {"start": NOW}, {"end": NOW}):
        with pytest.raises(ApplicationError):
            TimeRangeResolver().resolve(options.pop("expression", None), now=NOW, intent=intent, **options)
    for first, last in [("2026-09-19", "2026-09-18"), ("2026-01-01", "2027-01-02")]:
        with pytest.raises(ApplicationError):
            resolve("date_range", start_date=first, end_date=last)
    with pytest.raises(ApplicationError):
        resolve("calendar_day", now=datetime(2026, 1, 1), offset=0)
    out = StringIO()
    assert main(["deadlines", "--time-intent", json.dumps(intent.to_dict()), "--range", "下周"],
                factory=lambda _: pytest.fail("Mixed time modes must fail before factory"), stdout=out) == 2


class Repository:
    def __init__(self):
        self.calls = 0

    def list_courses(self):
        self.calls += 1
        return [Course("1", "COURSE103", "Analytics")]


class Collector:
    source_type = "canvas_assignment"

    def collect(self, *, course_id, start, end):
        return [{"id": 5, "name": "Assignment 2", "due_at": "2026-09-30T15:59:59Z"},
                {"id": 6, "name": "Assignment 3", "due_at": "2026-10-05T15:59:59Z"}]


def service():
    return DeadlineService(SimpleNamespace(close=lambda: None), base_url="https://canvas.example",
                           repository=Repository(), collectors=[Collector()], clock=lambda: NOW)


def test_cli_intent_filters_and_echoes_resolved_range_with_redaction():
    intent = TimeIntent("calendar_week", "下下周 fixture-token", offset=2, interpretation="两周后的完整自然周")
    out = StringIO()
    assert main(["deadlines", "--time-intent", json.dumps(intent.to_dict()), "--type", "assignment"],
                factory=lambda _: (service(), SimpleNamespace(token="fixture-token")), stdout=out) == 0
    result = json.loads(out.getvalue())
    assert result["count"] == 1 == result["matched_count"] == len(result["deadlines"])
    assert result["deadlines"][0]["title"] == "Assignment 2"
    assert result["query"]["start"] == "2026-09-28T00:00:00+08:00"
    assert result["query"]["end"] == "2026-10-04T23:59:59.999999+08:00"
    assert result["query"]["time_intent"]["offset"] == 2
    assert "fixture-token" not in out.getvalue()


def test_intent_semantics_ignore_original_text_and_legacy_text_is_rejected():
    structured = service().query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "任意自然语言", offset=2)))
    assert structured.query.time_range.start.date().isoformat() == "2026-09-28"
    with pytest.raises(ApplicationError) as error:
        service().query(DeadlineQuery(time_expression="下下周"))
    assert error.value.code == "INVALID_TIME_INTENT"


def test_invalid_time_never_refreshes_or_fetches_courses():
    s = service()
    s.document_refresher = SimpleNamespace(refresh=lambda _: pytest.fail("Must not refresh files"))
    with pytest.raises(ApplicationError):
        s.query(DeadlineQuery(time_expression="下周", time_intent=TimeIntent("calendar_week", "下周", offset=1),
                              types=("exam",), document_mode="refresh"))
    assert s.repository.calls == 0


def test_refresh_midnight_rollover_keeps_initial_time_range():
    s = service()
    clock = [datetime.fromisoformat("2026-09-20T23:59:59+08:00")]
    s.clock = lambda: clock[0]
    def refresh(_):
        clock[0] = datetime.fromisoformat("2026-09-21T00:00:01+08:00")
        return {"complete": True}
    s.document_refresher = SimpleNamespace(refresh=refresh)
    result = s.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下下周", offset=2), document_mode="refresh"))
    assert result.query.time_range.start.date().isoformat() == "2026-09-28"
    assert result.count == 1


def test_cli_intent_file_and_bad_files(tmp_path):
    path = tmp_path / "意图.json"
    path.write_text(json.dumps(TimeIntent("calendar_week", "下下周", offset=2).to_dict(), ensure_ascii=False), encoding="utf-8-sig")
    out = StringIO()
    assert main(["deadlines", "--time-intent-file", str(path)],
                factory=lambda _: (service(), SimpleNamespace(token="")), stdout=out) == 0
    assert json.loads(out.getvalue())["count"] == 1
    for content in (b"x" * 32001, b"\xff", b"not JSON"):
        path.write_bytes(content)
        out = StringIO()
        assert main(["deadlines", "--time-intent-file", str(path)],
                    factory=lambda _: pytest.fail("Bad file must fail before factory"), stdout=out) == 2
        assert str(path) not in out.getvalue()
    out = StringIO()
    assert main(["deadlines", "--time-intent", "{}", "--time-intent-file", str(path)],
                factory=lambda _: pytest.fail("Mixed adapters must fail before factory"), stdout=out) == 2
