from datetime import datetime, timedelta, timezone
import pytest
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.courses.resolver import CourseResolver
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.deadlines.time_range import TimeRangeResolver

NOW = datetime.fromisoformat("2026-09-17T13:05:00+08:00")


@pytest.mark.parametrize("kind,params,first,last", [
    ("calendar_day", {"offset": 0}, "2026-09-17", "2026-09-17"),
    ("calendar_day", {"offset": 1}, "2026-09-18", "2026-09-18"),
    ("calendar_week", {"offset": 0}, "2026-09-14", "2026-09-20"),
    ("calendar_week", {"offset": 1}, "2026-09-21", "2026-09-27"),
    ("calendar_week", {"offset": 2}, "2026-09-28", "2026-10-04"),
    ("calendar_weekend", {"offset": 0}, "2026-09-19", "2026-09-20"),
    ("calendar_month", {"offset": 0}, "2026-09-01", "2026-09-30"),
])
def test_calendar_ranges(kind, params, first, last):
    intent = TimeIntent(kind, "arbitrary LLM-understood wording", **params)
    result = TimeRangeResolver().resolve(None, now=NOW, intent=intent)
    assert result.start.date().isoformat() == first
    assert result.end.date().isoformat() == last
    assert result.start.hour == 0 and result.end.hour == 23
    assert result.end.microsecond == 999999
    assert result.timezone == "Asia/Singapore"


@pytest.mark.parametrize("days", [7, 14])
def test_rolling(days):
    r = TimeRangeResolver().resolve(None, now=NOW,
                                    intent=TimeIntent("rolling_days", "arbitrary wording", days=days))
    assert r.start == NOW and r.end - r.start == timedelta(days=days)


@pytest.mark.parametrize("now,kind,offset,first,last", [
    ("2026-12-31T23:59:59+08:00", "calendar_day", 1, "2027-01-01", "2027-01-01"),
    ("2024-02-28T23:59:59+08:00", "calendar_day", 1, "2024-02-29", "2024-02-29"),
    ("2026-09-30T23:59:59+08:00", "calendar_day", 1, "2026-10-01", "2026-10-01"),
    ("2026-12-31T23:59:59+08:00", "calendar_week", 1, "2027-01-04", "2027-01-10"),
    ("2026-09-20T23:59:59+08:00", "calendar_week", 1, "2026-09-21", "2026-09-27"),
    ("2026-12-31T23:59:59+08:00", "calendar_week", 2, "2027-01-11", "2027-01-17"),
    ("2026-09-20T23:59:59+08:00", "calendar_week", 2, "2026-09-28", "2026-10-04"),
    ("2026-09-20T16:00:00+00:00", "calendar_week", 2, "2026-10-05", "2026-10-11"),
])
def test_boundaries(now, kind, offset, first, last):
    r = TimeRangeResolver().resolve(None, now=datetime.fromisoformat(now),
                                    intent=TimeIntent(kind, "arbitrary wording", offset=offset))
    assert r.start.date().isoformat() == first and r.end.date().isoformat() == last


def test_dst_calendar_day_and_rolling_duration():
    resolver = TimeRangeResolver("America/New_York")
    now = datetime.fromisoformat("2026-03-08T00:00:00-05:00")
    day = resolver.resolve(None, now=now, intent=TimeIntent("calendar_day", "arbitrary wording", offset=0))
    assert day.end.astimezone(timezone.utc) - day.start.astimezone(timezone.utc) == timedelta(hours=23, microseconds=-1)
    roll = resolver.resolve(None, now=now, intent=TimeIntent("rolling_days", "arbitrary wording", days=7))
    assert roll.end.astimezone(timezone.utc) - roll.start.astimezone(timezone.utc) == timedelta(days=7)


def test_before_monday_and_explicit():
    resolver = TimeRangeResolver()
    r = resolver.resolve(None, now=NOW,
                         intent=TimeIntent("before", "arbitrary wording", boundary="week", offset=1))
    assert r.start == NOW and r.end.date().isoformat() == "2026-09-20"
    r = resolver.resolve(None, now=NOW, start=NOW, end=NOW)
    assert r.start == r.end


@pytest.mark.parametrize("expression", ["随便", "未来0天", "未来367天", "", None])
def test_natural_language_expression_path_is_disabled(expression):
    with pytest.raises(ApplicationError) as error:
        TimeRangeResolver().resolve(expression, now=NOW)
    assert error.value.code == "INVALID_TIME_INTENT"


def test_naive_reversed_invalid_zone():
    resolver = TimeRangeResolver()
    for start, end in [(datetime(2026, 1, 1), NOW), (NOW, NOW - timedelta(days=1))]:
        with pytest.raises(ApplicationError):
            resolver.resolve(None, now=NOW, start=start, end=end)
    with pytest.raises(ApplicationError):
        resolver.resolve("今天", now=datetime(2026, 1, 1))
    with pytest.raises(ApplicationError):
        TimeRangeResolver("Not/AZone")


COURSES = [Course("1", "CS3244", "Machine Learning"), Course("2", "ST3131", "Statistics")]


@pytest.mark.parametrize("reference", ["CS3244", "cs3244", " CS 3244 ", "Machine Learning", "machine learning", "1", "我的机器学习课"])
def test_course_resolution(reference):
    assert CourseResolver({"我的机器学习课": "1"}).resolve(reference, COURSES).course_id == "1"


def test_course_ambiguity_unknown_and_no_fuzzy_guess():
    with pytest.raises(ApplicationError) as error:
        CourseResolver().resolve("CS3244", COURSES + [Course("3", "CS3244", "Another term")])
    assert error.value.code == "AMBIGUOUS_COURSE" and len(error.value.candidates) == 2
    for ref in ["3244", "unknown"]:
        with pytest.raises(ApplicationError) as error:
            CourseResolver().resolve(ref, COURSES)
        assert error.value.code == "COURSE_NOT_FOUND"
