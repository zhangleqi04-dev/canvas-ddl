"""Structured fields only. Preserve null personal due dates and availability."""
import re
from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
from canvas_ddl.canvas.errors import ApplicationError
from .models import Deadline, RawRecord, SourceReference, TYPES, ValidationInfo
from .time_range import aware


def submission_status(value: dict | None) -> str:
    if not isinstance(value, dict):
        return "unknown"
    if value.get("excused"):
        return "unknown"
    submitted = bool(value.get("submitted_at")) or value.get("workflow_state") in ("submitted", "pending_review")
    # Graded offline work has no submission timestamp; Canvas does not always tell
    # us whether it was handed in. Do not equate a grade with a submission.
    if submitted and value.get("late"):
        return "late"
    if submitted:
        return "submitted"
    if value.get("missing"):
        return "missing"
    if value.get("workflow_state") == "unsubmitted":
        return "not_submitted"
    return "unknown"


class DeadlineNormalizer:
    def __init__(self, base_url: str, timezone: str):
        self.base_url = base_url
        self.zone = ZoneInfo(timezone)

    def timestamp(self, value) -> datetime | None:
        if value is None or value == "":
            return None
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not aware(result):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ApplicationError("INVALID_RECORD_DATE", "A Canvas item has an invalid scheduled timestamp.") from None
        return result.astimezone(self.zone)

    def normalize(self, raw: RawRecord, assignments: dict[tuple[str, str], dict], *, allow_undated=False) -> Deadline | None:
        p, course, kind = raw.payload, raw.course, raw.source_type
        if p.get("published") is False or p.get("workflow_state") == "deleted" or p.get("is_announcement"):
            return None
        identifier = str(p.get("id") or "")
        if not identifier:
            raise ApplicationError("INVALID_RECORD", "A Canvas item is missing its resource ID.")
        if not re.fullmatch(r"\d+|assignment_\d+", identifier):
            raise ApplicationError("INVALID_RECORD", "A Canvas item has an invalid resource ID.")
        cid = course.course_id
        assignment_id = None
        if kind == "canvas_assignment":
            assignment_id = identifier
        elif kind == "canvas_calendar_assignment":
            match = re.fullmatch(r"assignment_(\d+)", identifier)
            assignment_id = str((p.get("assignment") or {}).get("id") or match[1]) if match else str((p.get("assignment") or {}).get("id") or "")
        else:
            assignment_id = str(p.get("assignment_id") or (p.get("assignment") or {}).get("id") or "")
        personal = assignments.get((cid, assignment_id)) if assignment_id else None
        if personal and personal.get("published") is False:
            return None
        effective = personal if personal is not None else p
        title = str(p.get("name") or p.get("title") or (personal or {}).get("name") or "Untitled")
        identities = [f"{kind}:{identifier}"]
        if assignment_id:
            identities.append(f"assignment:{assignment_id}")
        if p.get("quiz_id"):
            identities.append(f"quiz:{p['quiz_id']}")
        if kind == "canvas_quiz":
            identities.append(f"quiz:{identifier}")
        topic = p.get("discussion_topic") or {}
        if topic.get("id"):
            identities.append(f"discussion:{topic['id']}")
        if kind == "canvas_discussion":
            identities.append(f"discussion:{identifier}")
        due = self.timestamp(effective.get("due_at"))
        start = end = None
        all_day_date = None
        date_only = False
        if kind == "canvas_calendar_event":
            start, end = self.timestamp(p.get("start_at")), self.timestamp(p.get("end_at"))
            if p.get("all_day"):
                try:
                    all_day_date = date.fromisoformat(p["all_day_date"])
                except (KeyError, ValueError, TypeError):
                    raise ApplicationError("INVALID_RECORD_DATE", "An all-day event has no valid date.") from None
                start = datetime.combine(all_day_date, time.min, self.zone)
                end = None
                date_only = True
        elif kind == "canvas_calendar_assignment" and personal is None:
            # Synthetic assignment events start at the due time. This is a due
            # timestamp even when Canvas displays the event as all-day.
            due = self.timestamp(p.get("start_at"))
        # unlock_at and lock_at do not become exam start/end or due timestamps.
        unlock, lock = self.timestamp(effective.get("unlock_at")), self.timestamp(effective.get("lock_at"))
        if not (due or start or end) and not allow_undated:
            return None
        if start and end and end.astimezone(timezone.utc) < start.astimezone(timezone.utc):
            raise ApplicationError("INVALID_RECORD_DATE", "A Canvas event ends before it starts.")
        source_paths = {"canvas_assignment": "assignments", "canvas_quiz": "quizzes", "canvas_discussion": "discussion_topics"}
        if kind in source_paths:
            url = f"{self.base_url}/courses/{cid}/{source_paths[kind]}/{identifier}"
        elif kind == "canvas_calendar_assignment" and assignment_id:
            url = f"{self.base_url}/courses/{cid}/assignments/{assignment_id}"
        else:
            url = f"{self.base_url}/calendar?event_id={identifier}&include_contexts=course_{cid}"
        explicit = effective.get("deadline_type") or effective.get("assessment_type")
        if explicit not in TYPES:
            explicit = None
        submission_types = tuple(effective.get("submission_types") or ())
        if effective.get("is_quiz_assignment") or effective.get("is_new_quiz") or effective.get("quiz_id"):
            submission_types += ("online_quiz",)
        if kind == "canvas_quiz" and p.get("quiz_type") in ("practice_quiz", "survey", "graded_survey"):
            explicit = explicit or "quiz"
        fallback = {"canvas_assignment": "assignment", "canvas_quiz": "quiz", "canvas_discussion": "discussion",
                    "canvas_calendar_event": "event", "canvas_calendar_assignment": "assignment"}.get(kind, "other")
        reference = SourceReference(kind, identifier, url, raw.verified_at,
                                    value_at=due or start or end, value_kind="due_at" if due else "start_at" if start else "end_at", date_only=date_only)
        return Deadline(f"course_{cid}_{kind.removeprefix('canvas_')}_{identifier}", identifier, cid,
                        course.course_code, course.course_name, title, fallback, start, due, end,
                        submission_status(effective.get("submission")), kind, url, raw.verified_at,
                        (reference,), all_day_date, date_only, unlock, lock, tuple(identities), explicit,
                        submission_types, personal is not None or kind == "canvas_assignment")

    def normalize_document(self, candidate, validation, document, parsed, course):
        if validation.status != "confirmed" or validation.value_at is None:
            raise ApplicationError("UNVALIDATED_DOCUMENT_DEADLINE", "Only validated document evidence may enter the Deadline model.")
        value = validation.value_at.astimezone(self.zone)
        info = ValidationInfo(validation.status, validation.rule_version, validation.validated_at, validation.reasons)
        url = document.source_url + (f"#page={candidate.page}" if document.path.suffix.casefold() == ".pdf" else "")
        page = next(p for p in parsed.pages if p.page == candidate.page)
        lines = [line.strip() for line in page.text.splitlines()]
        confidence = max((page.line_confidences[i] for i, line in enumerate(lines)
                          if line == candidate.evidence_text and i < len(page.line_confidences)), default=None)
        reference = SourceReference("official_document", None, url, validation.validated_at,
                                    document.document_id, document.document_name, candidate.page, candidate.evidence_text,
                                    document.sha256, parsed.parsed_at, info, value, validation.value_kind, validation.date_only,
                                    page.extraction_mode, page.ocr_engine, confidence, candidate.location,
                                    document.source_authority)
        fallback = candidate.deadline_type or ("event" if validation.value_kind == "start_at" else "assignment")
        if candidate.deadline_type is None and re.search(r"\bquiz\b|小测|测验", candidate.title, re.I):
            fallback = "quiz"
        elif candidate.deadline_type is None and re.search(r"\bdiscussion\b|讨论", candidate.title, re.I):
            fallback = "discussion"
        return Deadline(deadline_id=f"course_{course.course_id}_document_{candidate.candidate_id}",
                        canvas_resource_id=None, course_id=course.course_id, course_code=course.course_code,
                        course_name=course.course_name, title=candidate.title, type=fallback,
                        start_at=value if validation.value_kind == "start_at" else None,
                        due_at=value if validation.value_kind == "due_at" else None,
                        end_at=value if validation.value_kind == "end_at" else None,
                        submission_status="unknown", source_type="official_document", source_url=url,
                        last_verified_at=validation.validated_at, sources=(reference,),
                        all_day_date=value.date() if validation.date_only else None, date_only=validation.date_only,
                        identities=(f"document_candidate:{candidate.candidate_id}",), validation=info,
                        canonical_reason="validated_official_document")
