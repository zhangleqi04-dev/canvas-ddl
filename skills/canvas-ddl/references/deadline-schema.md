# Multi-source JSON contract

All scripts call DeadlineService. Normal results are one UTF-8 JSON object;
exit 0 for ok/partial, exit 2 for errors. Credentials/raw Canvas bodies are absent.

## Query results

status: ok/partial/needs_input/error. complete means requested Canvas sources and
registered official document artifacts are checked; it does not mean all course documents
in the course were discovered. evidence_scope explains the supported inventory.
Any provisional reference evidence is explicitly labeled and never counted.

query has time_intent (structured input, or null only for an explicit aware start/end call), resolved inclusive
start/end/timezone, range_expression (original_text for structured calls), course IDs,
type/status filters and limit. count equals canonical returned list length after
reconcile/deduplicate/filter/limit. matched_count and course_counts precede limit;
partial counts are not full totals. warnings retain trust limitations.

coverage gives per-course Canvas source status, successful record count, safe
error code and verification time. document_summary gives registered document ID,
name, course, ingestion state/time, confirmed/unresolved/rejected candidate counts
and candidate issues. unresolved_deadlines holds unresolved identity/source
conflicts, with evidence, but never contributes to count.
document_summary additionally exposes source_url and valid_from/valid_until;
candidate issues expose original evidence_text and validation rule/time.

reference_deadlines is a separate high-recall list for persisted Week N evidence
bounded by full-term Canvas Calendar Events or official course ICS anchors.
reference_count equals its
length and never contributes to canonical count/matched_count/course_counts. Each
reference has course/title/type, window_start_at/window_end_at, teaching_week or
day precision, resolution, confidence, unknown submission status, validation and
document plus calendar SourceReferences. `reference` validation and
NON_CANONICAL_REFERENCE mean the item must be presented as unconfirmed. A
provisional_canvas_document source retains SOURCE_APPROVAL_REQUIRED risk.

file_library_check is null when no refresh orchestrator is used/existing mode;
otherwise skipped (reason=existing_evidence_sufficient), checked (checked_at,
course_ids, complete, actions, warnings) or failed. Actions include unchanged,
updated/ingested, pending_review, inaccessible, missing or failed. This field is
library-check metadata, never an exam count or document extraction timestamp.

Freshness: live/live_partial, live_with_ingested_documents/mixed_partial. Details
may use ingested_document for document-only evidence. generated_at is response
generation, not a fresh document verification timestamp.

## Deadlines and sources

Existing fields: deadline_id, nullable canvas_resource_id, course_id/code/name,
title, type, start_at/due_at/end_at, submission_status, source_type/url,
last_verified_at, all_day_date/date_only, unlock_at/lock_at.

Additional facts: validation (status/rule_version/validated_at/checks),
reconciliation_status, canonical_reason, conflicts and multiple sources.
Document sources preserve document_id/name, page-compatible unit index, location,
evidence_text, document_sha256,
ingested_at, validation, value_at/value_kind/date_only, extraction_mode, ocr_engine,
ocr_confidence and original source URL.
Canvas sources retain resource identity and verified structured value.

Conflict fields: field, canonical_value, alternative_value, alternative_source
(full SourceReference) and resolution. live_canvas_preferred means the displayed
value is canonical; do not substitute the document alternative. unresolved conflicts
and ambiguous identities require review. Date-only evidence never states midnight
as an actual exam time. due→start→end is actionable priority; unlock/lock is availability.

Types: assignment/exam/quiz/discussion/event/other. Submission status:
submitted/not_submitted/late/missing/unknown; document-only submission is unknown.
Do not infer missing from time or submitted from offline grading alone.

## Ingestion and authority

Operator-approved official course-document registry is separate from extraction.
Ingestion parses once, extracts candidates, validates literal source-location evidence/explicit date/
course/content version/role, then persists parsed evidence and validation audit.
Only confirmed candidates become Deadline. Default document_mode=auto first uses
existing evidence, always checks all scoped supported documents and Canvas Syllabus/Pages for exam queries, allows complete
positive non-exam results directly, and checks the
scoped library for zero/partial results. refresh always checks first; existing
never checks. Authorized same-file versions may be re-ingested in the update stage;
final queries revalidate persisted evidence without source-document parsing. Unchanged files
are reused. New source IDs remain pending. Changed unauthorized/disappeared/known
failed new versions set refresh_blocked and withhold old facts. Week expressions and
low-confidence OCR remain unresolved at ingestion. A deterministic query-time
TeachingWeekResolver can bound one Week N expression from Canvas Calendar Events or
the engine's safe official course ICS fallback as a ReferenceDeadline only; it never
promotes it into canonical count. Empty scanned pages,
missing ingestion, changed versions and unresolved/rejected coverage make the
query partial. No unreviewed filename/URL/LLM confidence grants source authority.

## Errors

Safe error.code/message. AMBIGUOUS_COURSE also gives candidates. Retain prior
Canvas/time/query/config codes, plus INVALID_DOCUMENT_REGISTRY,
DOCUMENT_NOT_REGISTERED, DOCUMENT_COURSE_MISMATCH, DOCUMENT_HASH_MISMATCH,
DOCUMENT_PARSE_FAILED, DOCUMENT_STORE_UNAVAILABLE and INVALID_DOCUMENT_EXTRACTION.
401 aborts; other source failures can yield partial document/Canvas facts. No
stale Canvas cache fallback. Canonical details use the same reconciliation policy.

## Provisional course-document content (v0.9)

file_library_check.scope=all_course_documents; coverage records course_id/code,
listed_document_count/processed_document_count/unprocessed_document_count and access errors.
module_fallback_partial cannot prove complete inventory. Actions may include scan
state/pages/candidate_count/confirmed/scanned_at/ocr_pages/empty_pages. Attempted processing
counts are not successful document parse counts; inspect action failures/scan states.
Default refresh has no file-count cap. existing explicitly skips inventory checks.

Unapproved supported documents and Canvas Syllabus/Page HTML are parsed into an isolated provisional database, never the approved
fact store. document_summary.source_verified=false and candidate issues retain
original text/location/validation checks/reasons. SOURCE_APPROVAL_REQUIRED and
COURSE_PERIOD_APPROVAL_REQUIRED ensure no provisional proposal is confirmed.
proposed_value_at, if present, is a literal syntax proposal, never canonical timing.

Result.document_content_matches is separate from deadlines and counts. Each entry
has document_id/name, course_id/code, source_url, source_verified, content_hash,
scanned_at, unvalidated_excerpts=true, matches[{page,location,evidence_text,excerpt_truncated}],
match_count and truncated. Every persisted source unit is searched; response limits expose
omitted/truncated context. Match counts include unrelated keywords and are not exams.
Neither excerpts nor unresolved proposals can bypass source/date validation.

Parser v3 retains rotated text; zero-width layout failures may use the plain text
extractor. Low-text pages may use local PP-OCRv6 Small during ingestion. Persist
per-page extraction_mode, ocr_engine and aligned line confidence; expose extraction
mode on context excerpts and ocr_pages/plain_fallback_pages in scan actions. OCR
evidence below CANVAS_OCR_MIN_CONFIDENCE is unresolved. OCR failure or absence keeps
coverage partial. This does not confer source authority or guarantee table ordering.

Time input schema and ambiguity rules: [time-intent.md](time-intent.md).
