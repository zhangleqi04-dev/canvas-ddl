from dataclasses import dataclass
from datetime import datetime, date
from typing import Literal, Any
from canvas_ddl.courses.models import Course
from .time_intent import TimeIntent

DeadlineType = Literal["assignment", "exam", "quiz", "discussion", "event", "other"]
SubmissionStatus = Literal["submitted", "not_submitted", "late", "missing", "unknown"]
TYPES = ("assignment", "exam", "quiz", "discussion", "event", "other")
STATUSES = ("submitted", "not_submitted", "late", "missing", "unknown")


@dataclass(frozen=True)
class SourceReference:
    source_type: str
    canvas_resource_id: str | None
    source_url: str
    last_verified_at: datetime
    document_id: str | None = None
    document_name: str | None = None
    page: int | None = None
    evidence_text: str | None = None
    document_sha256: str | None = None
    ingested_at: datetime | None = None
    validation: "ValidationInfo | None" = None
    value_at: datetime | None = None
    value_kind: str | None = None
    date_only: bool = False
    extraction_mode: str | None = None
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    location: str | None = None
    source_authority: str | None = None


@dataclass(frozen=True)
class ValidationInfo:
    status: str
    rule_version: str
    validated_at: datetime
    checks: tuple[str, ...]


@dataclass(frozen=True)
class SourceConflict:
    field: str
    canonical_value: str | None
    alternative_value: str | None
    alternative_source: SourceReference
    resolution: str


@dataclass(frozen=True)
class Deadline:
    deadline_id: str
    canvas_resource_id: str | None
    course_id: str
    course_code: str
    course_name: str
    title: str
    type: DeadlineType
    start_at: datetime | None
    due_at: datetime | None
    end_at: datetime | None
    submission_status: SubmissionStatus
    source_type: str
    source_url: str
    last_verified_at: datetime
    sources: tuple[SourceReference, ...] = ()
    all_day_date: date | None = None
    date_only: bool = False
    # Availability is not an actionable deadline.
    unlock_at: datetime | None = None
    lock_at: datetime | None = None
    # Internal domain signals; not raw Canvas payloads or model-facing fields.
    identities: tuple[str, ...] = ()
    explicit_type: str | None = None
    submission_types: tuple[str, ...] = ()
    authoritative_dates: bool = False
    validation: ValidationInfo | None = None
    conflicts: tuple[SourceConflict, ...] = ()
    reconciliation_status: str = "single_source"
    canonical_reason: str = "structured_canvas"

    @property
    def actionable_at(self) -> datetime | None:
        return self.due_at or self.start_at or self.end_at


@dataclass(frozen=True)
class ReferenceDeadline:
    """High-recall schedule evidence that has a bounded window but no exact fact."""
    reference_id: str
    course_id: str
    course_code: str
    course_name: str
    title: str
    type: DeadlineType
    window_start_at: datetime
    window_end_at: datetime
    date_precision: str
    resolution: str
    confidence: str
    submission_status: SubmissionStatus
    sources: tuple[SourceReference, ...]
    validation: ValidationInfo


@dataclass(frozen=True)
class TimeRange:
    expression: str | None
    start: datetime
    end: datetime
    timezone: str


@dataclass(frozen=True)
class ResolvedDeadlineQuery:
    time_range: TimeRange
    course: str | None
    course_ids: tuple[str, ...]
    types: tuple[str, ...] | None
    submission_statuses: tuple[str, ...] | None
    limit: int | None
    time_intent: TimeIntent | None = None


@dataclass(frozen=True)
class Coverage:
    course_id: str
    source_type: str
    state: str
    record_count: int | None
    last_verified_at: datetime
    error_code: str | None = None


@dataclass(frozen=True)
class DeadlineQueryResult:
    query: ResolvedDeadlineQuery
    count: int
    deadlines: tuple[Deadline, ...]
    generated_at: datetime
    data_freshness: str
    warnings: tuple[str, ...] = ()
    coverage: tuple[Coverage, ...] = ()
    complete: bool = True
    matched_count: int = 0
    course_counts: tuple[tuple[str, str, int], ...] = ()
    document_summary: tuple[dict[str, Any], ...] = ()
    unresolved_deadlines: tuple[Deadline, ...] = ()
    file_library_check: dict[str, Any] | None = None
    document_content_matches: tuple[dict[str, Any], ...] = ()
    reference_deadlines: tuple[ReferenceDeadline, ...] = ()
    reference_count: int = 0

    def __post_init__(self):
        if self.count != len(self.deadlines):
            raise ValueError("Count must equal returned deadlines.")
        if self.reference_count != len(self.reference_deadlines):
            raise ValueError("Reference count must equal returned reference deadlines.")


@dataclass(frozen=True)
class RawRecord:
    source_type: str
    course: Course
    payload: dict[str, Any]
    verified_at: datetime
