"""Resolve relative teaching weeks from auditable Canvas course-calendar evidence."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from .models import RawRecord, SourceReference, ValidationInfo


WEEK = re.compile(r"\bweek\s*([1-9]|[12]\d|3[0-2])\b|第\s*([1-9]|[12]\d|3[0-2])\s*周", re.I)
FIRST_CLASS = re.compile(
    r"\b(?:first|1st)\s+(?:class|lecture|lesson|seminar|tutorial|session)\b|"
    r"\b(?:class|course|lecture)\s+(?:starts?|begins?|commences?)\b|"
    r"第一次课|第一堂课|首次上课|课程开始|开课",
    re.I,
)
BREAK_WEEK = re.compile(r"\b(?:recess|reading|semester\s+break)\s+week\b|教学暂停周|阅读周|休息周", re.I)
WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "周一": 0, "星期一": 0, "周二": 1, "星期二": 1, "周三": 2, "星期三": 2,
    "周四": 3, "星期四": 3, "周五": 4, "星期五": 4, "周六": 5, "星期六": 5,
    "周日": 6, "星期日": 6, "周天": 6, "星期天": 6,
}


@dataclass(frozen=True)
class TeachingWeek:
    course_id: str
    week_number: int
    start_date: date
    end_date: date
    resolution: str
    confidence: str
    sources: tuple[SourceReference, ...]


class TeachingWeekResolver:
    """Build course-local Week N ranges without consulting an external calendar."""

    def __init__(self, base_url: str, timezone_name="Asia/Singapore"):
        self.base_url = base_url.rstrip("/")
        self.zone = ZoneInfo(timezone_name)

    @staticmethod
    def _text(payload):
        return " ".join(str(payload.get(key) or "") for key in ("title", "name", "description", "comments"))

    @staticmethod
    def _series_start(payload):
        if payload.get("series_head") is not True:
            return False
        recurrence = " ".join(str(payload.get(key) or "") for key in ("rrule", "series_natural_language"))
        return bool(re.search(r"FREQ=WEEKLY|\bweekly\b", recurrence, re.I))

    def _day(self, payload):
        if payload.get("all_day_date"):
            try:
                return date.fromisoformat(str(payload["all_day_date"]))
            except ValueError:
                return None
        value = payload.get("start_at") or payload.get("end_at")
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            return parsed.astimezone(self.zone).date()
        except (TypeError, ValueError):
            return None

    def _source(self, raw, day):
        payload = raw.payload
        identifier = str(payload.get("id") or "")
        value = datetime.combine(day, time.min, self.zone)
        source_url = (f"{self.base_url}/calendar?event_id={identifier}&include_contexts=course_{raw.course.course_id}"
                      if raw.source_type == "canvas_calendar_event" else
                      f"{self.base_url}/courses/{raw.course.course_id}")
        return SourceReference(
            raw.source_type, identifier or None, source_url,
            raw.verified_at, value_at=value, value_kind="calendar_anchor", date_only=True,
        )

    @staticmethod
    def relative_week_number(text):
        values = {int(a or b) for a, b in WEEK.findall(text)}
        return next(iter(values)) if len(values) == 1 else None

    @staticmethod
    def weekday(text):
        lowered = text.casefold()
        found = {number for label, number in WEEKDAYS.items() if label in lowered}
        return next(iter(found)) if len(found) == 1 else None

    def build(self, records: list[RawRecord]):
        by_course, warnings = {}, []
        grouped = {}
        for raw in records:
            if raw.source_type not in ("canvas_calendar_event", "canvas_calendar_feed"):
                continue
            day = self._day(raw.payload)
            if day is not None:
                grouped.setdefault(raw.course.course_id, []).append((raw, day, self._text(raw.payload)))

        for course_id, rows in grouped.items():
            direct = {}
            for raw, day, text_value in rows:
                week = self.relative_week_number(text_value)
                if week is not None:
                    monday = day - timedelta(days=day.weekday())
                    direct.setdefault(week, []).append((monday, self._source(raw, day)))

            explicit = {}
            conflicted = set()
            for week, values in direct.items():
                mondays = {monday for monday, _ in values}
                if len(mondays) != 1:
                    conflicted.add(week)
                    warnings.append(f"Course {course_id}: Canvas calendar gives conflicting ranges for Week {week}; it was not resolved.")
                    continue
                monday = next(iter(mondays))
                explicit[week] = TeachingWeek(course_id, week, monday, monday + timedelta(days=6),
                                              "explicit_canvas_calendar_week", "high",
                                              tuple(source for _, source in values))

            anchors = [(raw, day, text_value) for raw, day, text_value in rows
                       if self.relative_week_number(text_value) == 1 or FIRST_CLASS.search(text_value)
                       or self._series_start(raw.payload)]
            derived = {}
            if anchors:
                anchor_mondays = {day - timedelta(days=day.weekday()) for _, day, _ in anchors}
                if len(anchor_mondays) == 1:
                    anchor = next(iter(anchor_mondays))
                    anchor_sources = tuple(self._source(raw, day) for raw, day, _ in anchors)
                    breaks = {}
                    for raw, day, text_value in rows:
                        if raw.payload.get("blackout_date") is True or BREAK_WEEK.search(text_value):
                            monday = day - timedelta(days=day.weekday())
                            breaks.setdefault(monday, []).append(self._source(raw, day))
                    week_number, monday = 1, anchor
                    # A bounded semester-sized map prevents an accidental perpetual calendar.
                    while week_number <= 20 and monday <= anchor + timedelta(weeks=30):
                        if monday in breaks:
                            monday += timedelta(days=7)
                            continue
                        sources = anchor_sources + tuple(
                            source for break_monday in sorted(breaks) if break_monday < monday
                            for source in breaks[break_monday]
                        )
                        derived[week_number] = TeachingWeek(
                            course_id, week_number, monday, monday + timedelta(days=6),
                            "canvas_course_start_and_breaks", "medium", sources,
                        )
                        week_number += 1
                        monday += timedelta(days=7)
                else:
                    warnings.append(f"Course {course_id}: Canvas calendar has conflicting Week 1/course-start anchors.")

            combined = dict(derived)
            for week, value in explicit.items():
                inferred = combined.get(week)
                if inferred and inferred.start_date != value.start_date:
                    combined.pop(week, None)
                    warnings.append(f"Course {course_id}: explicit and inferred Canvas ranges conflict for Week {week}; it was not resolved.")
                elif week not in conflicted:
                    combined[week] = value
            by_course[course_id] = combined
        return by_course, tuple(warnings)

    def resolve(self, course_id, evidence_text, mappings):
        week_number = self.relative_week_number(evidence_text)
        if week_number is None:
            return None
        week = mappings.get(course_id, {}).get(week_number)
        if week is None:
            return None
        weekday = self.weekday(evidence_text)
        if weekday is None:
            first, last, precision = week.start_date, week.end_date, "teaching_week"
        else:
            first = last = week.start_date + timedelta(days=weekday)
            precision = "day"
        return week, datetime.combine(first, time.min, self.zone), datetime.combine(last, time.max, self.zone), precision
