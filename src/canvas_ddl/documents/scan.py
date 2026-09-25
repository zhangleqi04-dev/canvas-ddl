"""Provisional course-document evidence stays separate from approved fact artifacts."""
from dataclasses import replace
import re
from .models import DocumentDraft, ParsedDocument
from .repository import DocumentRepository
from canvas_ddl.canvas.errors import ApplicationError


class DocumentScanService:
    def __init__(self, ingestion):
        self.ingestion = ingestion
        self.repository = DocumentRepository(ingestion.repository.path.with_suffix(".pending.sqlite3"))

    def draft(self, row):
        try:
            if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not str(row["course_id"]).isdecimal():
                raise ValueError
            return DocumentDraft(row["document_id"], str(row["course_id"]), row["course_code"], row["course_name"],
                row["document_name"], row["document_kind"], row["source_url"],
                (self.ingestion.registry.path.parent / row["path"]).resolve(), row["sha256"])
        except (KeyError, ValueError, TypeError):
            raise ApplicationError("INVALID_DOCUMENT_DRAFT", "The provisional document scan record is invalid.") from None

    def validate(self, candidate, parsed, draft, *, now):
        # Independent literal checks may identify a proposal's explicit timestamp,
        # but the broad syntax range above is NOT an approved teaching period.
        v = self.ingestion.validator.validate_candidate(candidate, parsed, draft, now=now)
        reasons = v.reasons if v.status != "confirmed" else ()
        return replace(v, status="rejected" if v.status == "rejected" else "unresolved",
            reasons=reasons + ("SOURCE_APPROVAL_REQUIRED", "COURSE_PERIOD_APPROVAL_REQUIRED"))

    def scan(self, row):
        draft = self.draft(row)
        parsed = self.ingestion.parser.parse(draft, now=self.ingestion.clock())
        candidates = self.ingestion.extractor.extract(parsed, draft)
        if len(candidates) > 2000 or len({c.candidate_id for c in candidates}) != len(candidates):
            raise ApplicationError("INVALID_DOCUMENT_EXTRACTION", "Invalid provisional extraction output.")
        validations = tuple(self.validate(c, parsed, draft, now=self.ingestion.clock()) for c in candidates)
        self.repository.save(parsed, candidates, validations, draft.course_id)
        return {"state": "scanned_pending_review", "pages": len(parsed.pages), "confirmed": 0,
                "candidate_count": len(candidates), "scanned_at": parsed.parsed_at.isoformat(),
                "plain_fallback_pages": [p.page for p in parsed.pages if p.extraction_mode == "plain"],
                "ocr_pages": [p.page for p in parsed.pages if p.extraction_mode == "ocr_ppocrv6_small"],
                "empty_pages": [p.page for p in parsed.pages if not p.text.strip()]}

    def load(self, courses, rows):
        course_map = {c.course_id: c for c in courses}
        approved = {d.document_id: d for d in self.ingestion.registry.list_documents(tuple(course_map))}
        summaries, warnings = [], []
        for row in rows:
            if str(row.get("course_id")) not in course_map:
                continue
            doc = approved.get(row.get("document_id"))
            if doc and doc.sha256 == row.get("sha256") and not doc.refresh_blocked:
                continue
            draft = self.draft(row)
            course = course_map[draft.course_id]
            if draft.course_code != course.course_code:
                raise ApplicationError("DOCUMENT_COURSE_MISMATCH", "A provisional scan does not match the selected course.")
            state = row.get("remote_state", "pending_review")
            info = {"document_id": draft.document_id, "document_name": draft.document_name,
                "course_id": draft.course_id, "course_code": draft.course_code, "state": state,
                "source_verified": False, "source_url": draft.source_url, "confirmed": 0,
                "unresolved": 0, "rejected": 0, "candidate_issues": [], "ingested_at": None}
            summaries.append(info)
            if state in ("missing", "inaccessible"):
                continue  # Removed/inaccessible pending proposals never look current.
            loaded = self.repository.load(draft)
            if loaded is None:
                info["state"] = "not_scanned"
                warnings.append(f"Course {draft.course_id}: document {draft.document_name} has not been content-scanned.")
                continue
            parsed, candidates = loaded
            if parsed.parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
                info["state"] = "parser_version_mismatch"
                warnings.append(f"Course {draft.course_id}: document {draft.document_name} requires a new content scan.")
                continue
            info["ingested_at"] = parsed.parsed_at.isoformat()
            info["empty_pages"] = [p.page for p in parsed.pages if not p.text.strip()]
            for candidate, stored in candidates:
                v = self.validate(candidate, parsed, draft, now=parsed.parsed_at)
                info[v.status] += 1
                info["candidate_issues"].append({"candidate_id": candidate.candidate_id, "title": candidate.title,
                    "page": candidate.page, "location": candidate.location,
                    "evidence_text": candidate.evidence_text, "status": v.status,
                    "reasons": list(v.reasons), "source_verified": False,
                    "proposed_value_at": v.value_at.isoformat() if v.value_at else None, "date_only": v.date_only,
                    "validated_at": v.validated_at.isoformat(), "validation_rule_version": v.rule_version})
            warnings.append(f"Course {draft.course_id}: {draft.document_name} was content-scanned; source approval is pending, not a confirmed deadline fact.")
        return summaries, warnings
