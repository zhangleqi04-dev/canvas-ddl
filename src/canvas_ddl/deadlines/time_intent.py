"""Bounded semantic time input. Natural-language interpretation belongs to Codex."""
import json
import re
from dataclasses import dataclass, asdict
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from canvas_ddl.canvas.errors import ApplicationError


def invalid_intent():
    return ApplicationError("INVALID_TIME_INTENT", "Invalid structured time intent; use the documented schema.")


@dataclass(frozen=True)
class TimeIntent:
    kind: str
    original_text: str
    timezone: str | None = None
    offset: int | None = None
    days: int | None = None
    weekday_start: int | None = None  # Monday=0, Sunday=6
    weekday_end: int | None = None
    portion: str | None = None  # full or remaining (current period only)
    boundary: str | None = None  # before: day/week/month
    weekday: int | None = None  # before week boundary: Monday=0
    start_date: str | None = None
    end_date: str | None = None
    interpretation: str | None = None

    def __post_init__(self):
        common = {"kind", "original_text", "timezone", "interpretation"}
        fields = {
            "calendar_day": {"offset", "portion"},
            "calendar_week": {"offset", "weekday_start", "weekday_end", "portion"},
            "calendar_weekend": {"offset"},
            "calendar_month": {"offset", "days", "portion"},
            "rolling_days": {"days"},
            "date_range": {"start_date", "end_date"},
            "before": {"boundary", "offset", "weekday"},
        }
        if not isinstance(self.kind, str) or self.kind not in fields:
            raise invalid_intent()
        supplied = {k for k, v in asdict(self).items() if v is not None}
        if supplied - common - fields[self.kind]:
            raise invalid_intent()
        if not isinstance(self.original_text, str) or not self.original_text.strip() or len(self.original_text) > 2000:
            raise invalid_intent()
        if self.interpretation is not None and (not isinstance(self.interpretation, str) or
                                              not self.interpretation.strip() or len(self.interpretation) > 1000):
            raise invalid_intent()
        if self.timezone is not None:
            if not isinstance(self.timezone, str) or len(self.timezone) > 100:
                raise invalid_intent()
            try:
                ZoneInfo(self.timezone)
            except (ValueError, ZoneInfoNotFoundError):
                raise invalid_intent() from None

        def integer(value, low, high):
            if type(value) is not int or not low <= value <= high:
                raise invalid_intent()

        if self.kind.startswith("calendar_") or self.kind == "before":
            integer(self.offset, -366, 366)
        for value in (self.weekday_start, self.weekday_end, self.weekday):
            if value is not None:
                integer(value, 0, 6)
        if self.kind == "calendar_week":
            if (self.weekday_start is None) != (self.weekday_end is None):
                raise invalid_intent()
            if self.weekday_start is not None and self.weekday_start > self.weekday_end:
                raise invalid_intent()
        if self.portion is not None:
            if self.portion not in ("full", "remaining") or (self.portion == "remaining" and self.offset != 0):
                raise invalid_intent()
        if self.kind == "rolling_days":
            integer(self.days, 1, 366)
        elif self.days is not None:
            integer(self.days, 1, 31)
            if self.portion == "remaining":
                raise invalid_intent()
        if self.kind == "before":
            if self.boundary not in ("day", "week", "month") or (self.weekday is not None and self.boundary != "week"):
                raise invalid_intent()
        if self.kind == "date_range":
            for value in (self.start_date, self.end_date):
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise invalid_intent()
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise invalid_intent() from None

    def to_dict(self):
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() - cls.__dataclass_fields__.keys():
            raise invalid_intent()
        # Even irrelevant null fields are rejected, rather than silently ignored.
        if any(v is None for v in value.values()):
            raise invalid_intent()
        try:
            return cls(**value)
        except TypeError:
            raise invalid_intent() from None

    @classmethod
    def from_json(cls, value):
        def unique_keys(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise invalid_intent()
                result[key] = item
            return result
        if not isinstance(value, str) or len(value) > 8000:
            raise invalid_intent()
        try:
            return cls.from_dict(json.loads(value, object_pairs_hook=unique_keys))
        except (ValueError, TypeError, RecursionError):
            raise invalid_intent() from None
