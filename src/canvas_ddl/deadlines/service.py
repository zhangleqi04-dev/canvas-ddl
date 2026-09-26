"""Single application gateway for CLI, Skill, and future adapters."""
from collections import Counter
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import re
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.collectors.assignment import AssignmentCollector
from canvas_ddl.collectors.quiz import QuizCollector
from canvas_ddl.collectors.discussion import DiscussionCollector
from canvas_ddl.collectors.calendar import CalendarCollector, belongs_to_course
from canvas_ddl.collectors.calendar_feed import CourseCalendarFeedCollector
from canvas_ddl.courses.repository import CourseRepository
from canvas_ddl.courses.resolver import CourseResolver
from .models import (RawRecord, Coverage, DeadlineQueryResult, ResolvedDeadlineQuery,
                     ReferenceDeadline, SourceReference, ValidationInfo, TYPES, STATUSES)
from .query import DeadlineQuery
from .time_intent import TimeIntent
from .time_range import TimeRangeResolver
from .normalizer import DeadlineNormalizer
from .classifier import DeadlineClassifier
from .deduplicator import DeadlineDeduplicator
from .reconciler import DeadlineReconciler
from .validator import DeadlineValidator
from .teaching_weeks import TeachingWeekResolver


class DeadlineService:
    def __init__(self, client, *, base_url, timezone_name="Asia/Singapore", allowed_ids=(),
                 exam_keywords=(), clock=None, repository=None, collectors=None, course_resolver=None, document_ingestion=None,
                 document_preparation=None, document_refresher=None, teaching_calendar_collector=None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.time_resolver = TimeRangeResolver(timezone_name)
        self.repository = repository or CourseRepository(client, allowed_ids)
        self.course_resolver = course_resolver or CourseResolver()
        self.normalizer = DeadlineNormalizer(base_url, timezone_name)
        self.classifier = DeadlineClassifier(exam_keywords)
        self.deduplicator = DeadlineDeduplicator()
        self.reconciler = DeadlineReconciler()
        self.validator = DeadlineValidator(timezone_name)
        self.teaching_weeks = TeachingWeekResolver(base_url, timezone_name)
        self.document_ingestion = document_ingestion
        self.document_preparation = document_preparation
        self.document_refresher = document_refresher
        self.teaching_calendar_collector = teaching_calendar_collector or CourseCalendarFeedCollector(
            client, clock=self.clock, timezone_name=timezone_name)
        self.collectors = collectors if collectors is not None else (
            AssignmentCollector(client), QuizCollector(client), DiscussionCollector(client),
            CalendarCollector(client), CalendarCollector(client, "assignment"))

    def list_courses(self, course_reference=None):
        courses = self.repository.list_courses()
        if course_reference is not None:
            return [self.course_resolver.resolve(course_reference, courses)]
        return courses

    def prepare_documents(self, course_references, *, limit=20):
        if self.document_preparation is None:
            raise ApplicationError("INVALID_CONFIG", "Document preparation is not configured.")
        if not course_references or len(course_references) > 10:
            raise ApplicationError("INVALID_QUERY", "Select between one and ten courses for document preparation.")
        available = self.list_courses()
        selected = {}
        for reference in course_references:
            course = self.course_resolver.resolve(reference, available)
            selected[course.course_id] = course
        return self.document_preparation.prepare(list(selected.values()), limit=limit)

    def approve_document(self, document_id, *, approved_by, valid_from, valid_until):
        if self.document_preparation is None:
            raise ApplicationError("INVALID_CONFIG", "Document preparation is not configured.")
        self.document_preparation.approve(document_id, approved_by=approved_by,
                                          valid_from=valid_from, valid_until=valid_until)
        return self.ingest_document(document_id)

    def _resolve_courses(self, query):
        courses = self.list_courses()
        if query.course_reference is not None and query.course_ids is not None:
            raise ApplicationError("INVALID_QUERY", "Use a course reference or course IDs.")
        if query.course_reference is not None:
            return [self.course_resolver.resolve(query.course_reference, courses)]
        if query.course_ids is not None:
            if not query.course_ids or any(not str(v).isdecimal() for v in query.course_ids):
                raise ApplicationError("INVALID_QUERY", "Course IDs must be a nonempty numeric list.")
            selected = [c for c in courses if c.course_id in query.course_ids]
            if set(query.course_ids) - {c.course_id for c in selected}:
                raise ApplicationError("PERMISSION_DENIED", "One or more requested courses are not accessible.")
            return selected
        return courses

    @staticmethod
    def _validate(query):
        if query.document_mode not in ("auto", "existing", "refresh"):
            raise ApplicationError("INVALID_QUERY", "Unknown document refresh mode.")
        if query.types is not None and (not query.types or set(query.types) - set(TYPES)):
            raise ApplicationError("INVALID_QUERY", "Unknown or empty deadline type filter.")
        if query.submission_statuses is not None and (not query.submission_statuses or set(query.submission_statuses) - set(STATUSES)):
            raise ApplicationError("INVALID_QUERY", "Unknown or empty submission status filter.")
        if query.limit is not None and (type(query.limit) is not int or not 1 <= query.limit <= 1000):
            raise ApplicationError("INVALID_QUERY", "Limit must be an integer between 1 and 1000.")

    def _plan(self, query):
        if query.types == ("assignment",):
            return [c for c in self.collectors if c.source_type in ("canvas_assignment", "canvas_calendar_assignment")]
        return list(self.collectors)

    def _collect(self, courses, plan, time_range, document_courses=()):
        raw, coverage, warnings = [], [], []
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(c.collect_for_reconciliation if course.course_id in document_courses and hasattr(c, "collect_for_reconciliation") else c.collect,
                                course_id=course.course_id, start=time_range.start, end=time_range.end): (course, c)
                    for course in courses for c in plan}
            for future in as_completed(jobs):
                course, collector = jobs[future]
                verified = self.clock()
                try:
                    rows = future.result()
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    coverage.append(Coverage(course.course_id, collector.source_type, "unavailable", None, verified, error.code))
                    warnings.append(f"{course.course_code or course.course_id}: {collector.source_type} unavailable ({error.code}); totals are incomplete.")
                    continue
                raw.extend(RawRecord(collector.source_type, course, p, verified) for p in rows)
                coverage.append(Coverage(course.course_id, collector.source_type, "available", len(rows), verified))
        return raw, coverage, warnings

    def _collect_teaching_calendar_feeds(self, courses, course_ids):
        raw, coverage, warnings = [], [], []
        selected = [course for course in courses if course.course_id in course_ids]
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(self.teaching_calendar_collector.collect,
                                course_id=course.course_id): course for course in selected}
            for future in as_completed(jobs):
                course, verified = jobs[future], self.clock()
                try:
                    rows = future.result()
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    coverage.append(Coverage(course.course_id, "canvas_calendar_feed", "unavailable",
                                             None, verified, error.code))
                    warnings.append(f"{course.course_code or course.course_id}: Canvas course calendar feed unavailable ({error.code}); teaching-week references may be incomplete.")
                    continue
                raw.extend(RawRecord("canvas_calendar_feed", course, row, verified) for row in rows)
                coverage.append(Coverage(course.course_id, "canvas_calendar_feed", "available", len(rows), verified))
        return raw, coverage, warnings

    @staticmethod
    def _assignment_id(raw):
        p = raw.payload
        value = p.get("assignment_id") or (p.get("assignment") or {}).get("id")
        if raw.source_type == "canvas_calendar_assignment":
            match = re.fullmatch(r"assignment_(\d+)", str(p.get("id")))
            value = value or (match[1] if match else None)
        return str(value) if value is not None else None

    def _hydrate(self, raw, coverage, warnings):
        assignments = {(r.course.course_id, str(r.payload["id"])): r.payload for r in raw
                       if r.source_type == "canvas_assignment" and r.payload.get("id")}
        missing = {(r.course.course_id, self._assignment_id(r)) for r in raw
                   if r.source_type != "canvas_assignment" and self._assignment_id(r)
                   and (r.course.course_id, self._assignment_id(r)) not in assignments}
        failed = set()
        # Graded quizzes/discussions and synthetic calendar assignments use the
        # student's current assignment override, not embedded generic dates.
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = {pool.submit(self.client.get, f"/api/v1/courses/{cid}/assignments/{aid}",
                                params={"include[]": "submission", "override_assignment_dates": "true"}): (cid, aid)
                    for cid, aid in missing}
            for future in as_completed(jobs):
                key = jobs[future]
                try:
                    value = future.result()
                    if not isinstance(value, dict) or str(value.get("id")) != key[1]:
                        raise ApplicationError("CANVAS_UNAVAILABLE", "Assignment verification returned an unexpected resource.")
                    assignments[key] = value
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    failed.add(key)
                    coverage.append(Coverage(key[0], "canvas_assignment_verification", "unavailable", None, self.clock(), error.code))
                    warnings.append(f"Course {key[0]}: cannot verify personalized assignment dates; linked items were excluded.")
        return assignments, failed

    def query(self, query: DeadlineQuery) -> DeadlineQueryResult:
        self._validate(query)
        # Resolve once before any course fetch or file update, including forced
        # refresh. A midnight rollover during ingestion must not shift the query.
        time_range = self.time_resolver.resolve(query.time_expression, now=self.clock(), start=query.start,
                                                end=query.end, intent=query.time_intent)
        if query.document_mode == "existing" or self.document_refresher is None:
            return self._query_existing(query, time_range=time_range)
        initial = None
        # An exam list/count requires all course PDFs even when Canvas has a hit;
        # one structured exam does not establish that there are no PDF-only exams.
        exam_inventory_required = query.types is not None and "exam" in query.types
        if query.document_mode != "refresh" and not exam_inventory_required:
            try:
                initial = self._query_existing(query, time_range=time_range)
            except ApplicationError as error:
                if error.code != "CANVAS_UNAVAILABLE":
                    raise
            if initial is not None and initial.complete and initial.matched_count > 0:
                return replace(initial, file_library_check={"state": "skipped", "reason": "existing_evidence_sufficient"})
        courses = self._resolve_courses(query)
        try:
            report = self.document_refresher.refresh(courses)
        except ApplicationError as error:
            if error.code == "CANVAS_AUTH_FAILED":
                raise
            report = {"state": "failed", "complete": False,
                      "warnings": [f"File library refresh failed ({error.code}); latest document evidence is unverified."]}
        except (OSError, ValueError, TypeError, KeyError):
            report = {"state": "failed", "complete": False, "warnings": ["File library refresh failed; latest document evidence is unverified."]}
        # Refresh completes before the final structured evidence query. Adapters
        # still never parse PDFs or decide source authority themselves.
        result = self._query_existing(query, time_range=time_range)
        complete = result.complete and report.get("complete", False)
        freshness = result.data_freshness
        if not complete:
            freshness = "mixed_partial" if result.document_summary else "live_partial"
        return replace(result, file_library_check=report, complete=complete, data_freshness=freshness,
                       warnings=result.warnings + tuple(report.get("warnings", ())))

    def _query_existing(self, query: DeadlineQuery, *, time_range=None) -> DeadlineQueryResult:
        self._validate(query)
        time_range = time_range or self.time_resolver.resolve(query.time_expression, now=self.clock(), start=query.start,
                                                              end=query.end, intent=query.time_intent)
        courses = self._resolve_courses(query)
        resolved = ResolvedDeadlineQuery(time_range, query.course_reference, tuple(c.course_id for c in courses),
                                         query.types, query.submission_statuses, query.limit, query.time_intent)
        unique, coverage, warnings, complete, document_summary, unresolved, references = self._prepare_evidence(courses, query, time_range)
        def in_range(d):
            if d.date_only:
                # The document's date remains in the configured evidence zone;
                # a query-zone override must not relabel that source calendar day.
                return (time_range.start.astimezone(self.normalizer.zone).date() <= d.all_day_date <=
                        time_range.end.astimezone(self.normalizer.zone).date())
            return time_range.start.astimezone(timezone.utc) <= d.actionable_at.astimezone(timezone.utc) <= time_range.end.astimezone(timezone.utc)

        selected = [d for d in unique if in_range(d)
                    and (query.types is None or d.type in query.types)
                    and (query.submission_statuses is None or d.submission_status in query.submission_statuses)]
        selected.sort(key=lambda d: (d.actionable_at.astimezone(timezone.utc), d.course_id, d.deadline_id))
        matched_count = len(selected)
        per_course = Counter(d.course_id for d in selected)
        course_counts = tuple((c.course_id, c.course_code, per_course[c.course_id]) for c in courses)
        if query.limit:
            selected = selected[:query.limit]
        reference_selected = [r for r in references
                              if r.window_start_at.astimezone(timezone.utc) <= time_range.end.astimezone(timezone.utc)
                              and r.window_end_at.astimezone(timezone.utc) >= time_range.start.astimezone(timezone.utc)
                              and (query.types is None or r.type in query.types)
                              and (query.submission_statuses is None or r.submission_status in query.submission_statuses)]
        reference_selected.sort(key=lambda r: (r.window_start_at.astimezone(timezone.utc), r.course_id, r.reference_id))
        freshness = "live" if complete else "live_partial"
        if document_summary:
            freshness = "live_with_ingested_documents" if complete else "mixed_partial"
        for d in unresolved:
            warnings.append(f"{d.course_code}: {d.title} is unresolved during reconciliation; not counted.")
        result = DeadlineQueryResult(resolved, len(selected), tuple(selected), self.clock(),
                                   freshness, tuple(warnings),
                                   tuple(sorted(coverage, key=lambda c: (c.course_id, c.source_type))),
                                   complete, matched_count, course_counts, document_summary, unresolved,
                                   reference_deadlines=tuple(reference_selected), reference_count=len(reference_selected))
        if self.document_ingestion is not None:
            try:
                matches = self.document_ingestion.search_content(courses, types=query.types,
                    extra_exam_keywords=self.classifier.keywords)
                result = replace(result, document_content_matches=matches)
            except (ApplicationError, OSError, ValueError, TypeError, KeyError):
                result = replace(result, complete=False,
                    data_freshness="mixed_partial" if document_summary else "live_partial",
                    warnings=result.warnings + ("Document page-content search failed; evidence coverage is incomplete.",))
        return result

    def _prepare_evidence(self, courses, query, time_range):
        docs, document_summary, doc_warnings, docs_complete = [], (), [], True
        relative_evidence = ()
        if self.document_ingestion is not None:
            try:
                docs, document_summary, doc_warnings, docs_complete = self.document_ingestion.load_deadlines(courses)
                relative_evidence = self.document_ingestion.load_relative_week_evidence(courses)
            except ApplicationError as error:
                docs_complete = False
                doc_warnings.append(f"Official document evidence is unavailable ({error.code}); totals are incomplete.")
        relative_courses = {e.candidate.course_id for e in relative_evidence}
        document_courses = {d.course_id for d in docs} | relative_courses
        plan = self._plan(query)
        if document_courses and not any(c.source_type == "canvas_calendar_event" for c in plan):
            calendar = next((c for c in self.collectors if c.source_type == "canvas_calendar_event"), None)
            if calendar is not None:
                plan.append(calendar)
        raw, coverage, warnings = self._collect(courses, plan, time_range, document_courses)
        # The course ICS feed is a bounded fallback for unresolved teaching-week
        # expressions only. Absolute document deadlines do not need it and must
        # not become partial merely because a feed is absent.
        feed_raw, feed_coverage, feed_warnings = self._collect_teaching_calendar_feeds(courses, relative_courses)
        coverage.extend(feed_coverage)
        warnings.extend(feed_warnings)
        warnings.extend(doc_warnings)
        week_mappings, week_warnings = self.teaching_weeks.build(raw + feed_raw)
        warnings.extend(week_warnings)
        references = self._reference_deadlines(relative_evidence, week_mappings, courses)
        if coverage and not any(c.state == "available" for c in coverage) and not docs and not document_summary:
            raise ApplicationError("CANVAS_UNAVAILABLE", "None of the required Canvas sources could be verified.")
        if coverage and not any(c.state == "available" for c in coverage):
            warnings.append("All required Canvas collectors are unavailable; document excerpts may be shown only as unconfirmed references, not a full total.")
        assignments, failed = self._hydrate(raw, coverage, warnings)
        normalized = []
        invalid = False
        for record in raw:
            if (record.course.course_id, self._assignment_id(record)) in failed:
                continue
            try:
                deadline = self.normalizer.normalize(record, assignments, allow_undated=True)
                if deadline:
                    deadline = self.classifier.classify(deadline)
                if deadline and deadline.actionable_at:
                    deadline = self.validator.validate_deadline(deadline, now=self.clock())
            except ApplicationError as error:
                invalid = True
                warnings.append(f"Course {record.course.course_id}: a {record.source_type} item was excluded ({error.code}).")
                continue
            if deadline:
                normalized.append(deadline)
        for doc in docs:
            try:
                normalized.append(self.validator.validate_deadline(self.classifier.classify(doc), now=self.clock()))
            except ApplicationError:
                invalid = True
                warnings.append("Unvalidated document deadline excluded; document evidence requires review.")
        reconciled, reconciliation_warnings, reconciliation_complete = self.reconciler.reconcile(normalized)
        warnings.extend(reconciliation_warnings)
        unresolved = tuple(d for d in reconciled if d.reconciliation_status in ("ambiguous", "unresolved_conflict"))
        unique = self.deduplicator.deduplicate([d for d in reconciled if d not in unresolved])
        complete = not invalid and docs_complete and reconciliation_complete and all(c.state == "available" for c in coverage)
        return unique, coverage, warnings, complete, document_summary, unresolved, references

    def _reference_deadlines(self, evidence_rows, mappings, courses):
        course_map = {c.course_id: c for c in courses}
        result = []
        for evidence in evidence_rows:
            candidate = evidence.candidate
            resolved = self.teaching_weeks.resolve(candidate.course_id, candidate.evidence_text, mappings)
            course = course_map.get(candidate.course_id)
            if resolved is None or course is None:
                continue
            week, first, last, precision = resolved
            candidate_validation = ValidationInfo("unresolved", evidence.validation.rule_version,
                                                  evidence.validation.validated_at, evidence.validation.reasons)
            document_url = evidence.source_url + (f"#page={candidate.page}" if
                                                   (candidate.location or "").startswith("page ") else "")
            document_source = SourceReference(
                "official_document" if evidence.source_verified else "provisional_canvas_document",
                None, document_url, evidence.parsed_at,
                candidate.document_id, evidence.document_name, candidate.page, candidate.evidence_text,
                evidence.document_sha256, evidence.parsed_at, candidate_validation,
                value_kind="teaching_week_reference", date_only=True,
                extraction_mode=evidence.extraction_mode, ocr_engine=evidence.ocr_engine,
                ocr_confidence=evidence.ocr_confidence, location=candidate.location,
            )
            checks = ("DOCUMENT_RELATIVE_WEEK_EVIDENCE", "CANVAS_COURSE_CALENDAR_MAPPING",
                      "NON_CANONICAL_REFERENCE")
            if evidence.source_verified:
                checks += ("OFFICIAL_REGISTRY",)
            else:
                checks += ("SOURCE_APPROVAL_REQUIRED",)
            validation = ValidationInfo("reference", "teaching-week-resolver-v1", self.clock(), checks)
            confidence = "high" if evidence.source_verified and week.confidence == "high" else (
                "medium" if evidence.source_verified else "low")
            result.append(ReferenceDeadline(
                f"course_{course.course_id}_reference_{candidate.candidate_id}", course.course_id,
                course.course_code, course.course_name, candidate.title,
                self.classifier.classify_title(candidate.title, "event"), first, last, precision,
                week.resolution, confidence, "unknown", (document_source,) + week.sources, validation,
            ))
        return tuple(result)

    def ingest_document(self, document_id):
        if self.document_ingestion is None:
            raise ApplicationError("INVALID_CONFIG", "Official document ingestion is not configured.")
        document = self.document_ingestion.registry.get(document_id)
        courses = self.list_courses(document.course_id)
        return self.document_ingestion.ingest(document_id, courses[0])

    def semantic_review_requests(self, document_id, *, offset=0, limit=20):
        if self.document_ingestion is None:
            raise ApplicationError("INVALID_CONFIG", "Official document ingestion is not configured.")
        document = self.document_ingestion.registry.get(document_id)
        courses = self.list_courses(document.course_id)
        return self.document_ingestion.semantic_review_requests(document_id, courses[0], offset=offset, limit=limit)

    def apply_semantic_review(self, document_id, payload):
        if self.document_ingestion is None:
            raise ApplicationError("INVALID_CONFIG", "Official document ingestion is not configured.")
        document = self.document_ingestion.registry.get(document_id)
        courses = self.list_courses(document.course_id)
        return self.document_ingestion.apply_semantic_review(document_id, courses[0], payload)

    def list_documents(self):
        if self.document_ingestion is None:
            return []
        _, summary, _, _ = self.document_ingestion.load_deadlines(self.list_courses())
        return list(summary)

    def upcoming(self, *, days=7, course_reference=None, types=None, submission_statuses=None, limit=None, document_mode="auto"):
        if type(days) is not int:
            raise ApplicationError("INVALID_QUERY", "Days must be an integer.")
        return self.query(DeadlineQuery(time_intent=TimeIntent("rolling_days", f"next {days} elapsed days", days=days),
                                       course_reference=course_reference,
                                       types=types, submission_statuses=submission_statuses, limit=limit, document_mode=document_mode))

    def get_deadline(self, deadline_id):
        document_match = re.fullmatch(r"course_(\d+)_document_([a-f0-9]{24})", deadline_id)
        if document_match:
            courses = self.list_courses(document_match[1])
            intent = TimeIntent("rolling_days", "document detail verification horizon", days=180)
            q = DeadlineQuery(time_intent=intent, course_ids=(document_match[1],))
            time_range = self.time_resolver.resolve(None, now=self.clock(), intent=intent)
            unique, _, _, _, _, _, _ = self._prepare_evidence(courses, q, time_range)
            matches = [d for d in unique if d.deadline_id == deadline_id or any(
                d_id == f"document_candidate:{document_match[2]}" for d_id in d.identities)]
            if not matches:
                raise ApplicationError("DEADLINE_NOT_FOUND", "The document deadline is unresolved or no longer in the current evidence scope.")
            return matches[0]
        match = re.fullmatch(r"course_(\d+)_(assignment|quiz|discussion|calendar_event|calendar_assignment)_(\d+|assignment_\d+)", deadline_id)
        if not match:
            raise ApplicationError("INVALID_QUERY", "Use a deadline_id returned by the engine.")
        cid, kind, rid = match.groups()
        courses = [c for c in self.list_courses() if c.course_id == cid]
        if not courses:
            raise ApplicationError("PERMISSION_DENIED", "The deadline's course is not currently accessible.")
        endpoints = {"assignment": "assignments", "quiz": "quizzes", "discussion": "discussion_topics"}
        path = f"/api/v1/courses/{cid}/{endpoints[kind]}/{rid}" if kind in endpoints else f"/api/v1/calendar_events/{rid}"
        params = {"include[]": "submission", "override_assignment_dates": "true"} if kind == "assignment" else None
        try:
            payload = self.client.get(path, params=params)
        except ApplicationError as error:
            if error.code == "SOURCE_UNAVAILABLE":
                raise ApplicationError("DEADLINE_NOT_FOUND", "The Canvas deadline is no longer available.") from None
            raise
        if not isinstance(payload, dict) or str(payload.get("id")) != rid:
            raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned an unexpected resource.")
        if kind.startswith("calendar_") and not belongs_to_course(payload, cid):
            raise ApplicationError("PERMISSION_DENIED", "This event does not belong to the requested course.")
        if payload.get("is_announcement") or payload.get("hidden") or payload.get("workflow_state") == "deleted":
            raise ApplicationError("DEADLINE_NOT_FOUND", "The Canvas item is not a supported deadline.")
        raw = RawRecord(f"canvas_{kind}", courses[0], payload, self.clock())
        coverage, warnings = [], []
        assignments, failed = self._hydrate([raw], coverage, warnings)
        if failed:
            raise ApplicationError("CANVAS_UNAVAILABLE", "The deadline's personalized dates could not be verified.")
        deadline = self.normalizer.normalize(raw, assignments)
        if deadline is None:
            raise ApplicationError("DEADLINE_NOT_FOUND", "The Canvas item no longer has a supported due or scheduled time.")
        if self.document_ingestion is not None and self.document_ingestion.registry.list_documents((cid,)):
            q = DeadlineQuery(course_ids=(cid,))
            time_range = self.time_resolver.resolve(None, now=self.clock(), start=deadline.actionable_at - timedelta(days=1),
                                                     end=deadline.actionable_at + timedelta(days=1))
            unique, _, _, _, _, _, _ = self._prepare_evidence(courses, q, time_range)
            matches = [d for d in unique if any(s.source_type == raw.source_type and s.canvas_resource_id == rid for s in d.sources)]
            if len(matches) != 1:
                raise ApplicationError("DEADLINE_NOT_FOUND", "The deadline cannot be uniquely resolved from current evidence.")
            return matches[0]
        return self.validator.validate_deadline(self.classifier.classify(deadline), now=self.clock())
