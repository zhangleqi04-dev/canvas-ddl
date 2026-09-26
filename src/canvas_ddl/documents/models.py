from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path


@dataclass(frozen=True)
class OfficialDocument:
    document_id: str
    course_id: str
    course_code: str
    course_name: str
    document_name: str
    document_kind: str
    source_url: str
    path: Path
    sha256: str
    approved_by: str
    valid_from: date
    valid_until: date
    auto_refresh: bool = False
    refresh_blocked: bool = False


@dataclass(frozen=True)
class DocumentDraft:
    """Download/scan input; these syntax bounds grant no source/period authority."""
    document_id: str
    course_id: str
    course_code: str
    course_name: str
    document_name: str
    document_kind: str
    source_url: str
    path: Path
    sha256: str
    valid_from: date = date.min
    valid_until: date = date.max


@dataclass(frozen=True)
class DocumentPage:
    page: int
    text: str
    extraction_mode: str = "layout"
    line_confidences: tuple[float, ...] = ()
    ocr_engine: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    sha256: str
    pages: tuple[DocumentPage, ...]
    parsed_at: datetime
    parser_version: str = "multi-format-ppocrv6-small-v4"


@dataclass(frozen=True)
class DeadlineCandidate:
    candidate_id: str
    document_id: str
    course_id: str
    title: str
    page: int
    evidence_text: str
    date_expression: str | None
    extractor: str = "rule-v1"
    location: str | None = None
    deadline_type: str | None = None
    value_kind: str | None = None
    semantic_status: str | None = None
    review_id: str | None = None
    semantic_reason: str | None = None


@dataclass(frozen=True)
class CandidateValidation:
    status: str
    reasons: tuple[str, ...]
    validated_at: datetime
    rule_version: str = "document-validator-v3"
    value_at: datetime | None = None
    value_kind: str | None = None
    date_only: bool = False


@dataclass(frozen=True)
class RelativeWeekEvidence:
    candidate: DeadlineCandidate
    validation: CandidateValidation
    document_name: str
    source_url: str
    document_sha256: str
    parsed_at: datetime
    extraction_mode: str
    ocr_engine: str | None
    ocr_confidence: float | None
    source_verified: bool
