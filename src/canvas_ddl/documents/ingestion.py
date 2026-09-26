from dataclasses import replace
from datetime import datetime
import json
from canvas_ddl.deadlines.time_range import aware
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.deadlines.validator import DeadlineValidator
from canvas_ddl.deadlines.normalizer import DeadlineNormalizer
from .parser import DocumentParser
from .extractor import DeadlineExtractor
from .models import ParsedDocument, RelativeWeekEvidence
from .semantic import CodexSemanticExtractor, LightSemanticPrefilter, StructuralChunker, request_batch


class DocumentIngestionService:
    def __init__(self, registry, repository, *, timezone, base_url, clock, parser=None, extractor=None,
                 ocr_min_confidence=0.90, require_semantic_review=False):
        self.registry, self.repository, self.clock = registry, repository, clock
        self.parser = parser or DocumentParser()
        self.extractor = extractor or DeadlineExtractor()
        self.validator = DeadlineValidator(timezone, ocr_min_confidence=ocr_min_confidence)
        self.normalizer = DeadlineNormalizer(base_url, timezone)
        self.require_semantic_review = require_semantic_review
        self.semantic_extractor = CodexSemanticExtractor()
        self.chunker = StructuralChunker()
        self.semantic_prefilter = LightSemanticPrefilter()
        from .scan import DocumentScanService
        self.scanner = DocumentScanService(self)

    def ingest(self, document_id, course):
        document = self.registry.get(document_id)
        if document.course_id != course.course_id or document.course_code != course.course_code:
            raise ApplicationError("DOCUMENT_COURSE_MISMATCH", "The registered document does not match the current resolved course.")
        parsed = self.parser.parse(document, now=self.clock())
        # Runtime semantic mode never uses keyword extraction as a factual stage.
        # Keep the legacy extractor only for migration/tests and provisional diagnostics.
        candidates = () if self.require_semantic_review else self.extractor.extract(parsed, document)
        if len(candidates) > 2000 or len({c.candidate_id for c in candidates}) != len(candidates):
            raise ApplicationError("INVALID_DOCUMENT_EXTRACTION", "The extractor returned too many or duplicate candidate IDs.")
        validations = tuple(self.validator.validate_candidate(c, parsed, document, now=self.clock()) for c in candidates)
        self.repository.save(parsed, candidates, validations, course.course_id)
        semantic_requests = self._semantic_requests(parsed, document) if self.require_semantic_review else ()
        return {"document_id": document_id, "document_name": document.document_name, "sha256": parsed.sha256,
                "pages": len(parsed.pages), "ingested_at": parsed.parsed_at.isoformat(),
                "confirmed": 0 if self.require_semantic_review else sum(v.status == "confirmed" for v in validations),
                "unresolved": len(semantic_requests) if self.require_semantic_review else sum(v.status == "unresolved" for v in validations),
                "rejected": 0 if self.require_semantic_review else sum(v.status == "rejected" for v in validations),
                "legacy_rule_candidates": len(candidates),
                "ocr_pages": [p.page for p in parsed.pages if p.extraction_mode == "ocr_ppocrv6_small"],
                "semantic_review_required": bool(semantic_requests),
                "semantic_review_total": len(semantic_requests),
                "warnings": [self._page_warning(p) for p in parsed.pages if self._page_warning(p)]}

    def semantic_review_requests(self, document_id, course, *, offset=0, limit=20):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 50:
            raise ApplicationError("INVALID_QUERY", "Semantic review offset/limit are invalid.")
        document = self.registry.get(document_id)
        if document.course_id != course.course_id or document.course_code != course.course_code:
            raise ApplicationError("DOCUMENT_COURSE_MISMATCH", "The registered document does not match the current resolved course.")
        loaded = self.repository.load(document)
        if loaded is None or loaded[0].parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
            self.ingest(document_id, course)
            loaded = self.repository.load(document)
        parsed, rows = loaded
        requests = self._semantic_requests(parsed, document)
        reviewed = self.repository.load_semantic_reviews(document)
        _requests, _reviews, _semantic_rows, issue = self._semantic_artifact(parsed, document, rows)
        if issue == "invalid":
            self.ingest(document_id, course)
            parsed, rows = self.repository.load(document)
            requests = self._semantic_requests(parsed, document)
            reviewed = {}
        pending = tuple(request for request in requests if request.request_id not in reviewed)
        batch = request_batch(document, parsed, pending, offset=offset, limit=limit)
        batch["reviewed"] = len(reviewed)
        batch["document_total"] = len(requests)
        batch["complete"] = not pending
        return batch

    def apply_semantic_review(self, document_id, course, payload):
        document = self.registry.get(document_id)
        if document.course_id != course.course_id or document.course_code != course.course_code:
            raise ApplicationError("DOCUMENT_COURSE_MISMATCH", "The registered document does not match the current resolved course.")
        loaded = self.repository.load(document)
        if loaded is None:
            raise ApplicationError("DOCUMENT_NOT_INGESTED", "Parse the approved document before applying semantic review.")
        parsed, _ = loaded
        if parsed.parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
            raise ApplicationError("DOCUMENT_NOT_INGESTED", "Re-parse the approved document before applying semantic review.")
        requests = self._semantic_requests(parsed, document)
        batch_reviews, _ = self.semantic_extractor.parse_batch(
            payload, document=document, parsed=parsed, requests=requests,
        )
        reviews = self.repository.load_semantic_reviews(document)
        reviews.update(batch_reviews)
        combined = {"schema_version": "codex-semantic-v1", "document_id": document.document_id,
                    "document_sha256": parsed.sha256, "reviews": list(reviews.values())}
        reviews, candidates = self.semantic_extractor.parse_batch(
            combined, document=document, parsed=parsed, requests=requests,
        )
        validations = tuple(self.validator.validate_candidate(candidate, parsed, document, now=self.clock())
                            for candidate in candidates)
        self.repository.save_semantic(parsed, candidates, validations, reviews, course.course_id)
        complete = {request.request_id for request in requests} == set(reviews)
        return {"document_id": document_id, "document_name": document.document_name,
                "schema_version": "codex-semantic-v1", "reviewed": len(reviews),
                "total": len(requests), "complete": complete,
                "confirmed": sum(v.status == "confirmed" for v in validations),
                "unresolved": sum(v.status == "unresolved" for v in validations),
                "rejected": sum(v.status == "rejected" for v in validations)}

    @staticmethod
    def _page_warning(page):
        label = page.location or f"page {page.page}"
        if page.extraction_mode == "ocr_unavailable":
            return f"{label}: no usable text layer and local OCR is unavailable."
        if page.extraction_mode == "ocr_failed":
            return f"{label}: local OCR failed; coverage is incomplete."
        if not page.text.strip():
            return f"{label}: no usable text was extracted."
        return None

    def _semantic_requests(self, parsed, document):
        return self.semantic_prefilter.filter(self.chunker.chunk(parsed, document))

    def _semantic_artifact(self, parsed, document, rows):
        """Rebuild stored proposals from strict reviews; never trust completeness flags alone."""
        requests = self._semantic_requests(parsed, document)
        reviews = self.repository.load_semantic_reviews(document)
        request_ids = {request.request_id for request in requests}
        review_ids = set(reviews)
        if review_ids - request_ids:
            return requests, reviews, None, "invalid"
        if request_ids - review_ids:
            return requests, reviews, None, "incomplete"
        payload = {"schema_version": "codex-semantic-v1", "document_id": document.document_id,
                   "document_sha256": parsed.sha256, "reviews": list(reviews.values())}
        try:
            _validated_reviews, expected = self.semantic_extractor.parse_batch(
                payload, document=document, parsed=parsed, requests=requests,
            )
        except ApplicationError:
            return requests, reviews, None, "invalid"
        stored = {candidate.candidate_id: (candidate, validation) for candidate, validation in rows}
        if len(stored) != len(rows) or {candidate.candidate_id: candidate for candidate in expected} != {
                key: value[0] for key, value in stored.items()}:
            return requests, reviews, None, "invalid"
        return requests, reviews, tuple((candidate, stored[candidate.candidate_id][1]) for candidate in expected), None

    def load_deadlines(self, courses):
        course_map = {c.course_id: c for c in courses}
        documents = self.registry.list_documents(tuple(course_map))
        deadlines, summary, warnings = [], [], []
        complete = True
        for document in documents:
            loaded = self.repository.load(document)
            info = {"document_id": document.document_id, "document_name": document.document_name,
                    "course_id": document.course_id, "state": "available", "confirmed": 0, "unresolved": 0,
                    "rejected": 0, "ingested_at": None, "candidate_issues": [], "source_url": document.source_url,
                    "valid_from": document.valid_from.isoformat(), "valid_until": document.valid_until.isoformat(), "source_verified": True}
            if document.refresh_blocked:
                info["state"] = "remote_version_requires_review"
                complete = False
                warnings.append(f"Official document {document.document_id}: remote evidence changed or disappeared; previous deadlines withheld.")
                summary.append(info)
                continue
            if loaded is None or document.course_code != course_map[document.course_id].course_code:
                info["state"] = "not_ingested_or_version_mismatch"
                complete = False
                warnings.append(f"Official document {document.document_id}: approved version requires ingestion.")
                summary.append(info)
                continue
            parsed, rows = loaded
            if parsed.parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
                info["state"] = "parser_version_mismatch"
                complete = False
                warnings.append(f"Official document {document.document_id}: parser changed; re-ingestion required.")
                summary.append(info)
                continue
            info["ingested_at"] = parsed.parsed_at.isoformat()
            semantic_requests = self._semantic_requests(parsed, document) if self.require_semantic_review else ()
            semantic_reviews = self.repository.load_semantic_reviews(document) if self.require_semantic_review else {}
            if self.require_semantic_review:
                semantic_requests, semantic_reviews, semantic_rows, semantic_issue = self._semantic_artifact(
                    parsed, document, rows,
                )
                missing = [request for request in semantic_requests if request.request_id not in semantic_reviews]
                info.update({"semantic_review_required": semantic_issue is not None,
                             "semantic_review_schema": "codex-semantic-v1",
                             "semantic_review_total": len(semantic_requests),
                             "semantic_reviewed": len(semantic_reviews),
                             "semantic_review_pending": len(missing)})
                if semantic_issue is not None:
                    complete = False
                    info["state"] = "semantic_review_required"
                    info["candidate_issues"].append({"status": "unresolved",
                        "reasons": ["INVALID_PERSISTED_SEMANTIC_REVIEW" if semantic_issue == "invalid"
                                    else "CODEX_SEMANTIC_REVIEW_REQUIRED"],
                        "pending_chunks": len(missing)})
                    warning = "is invalid" if semantic_issue == "invalid" else "is incomplete"
                    warnings.append(f"Official document {document.document_id}: Codex semantic review {warning}; document deadlines withheld.")
                    summary.append(info)
                    continue
                rows = semantic_rows
            incomplete_pages = [p for p in parsed.pages if not p.text.strip()
                                or p.extraction_mode in ("ocr_unavailable", "ocr_failed", "ocr_empty")]
            if incomplete_pages:
                complete = False
                for page in incomplete_pages:
                    warnings.append(f"Official document {document.document_id}: {self._page_warning(page)}")
            for candidate, stored in rows:
                # Re-run independent checks on the persisted page evidence, never
                # parse the source document during a deadline query or trust a stored LLM flag.
                validation = self.validator.validate_candidate(candidate, parsed, document, now=parsed.parsed_at)
                if self.require_semantic_review and candidate.extractor != "codex-semantic-v1":
                    complete = False
                    info["unresolved"] += 1
                    info["candidate_issues"].append({"candidate_id": candidate.candidate_id,
                        "page": candidate.page, "location": candidate.location, "title": candidate.title,
                        "status": "unresolved", "reasons": ["CODEX_SEMANTIC_REVIEW_REQUIRED"],
                        "evidence_text": candidate.evidence_text,
                        "validation_rule_version": validation.rule_version,
                        "validated_at": validation.validated_at.isoformat()})
                    continue
                if stored.get("rule_version") != validation.rule_version:
                    complete = False
                    warnings.append(f"Official document {document.document_id}: validator changed; re-ingestion required.")
                    continue
                try:
                    original_validation_time = datetime.fromisoformat(stored["validated_at"])
                    if not aware(original_validation_time):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Persisted validation metadata is invalid.") from None
                validation = replace(validation, validated_at=original_validation_time)
                info[validation.status] += 1
                if validation.status == "confirmed":
                    d = self.normalizer.normalize_document(candidate, validation, document, parsed, course_map[document.course_id])
                    deadlines.append(self.validator.validate_deadline(d, now=self.clock()))
                else:
                    complete = False
                    info["candidate_issues"].append({"candidate_id": candidate.candidate_id, "page": candidate.page,
                                                    "location": candidate.location,
                                                    "title": candidate.title, "status": validation.status, "reasons": list(validation.reasons),
                                                    "evidence_text": candidate.evidence_text, "validation_rule_version": validation.rule_version,
                                                    "validated_at": validation.validated_at.isoformat()})
                    warnings.append(f"Official document {document.document_id}, {candidate.location or f'page {candidate.page}'}: {validation.status} candidate ({validation.reasons[0]}); not counted.")
            summary.append(info)
        if not documents:
            warnings.append("No official documents are registered for these courses; document coverage has not been established.")
        pending_path = self.registry.path.parent / "pending-review.json"
        if pending_path.exists():
            try:
                pending = json.loads(pending_path.read_text(encoding="utf8"))["documents"]
                provisional, scan_warnings = self.scanner.load(courses, pending)
            except (OSError, ValueError, KeyError, TypeError):
                raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Provisional document evidence is unavailable.") from None
            summary.extend(provisional)
            warnings.extend(scan_warnings)
            if provisional:
                complete = False
        return deadlines, tuple(summary), warnings, complete

    def search_content(self, courses, *, types=None, extra_exam_keywords=()):
        from .search import DocumentContentSearcher
        course_map = {c.course_id: c for c in courses}
        approved = {d.document_id: d for d in self.registry.list_documents(tuple(course_map))}
        inputs = [(d, self.repository, True) for d in approved.values() if not d.refresh_blocked]
        pending_path = self.registry.path.parent / "pending-review.json"
        if pending_path.exists():
            rows = json.loads(pending_path.read_text(encoding="utf8"))["documents"]
            for row in rows:
                if str(row.get("course_id")) not in course_map or row.get("remote_state") in ("missing", "inaccessible"):
                    continue
                doc = approved.get(row.get("document_id"))
                if doc and doc.sha256 == row.get("sha256") and not doc.refresh_blocked:
                    continue
                inputs.append((self.scanner.draft(row), self.scanner.repository, False))
        searcher, results = DocumentContentSearcher(), []
        for doc, repository, verified in inputs:
            artifact = repository.load(doc)
            if artifact is None:
                continue
            result = searcher.search(artifact[0], types=types, extra_exam_keywords=extra_exam_keywords)
            if result["match_count"]:
                results.append({"document_id": doc.document_id, "document_name": doc.document_name,
                    "course_id": doc.course_id, "course_code": course_map[doc.course_id].course_code,
                    "source_url": doc.source_url, "source_verified": verified, "content_hash": doc.sha256,
                    "scanned_at": artifact[0].parsed_at.isoformat(), "unvalidated_excerpts": True, **result})
        return tuple(results)

    @staticmethod
    def _relative_evidence(document, parsed, candidate, validation, *, source_verified):
        if "CANVAS_TEACHING_WEEK_REQUIRED" not in validation.reasons:
            return None
        page = next((p for p in parsed.pages if p.page == candidate.page), None)
        if page is None:
            return None
        lines = [line.strip() for line in page.text.splitlines()]
        confidence = max((page.line_confidences[i] for i, line in enumerate(lines)
                          if line == candidate.evidence_text and i < len(page.line_confidences)), default=None)
        return RelativeWeekEvidence(candidate, validation, document.document_name, document.source_url,
                                    document.sha256, parsed.parsed_at, page.extraction_mode,
                                    page.ocr_engine, confidence, source_verified)

    def load_relative_week_evidence(self, courses):
        """Load already-extracted Week N proposals; never opens a source document."""
        course_map = {c.course_id: c for c in courses}
        approved = {d.document_id: d for d in self.registry.list_documents(tuple(course_map))}
        result = []
        for document in approved.values():
            if document.refresh_blocked:
                continue
            loaded = self.repository.load(document)
            if loaded is None:
                continue
            parsed, rows = loaded
            if parsed.parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
                continue
            if self.require_semantic_review:
                _requests, _reviews, semantic_rows, issue = self._semantic_artifact(parsed, document, rows)
                if issue is not None:
                    continue
                rows = semantic_rows
            for candidate, stored in rows:
                if self.require_semantic_review and candidate.extractor != "codex-semantic-v1":
                    continue
                validation = self.validator.validate_candidate(candidate, parsed, document, now=parsed.parsed_at)
                if stored.get("rule_version") != validation.rule_version:
                    continue
                evidence = self._relative_evidence(document, parsed, candidate, validation, source_verified=True)
                if evidence:
                    result.append(evidence)

        pending_path = self.registry.path.parent / "pending-review.json"
        if pending_path.exists():
            try:
                rows = json.loads(pending_path.read_text(encoding="utf8"))["documents"]
            except (OSError, ValueError, KeyError, TypeError):
                raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Provisional document evidence is unavailable.") from None
            for row in rows:
                if str(row.get("course_id")) not in course_map or row.get("remote_state") in ("missing", "inaccessible"):
                    continue
                document = self.scanner.draft(row)
                current = approved.get(document.document_id)
                if current and current.sha256 == document.sha256 and not current.refresh_blocked:
                    continue
                loaded = self.scanner.repository.load(document)
                if loaded is None:
                    continue
                parsed, candidates = loaded
                if parsed.parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default:
                    continue
                for candidate, _stored in candidates:
                    validation = self.scanner.validate(candidate, parsed, document, now=parsed.parsed_at)
                    evidence = self._relative_evidence(document, parsed, candidate, validation, source_verified=False)
                    if evidence:
                        result.append(evidence)
        return tuple(result)
