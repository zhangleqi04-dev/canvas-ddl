"""Timezone-aware resolution of validated structured time inputs."""
from datetime import date, datetime, timedelta, time, timezone as utc_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from canvas_ddl.canvas.errors import ApplicationError
from .models import TimeRange
from .time_intent import TimeIntent, invalid_intent


def aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


class TimeRangeResolver:
    def __init__(self, timezone: str = "Asia/Singapore"):
        try:
            self.zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ApplicationError("INVALID_TIME_RANGE", "Unknown configured timezone.") from None
        self.timezone = timezone

    def resolve(self, expression: str | None, *, now: datetime,
                start: datetime | None = None, end: datetime | None = None,
                intent: TimeIntent | None = None) -> TimeRange:
        if not aware(now):
            raise ApplicationError("INVALID_TIME_RANGE", "The clock must be timezone-aware.")
        resolved_timezone = self.timezone
        if intent is not None:
            if not isinstance(intent, TimeIntent) or expression is not None or start is not None or end is not None:
                raise invalid_intent()
            # Validate direct API callers as well as JSON adapters.
            intent.__post_init__()
            resolved_timezone = intent.timezone or self.timezone
            try:
                start, end = self._resolve_intent(intent, now.astimezone(ZoneInfo(resolved_timezone)))
            except (OverflowError, ValueError):
                raise ApplicationError("INVALID_TIME_RANGE", "Time intent exceeds supported calendar bounds.") from None
            expression = intent.original_text
        elif start is not None or end is not None:
            if expression is not None or not aware(start) or not aware(end):
                raise ApplicationError("INVALID_TIME_RANGE", "Use TimeIntent or two timezone-aware timestamps.")
            start, end = start.astimezone(self.zone), end.astimezone(self.zone)
        else:
            # Natural-language interpretation belongs to the LLM/Skill adapter.
            # The former phrase whitelist and regex parser are intentionally
            # disabled so the engine cannot silently assign semantics to text.
            raise ApplicationError(
                "INVALID_TIME_INTENT",
                "Natural-language time must be normalized to TimeIntent before querying.",
            )
        if start.astimezone(utc_timezone.utc) > end.astimezone(utc_timezone.utc):
            raise ApplicationError("INVALID_TIME_RANGE", "Start must not be after end.")
        if end.astimezone(utc_timezone.utc) - start.astimezone(utc_timezone.utc) > timedelta(days=366):
            raise ApplicationError("INVALID_TIME_RANGE", "Query windows must not exceed 366 days.")
        return TimeRange(expression, start, end, resolved_timezone)

    @staticmethod
    def _resolve_intent(intent, now):
        today, zone = now.date(), now.tzinfo
        monday = today - timedelta(days=today.weekday())

        def midnight(day):
            return datetime.combine(day, time.min, zone)

        def month(offset):
            index = today.year * 12 + today.month - 1 + offset
            year, zero_month = divmod(index, 12)
            return date(year, zero_month + 1, 1)

        if intent.kind == "rolling_days":
            return now, (now.astimezone(utc_timezone.utc) + timedelta(days=intent.days)).astimezone(zone)
        if intent.kind == "before":
            if intent.boundary == "day":
                boundary = today + timedelta(days=intent.offset)
            elif intent.boundary == "week":
                boundary = monday + timedelta(weeks=intent.offset, days=intent.weekday or 0)
            else:
                boundary = month(intent.offset)
            end = (midnight(boundary).astimezone(utc_timezone.utc) - timedelta(microseconds=1)).astimezone(zone)
            return now, end
        if intent.kind == "calendar_day":
            first = today + timedelta(days=intent.offset)
            following = first + timedelta(days=1)
        elif intent.kind == "calendar_week":
            base = monday + timedelta(weeks=intent.offset)
            first = base + timedelta(days=intent.weekday_start or 0)
            following = base + timedelta(days=(intent.weekday_end if intent.weekday_end is not None else 6) + 1)
        elif intent.kind == "calendar_weekend":
            first = monday + timedelta(weeks=intent.offset, days=5)
            following = first + timedelta(days=2)
        elif intent.kind == "calendar_month":
            first, following = month(intent.offset), month(intent.offset + 1)
            if intent.days is not None:
                following = min(following, first + timedelta(days=intent.days))
        else:  # Validated date_range, for explicit user dates only.
            first = date.fromisoformat(intent.start_date)
            following = date.fromisoformat(intent.end_date) + timedelta(days=1)
        start = midnight(first)
        if intent.portion == "remaining":
            start = max(start, now)
        # Subtract in UTC so the inclusive end stays correct across offset changes.
        end = (midnight(following).astimezone(utc_timezone.utc) - timedelta(microseconds=1)).astimezone(zone)
        return start, end
