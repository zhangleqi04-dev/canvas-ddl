from datetime import datetime

from canvas_ddl.courses.models import Course
from canvas_ddl.deadlines.models import RawRecord
from canvas_ddl.deadlines.teaching_weeks import TeachingWeekResolver
from canvas_ddl.collectors.calendar_feed import CourseCalendarFeedCollector


COURSE = Course("11005", "COURSE105", "Nonlinear Optimization")
NOW = datetime.fromisoformat("2026-09-19T10:00:00+08:00")


def event(identifier, title, start_at, **extra):
    return RawRecord("canvas_calendar_event", COURSE,
                     {"id": identifier, "title": title, "start_at": start_at, **extra}, NOW)


def test_course_start_and_recess_build_actual_teaching_week_sequence():
    resolver = TeachingWeekResolver("https://canvas.example")
    mappings, warnings = resolver.build([
        event(1, "First lecture", "2026-08-11T10:00:00+08:00"),
        event(2, "No class (Recess Week)", "2026-09-22T10:00:00+08:00"),
    ])
    week = mappings["11005"][7]
    assert week.start_date.isoformat() == "2026-09-28"
    assert week.end_date.isoformat() == "2026-10-04"
    assert week.resolution == "canvas_course_start_and_breaks" and not warnings
    _, first, last, precision = resolver.resolve("11005", "Midterm Exam: Week 7 Friday", mappings)
    assert first.date().isoformat() == last.date().isoformat() == "2026-10-02"
    assert precision == "day"


def test_explicit_week_calendar_event_is_high_confidence():
    resolver = TeachingWeekResolver("https://canvas.example")
    mappings, _ = resolver.build([event(7, "COURSE105 Week 7 lecture", "2026-09-30T10:00:00+08:00")])
    assert mappings["11005"][7].confidence == "high"
    resolved = resolver.resolve("11005", "Take-home Midterm (Week 7)", mappings)
    assert resolved[1].date().isoformat() == "2026-09-28"
    assert resolved[2].date().isoformat() == "2026-10-04"
    assert resolved[3] == "teaching_week"


def test_conflicting_explicit_and_start_derived_week_is_not_resolved():
    resolver = TeachingWeekResolver("https://canvas.example")
    mappings, warnings = resolver.build([
        event(1, "First lecture", "2026-08-11T10:00:00+08:00"),
        event(7, "Week 7 lecture", "2026-10-14T10:00:00+08:00"),
    ])
    assert 7 not in mappings["11005"]
    assert any("conflict" in warning for warning in warnings)


def test_no_course_start_or_week_label_does_not_guess():
    resolver = TeachingWeekResolver("https://canvas.example")
    mappings, _ = resolver.build([event(1, "Office hour", "2026-08-11T10:00:00+08:00")])
    assert resolver.resolve("11005", "Midterm Exam: Week 7 Friday", mappings) is None


def test_weekly_series_head_and_blackout_date_are_structured_calendar_signals():
    resolver = TeachingWeekResolver("https://canvas.example")
    mappings, _ = resolver.build([
        event(1, "COURSE105 Lecture", "2026-08-11T10:00:00+08:00",
              series_head=True, rrule="FREQ=WEEKLY;COUNT=13"),
        event(2, "University closure", "2026-09-22T10:00:00+08:00", blackout_date=True),
    ])
    assert mappings["11005"][7].start_date.isoformat() == "2026-09-28"


def test_official_course_ics_series_and_recess_map_week_seven():
    collector = CourseCalendarFeedCollector(object(), clock=lambda: NOW)
    rows = collector.parse(b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:lecture-series\r
DTSTART;TZID=Asia/Singapore:20260811T100000\r
SUMMARY:COURSE105 Lecture\r
RRULE:FREQ=WEEKLY;COUNT=13\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:recess\r
DTSTART;VALUE=DATE:20260922\r
SUMMARY:Recess Week\r
END:VEVENT\r
END:VCALENDAR\r
""", COURSE.course_id)
    raw = [RawRecord("canvas_calendar_feed", COURSE, row, NOW) for row in rows]
    mappings, warnings = TeachingWeekResolver("https://canvas.example").build(raw)
    assert not warnings
    week, first, last, precision = TeachingWeekResolver("https://canvas.example").resolve(
        COURSE.course_id, "Midterm Exam: Week 7 Friday", mappings)
    assert week.start_date.isoformat() == "2026-09-28"
    assert first.date().isoformat() == last.date().isoformat() == "2026-10-02"
    assert precision == "day" and week.sources[0].source_type == "canvas_calendar_feed"
