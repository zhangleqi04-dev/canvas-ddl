"""Independent literal-evidence checks; extractor confidence is not authority."""
import re
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from canvas_ddl.documents.extractor import DATE_PATTERN, MONTHS, evidence_title, ITEM_PATTERN
from canvas_ddl.documents.models import CandidateValidation
from canvas_ddl.documents.semantic import SCHEMA_VERSION
from canvas_ddl.canvas.errors import ApplicationError
from .models import Deadline, ValidationInfo, TYPES
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


def _month_number(value):
    cleaned = value.casefold().rstrip(".")
    return next((i for i, month in enumerate(MONTHS, 1)
                 if cleaned in (month.casefold(), month[:3].casefold())), None)


def _context_year(parsed, document):
    years = {int(value) for page in parsed.pages for value in re.findall(r"\b(?:19|20)\d{2}\b", page.text)}
    years.update(2000 + int(value) for page in parsed.pages
                 for value in re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-](\d{2})\b", page.text))
    if getattr(document, "source_authority", None) == "operator" and document.valid_from.year == document.valid_until.year:
        years.add(document.valid_from.year)
    return next(iter(years)) if len(years) == 1 else None


def grounded_source_dates(expression, parsed, document):
    """Return literal-compatible dates; Codex chooses semantics, Python checks the mapping."""
    if not expression:
        return set()
    text = expression.strip()
    match = DATE_PATTERN.fullmatch(text)
    if match:
        try:
            return {parse_date(match[0])}
        except (ValueError, StopIteration):
            return set()
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", text)
    if numeric:
        first, second, raw_year = map(int, numeric.groups())
        year = 2000 + raw_year if raw_year < 100 else raw_year
        result = set()
        for month, day in ((first, second), (second, first)):
            try:
                result.add(date(year, month, day))
            except ValueError:
                pass
        return result
    month_first = re.fullmatch(
        r"(" + "|".join(month + "|" + month[:3] for month in MONTHS) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?", text, re.I)
    day_first = re.fullmatch(
        r"(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(month + "|" + month[:3] for month in MONTHS) + r")\.?", text, re.I)
    year = _context_year(parsed, document)
    if year is None or not (month_first or day_first):
        return set()
    month_text, day_text = (month_first[1], month_first[2]) if month_first else (day_first[2], day_first[1])
    try:
        return {date(year, _month_number(month_text), int(day_text))}
    except (TypeError, ValueError):
        return set()


def grounded_source_times(text):
    result = set()
    for match in re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})\s*(am|pm)?(?!\w)", text, re.I):
        hour, minute, suffix = int(match[1]), int(match[2]), (match[3] or "").casefold()
        if minute > 59 or (suffix and not 1 <= hour <= 12) or (not suffix and hour > 23):
            continue
        if suffix:
            hour = hour % 12 + (12 if suffix == "pm" else 0)
        result.add(time(hour, minute))
    return result


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
        semantic = candidate.extractor == SCHEMA_VERSION
        location_matches = candidate.location == page_objects[candidate.page].location if candidate.page in page_objects else False
        if semantic and candidate.page in page_objects and candidate.location:
            location_matches = candidate.location == page_objects[candidate.page].location or candidate.location.startswith(
                f"{page_objects[candidate.page].location or f'unit {candidate.page}'}; chunk ")
        common_invalid = (candidate.page not in pages or not location_matches or not text
                          or text not in pages.get(candidate.page, "") or len(text) > 4000
                          or not re.fullmatch(r"[a-f0-9]{24}", candidate.candidate_id))
        semantic_invalid = semantic and (
            candidate.review_id is None or not re.fullmatch(r"[a-f0-9]{24}", candidate.review_id)
            or candidate.deadline_type not in TYPES or candidate.value_kind not in ("due_at", "start_at", "end_at")
            or candidate.semantic_status not in ("scheduled", "ambiguous")
            or candidate.title.casefold() not in text.casefold()
            or (candidate.normalized_date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate.normalized_date))
            or (candidate.normalized_time is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", candidate.normalized_time))
        )
        rule_invalid = not semantic and (
            text not in {line.strip() for line in pages.get(candidate.page, "").splitlines()}
            or "\n" in text or evidence_title(text) != candidate.title or not ITEM_PATTERN.search(candidate.title)
        )
        if common_invalid or semantic_invalid or rule_invalid:
            return verdict("rejected", "EVIDENCE_OR_TITLE_NOT_SUPPORTED")
        page = page_objects[candidate.page]
        if page.extraction_mode in ("ocr_unavailable", "ocr_failed"):
            return verdict("unresolved", "OCR_COVERAGE_INCOMPLETE")
        if page.extraction_mode == "ocr_ppocrv6_small":
            lines = [line.strip() for line in page.text.splitlines()]
            scores = page.line_confidences
            evidence_lines = {line.strip() for line in text.splitlines() if line.strip()}
            matching = [scores[i] for i, line in enumerate(lines) if line in evidence_lines and i < len(scores)]
            if not matching or max(matching) < self.ocr_min_confidence:
                return verdict("unresolved", "OCR_CONFIDENCE_REQUIRES_REVIEW")
        if UNCERTAIN.search(text) or AVAILABILITY.search(text) or PAGE_QUALIFIER.search(pages[candidate.page]):
            return verdict("unresolved", "AMBIGUOUS_OR_NON_DEADLINE_TEXT")
        if semantic and candidate.semantic_status == "ambiguous":
            return verdict("unresolved", "SEMANTIC_REVIEW_AMBIGUOUS")
        relative_weeks = {int(a or b) for a, b in re.findall(
            r"\bweek\s*([1-9]|[12]\d|3[0-2])\b|第\s*([1-9]|[12]\d|3[0-2])\s*周", text, re.I)}
        if relative_weeks:
            if len(relative_weeks) != 1:
                return verdict("unresolved", "RELATIVE_WEEK_AMBIGUOUS")
            if not (DUE.search(text) or SCHEDULED.search(candidate.title)):
                return verdict("unresolved", "DEADLINE_ROLE_REQUIRED")
            return verdict("unresolved", "CANVAS_TEACHING_WEEK_REQUIRED")
        dates = list(DATE_PATTERN.finditer(text))
        selected = next((match for match in dates if match[0] == candidate.date_expression), None)
        if semantic:
            try:
                day = date.fromisoformat(candidate.normalized_date) if candidate.normalized_date else None
            except ValueError:
                return verdict("rejected", "INVALID_NORMALIZED_DATE_OR_TIME")
            if (not candidate.date_expression or candidate.date_expression not in text or day is None
                    or day not in grounded_source_dates(candidate.date_expression, parsed, document)):
                return verdict("unresolved", "EXPLICIT_GROUNDED_DATE_REQUIRED")
        elif len(dates) != 1 or selected is None:
            return verdict("unresolved", "EXPLICIT_UNIQUE_DATE_REQUIRED")
        if not semantic and not (DUE.search(text) or SCHEDULED.search(candidate.title)):
            return verdict("unresolved", "DEADLINE_ROLE_REQUIRED")
        try:
            if not semantic:
                day = parse_date(selected[0])
            authority = getattr(document, "source_authority", None)
            if authority == "operator" and not document.valid_from <= day <= document.valid_until:
                return verdict("rejected", "OUTSIDE_APPROVED_COURSE_PERIOD")
            if semantic:
                time_options = grounded_source_times(text)
                if candidate.normalized_time is None:
                    if time_options:
                        return verdict("unresolved", "EXPLICIT_GROUNDED_TIME_REQUIRED")
                    clock = time.min
                else:
                    clock = time.fromisoformat(candidate.normalized_time)
                    if clock not in time_options:
                        return verdict("unresolved", "EXPLICIT_GROUNDED_TIME_REQUIRED")
            else:
                times = list(re.finditer(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text))
                if len(times) > 1 or re.search(r"\b(?:am|pm|UTC|GMT|PST|EST|CET)\b|[+-]\d\d:\d\d", text, re.I):
                    return verdict("unresolved", "TIME_FORMAT_REQUIRES_REVIEW")
                clock = time(int(times[0][1]), int(times[0][2])) if times else time.min
            value = datetime.combine(day, clock, self.zone)
        except (ValueError, StopIteration):
            return verdict("rejected", "INVALID_LITERAL_DATE_OR_TIME")
        checks = ("CONTENT_HASH", "COURSE_SCOPE", "EXACT_PAGE_EVIDENCE", "EXPLICIT_DATE", "DEADLINE_ROLE")
        checks += (("CANVAS_API_AUTHENTICATED_SOURCE", "CANVAS_API_COURSE_SCOPE")
                   if getattr(document, "source_authority", None) == "canvas_api"
                   else ("OPERATOR_TRUSTED_SOURCE", "APPROVED_COURSE_PERIOD"))
        if semantic:
            checks += ("CODEX_SEMANTIC_REVIEW", "EXACT_EVIDENCE_SPAN", "EXACT_DATE_ANCHOR",
                       "CODEX_NORMALIZED_DATE_TIME")
        if page.extraction_mode == "ocr_ppocrv6_small":
            checks += ("OCR_PP_OCRV6_SMALL", "OCR_CONFIDENCE_THRESHOLD")
        return CandidateValidation("confirmed", checks,
                                   now, value_at=value,
                                   value_kind=candidate.value_kind if semantic else "due_at" if DUE.search(text) else "start_at",
                                   date_only=candidate.normalized_time is None if semantic else not times)

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
