"""Bounded parser for the official course ICS link returned by Canvas."""
from datetime import datetime
import hashlib
import re
from zoneinfo import ZoneInfo

from canvas_ddl.canvas.errors import ApplicationError


class CourseCalendarFeedCollector:
    source_type = "canvas_calendar_feed"

    def __init__(self, client, *, clock, timezone_name="Asia/Singapore"):
        self.client, self.clock = client, clock
        self.zone = ZoneInfo(timezone_name)

    def collect(self, *, course_id):
        course = self.client.get(f"/api/v1/courses/{course_id}")
        if not isinstance(course, dict) or str(course.get("id")) != str(course_id):
            raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned an unexpected course calendar record.")
        url = (course.get("calendar") or {}).get("ics")
        if not url:
            return []
        return self.parse(self.client.download_calendar_feed(str(url)), course_id)

    def parse(self, content, course_id):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApplicationError("CANVAS_CALENDAR_FEED_UNAVAILABLE", "The Canvas course calendar feed is not valid UTF-8.") from None
        physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = []
        for line in physical:
            if line.startswith((" ", "\t")) and lines:
                lines[-1] += line[1:]
            else:
                lines.append(line)
        events, current = [], None
        for line in lines:
            if line == "BEGIN:VEVENT":
                current = {}
                continue
            if line == "END:VEVENT":
                if current is not None:
                    event = self._event(current, course_id)
                    if event:
                        events.append(event)
                current = None
                continue
            if current is None or ":" not in line:
                continue
            key, value = line.split(":", 1)
            current.setdefault(key, []).append(value)
        if len(events) > 10000:
            raise ApplicationError("CANVAS_CALENDAR_FEED_UNAVAILABLE", "The Canvas course calendar feed exceeded the event limit.")
        return events

    @staticmethod
    def _text(value):
        return value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")

    @staticmethod
    def _values(fields, name):
        return tuple(value for key, values in fields.items()
                     if key.split(";", 1)[0] == name for value in values)

    def _date_value(self, fields):
        matches = [(key, values[0]) for key, values in fields.items()
                   if key.split(";", 1)[0] == "DTSTART" and values]
        if len(matches) != 1:
            return {}
        key, value = matches[0]
        try:
            if "VALUE=DATE" in key or re.fullmatch(r"\d{8}", value):
                day = datetime.strptime(value, "%Y%m%d").date()
                return {"all_day": True, "all_day_date": day.isoformat()}
            if value.endswith("Z"):
                parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC"))
            else:
                timezone_match = re.search(r"TZID=([^;:]+)", key)
                zone = ZoneInfo(timezone_match[1]) if timezone_match else self.zone
                parsed = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone)
            return {"start_at": parsed.isoformat()}
        except (ValueError, KeyError):
            return {}

    def _event(self, fields, course_id):
        timing = self._date_value(fields)
        if not timing:
            return None
        summary = self._text((self._values(fields, "SUMMARY") or ("",))[0])
        description = self._text((self._values(fields, "DESCRIPTION") or ("",))[0])
        uid = (self._values(fields, "UID") or (summary + repr(timing),))[0]
        rrule = (self._values(fields, "RRULE") or (None,))[0]
        categories = " ".join(self._values(fields, "CATEGORIES"))
        identifier = "ics-" + hashlib.sha256(uid.encode("utf8")).hexdigest()[:20]
        return {"id": identifier, "title": summary or "Course calendar event",
                "description": description, "context_code": f"course_{course_id}",
                "rrule": rrule, "series_head": bool(rrule),
                "blackout_date": bool(re.search(r"recess|reading|break|holiday|blackout", categories + " " + summary, re.I)),
                **timing}
