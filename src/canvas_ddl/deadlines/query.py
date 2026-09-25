from dataclasses import dataclass
from datetime import datetime
from .time_intent import TimeIntent


@dataclass(frozen=True)
class DeadlineQuery:
    # Deprecated compatibility field. Raw language is rejected by TimeRangeResolver;
    # adapters must supply time_intent instead.
    time_expression: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    course_reference: str | None = None
    course_ids: tuple[str, ...] | None = None
    types: tuple[str, ...] | None = None
    submission_statuses: tuple[str, ...] | None = None
    limit: int | None = None
    document_mode: str = "auto"
    time_intent: TimeIntent | None = None
