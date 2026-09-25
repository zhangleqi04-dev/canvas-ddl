"""Independent literal-evidence checks; extractor confidence is not authority."""
import re
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from canvas_ddl.documents.extractor import DATE_PATTERN, MONTHS, evidence_title, ITEM_PATTERN
from canvas_ddl.documents.models import CandidateValidation
from canvas_ddl.canvas.errors import ApplicationError
from .models import Deadline, ValidationInfo
from .time_range import aware

UNCERTAIN = re.compile(r"\b(?:may|might|tentative|approximately|around|TBA|TBC|not|cancelled|canceled)\b|可能|待定|暂定|大约|取消|不在", re.I)
PAGE_QUALIFIER = re.compile(r"\b(?:tentative|provisional|preliminary|draft)\b|subject\s+to\s+change|暂定|草案", re.I)
RELATIVE_WEEK = re.compile(r"\bweek\s*\d+\b|第\s*\d+\s*周", re.I)
AVAILABILITY = re.compile(r"\b(?:release|released|unlock|opens?|available)\b|开放|发布", re.I)
DUE = re.compile(r"\b(?:due|deadline)\b|截止|提交截止", re.I)
SCHEDULED = re.compile(r"\b(?:exam|examination|midterm|mid-term|quiz|test|lecture|class|meeting)\b|考试|期中|小测|测验|课程安排", re.I)


def parse_date(text):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    chinese = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if chinese:
        return date(int(chinese[1]), int(chinese[2]), int(chinese[3]))
    parts = text.replace(",", "").split()
    if parts[0].isdecimal():
        day, month, year = parts
    else:
        month, day, year = parts
    number = next(i for i, m in enumerate(MONTHS, 1) if month.casefold() in (m.casefold(), m[:3].casefold()))
    return date(int(year), number, int(day))


class DeadlineValidator:
    def __init__(self, timezone="Asia/Singapore", *, ocr_min_confidence=0.90):
        self.zone = ZoneInfo(timezone)
        self.ocr_min_confidence = ocr_min_confidence

    def validate_candidate(self, candidate, parsed, document, *, now):
        def verdict(status, reason):
            return CandidateValidation(status, (reason,), now)
        if (candidate.document_id != document.document_id or candidate.course_id != document.course_id
                or parsed.document_id != document.document_id or parsed.sha256 != document.sha256):
            return verdict("rejected", "DOCUMENT_SCOPE_OR_HASH_MISMATCH")
        page_objects = {p.page: p for p in parsed.pages}
        pages = {number: page.text for number, page in page_objects.items()}
        text = candidate.evidence_text
        if (candidate.page not in pages or candidate.location != page_objects[candidate.page].location
                or not text or text not in pages[candidate.page]
                or text not in {line.strip() for line in pages.get(candidate.page, "").splitlines()}
                or len(text) > 4000 or "\n" in text or not re.fullmatch(r"[a-f0-9]{24}", candidate.candidate_id)
                or evidence_title(text) != candidate.title or not ITEM_PATTERN.search(candidate.title)):
            return verdict("rejected", "EVIDENCE_OR_TITLE_NOT_SUPPORTED")
        page = page_objects[candidate.page]
        if page.extraction_mode in ("ocr_unavailable", "ocr_failed"):
            return verdict("unresolved", "OCR_COVERAGE_INCOMPLETE")
        if page.extraction_mode == "ocr_ppocrv6_small":
            lines = [line.strip() for line in page.text.splitlines()]
            scores = page.line_confidences
            matching = [scores[i] for i, line in enumerate(lines) if line == text and i < len(scores)]
            if not matching or max(matching) < self.ocr_min_confidence:
                return verdict("unresolved", "OCR_CONFIDENCE_REQUIRES_REVIEW")
        if UNCERTAIN.search(text) or AVAILABILITY.search(text) or PAGE_QUALIFIER.search(pages[candidate.page]):
            return verdict("unresolved", "AMBIGUOUS_OR_NON_DEADLINE_TEXT")
        relative_weeks = {int(a or b) for a, b in re.findall(
            r"\bweek\s*([1-9]|[12]\d|3[0-2])\b|第\s*([1-9]|[12]\d|3[0-2])\s*周", text, re.I)}
        if relative_weeks:
            if len(relative_weeks) != 1:
                return verdict("unresolved", "RELATIVE_WEEK_AMBIGUOUS")
            if not (DUE.search(text) or SCHEDULED.search(candidate.title)):
                return verdict("unresolved", "DEADLINE_ROLE_REQUIRED")
            return verdict("unresolved", "CANVAS_TEACHING_WEEK_REQUIRED")
        dates = list(DATE_PATTERN.finditer(text))
        if len(dates) != 1 or candidate.date_expression != dates[0][0]:
            return verdict("unresolved", "EXPLICIT_UNIQUE_DATE_REQUIRED")
        if not (DUE.search(text) or SCHEDULED.search(candidate.title)):
            return verdict("unresolved", "DEADLINE_ROLE_REQUIRED")
        try:
            day = parse_date(dates[0][0])
            if not document.valid_from <= day <= document.valid_until:
                return verdict("rejected", "OUTSIDE_APPROVED_COURSE_PERIOD")
            times = list(re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text))
            # Ranges/multiple times, AM/PM or foreign timezone require review in
            # this strict first implementation, rather than guessing semantics.
            if len(times) > 1 or re.search(r"\b(?:am|pm|UTC|GMT|PST|EST|CET)\b|[+-]\d\d:\d\d", text, re.I):
                return verdict("unresolved", "TIME_FORMAT_REQUIRES_REVIEW")
            clock = time(int(times[0][1]), int(times[0][2])) if times else time.min
            value = datetime.combine(day, clock, self.zone)
        except (ValueError, StopIteration):
            return verdict("rejected", "INVALID_LITERAL_DATE_OR_TIME")
        checks = ("OFFICIAL_REGISTRY", "APPROVED_CONTENT_HASH", "COURSE_SCOPE", "EXACT_PAGE_EVIDENCE",
                  "EXPLICIT_DATE", "DEADLINE_ROLE", "APPROVED_COURSE_PERIOD")
        if page.extraction_mode == "ocr_ppocrv6_small":
            checks += ("OCR_PP_OCRV6_SMALL", "OCR_CONFIDENCE_THRESHOLD")
        return CandidateValidation("confirmed", checks,
                                   now, value_at=value, value_kind="due_at" if DUE.search(text) else "start_at", date_only=not times)

    def validate_deadline(self, deadline: Deadline, *, now) -> Deadline:
        if not deadline.actionable_at or any(not aware(v) for v in (deadline.start_at, deadline.due_at, deadline.end_at, deadline.last_verified_at) if v is not None):
            raise ApplicationError("INVALID_DEADLINE", "The normalized deadline lacks valid timezone-aware timing.")
        if deadline.source_type == "official_document":
            if not deadline.validation or deadline.validation.status != "confirmed" or any(
                not s.document_id or not s.document_name or not s.page or not s.evidence_text or not s.document_sha256
                or not s.validation or s.validation.status != "confirmed" for s in deadline.sources):
                raise ApplicationError("UNVALIDATED_DOCUMENT_DEADLINE", "A document proposal cannot become a fact without validation and provenance.")
            return deadline
        from dataclasses import replace
        return replace(deadline, validation=ValidationInfo("confirmed", "structured-validator-v1", now, ("STRUCTURED_CANVAS", "AWARE_TIMESTAMP", "COURSE_SCOPE")))
