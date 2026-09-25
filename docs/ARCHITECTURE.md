# ARCHITECTURE — Multi-source Deadline Engine

**Contract v0.10 · 2026-09-25**. Product: [PRD_DDL_ONLY.md](PRD_DDL_ONLY.md).
Rules: [AGENTS.md](AGENTS.md). Runtime Skill: [../skills/canvas-ddl/SKILL.md](../skills/canvas-ddl/SKILL.md).

## Document preparation interface

`DeadlineService.prepare_documents(course_references, limit=20)` resolves one to ten
explicit courses, then delegates to DocumentPreparationService. Course Files API
metadata supplies candidates; filename hints select syllabus/outline/handout
documents in supported formats. GET-only bounded downloads use server-issued URLs, safe local hash-versioned
paths and a separate pending-review.json. Canvas token stays on-origin. Published
Canvas storage destinations and explicitly configured hosts can receive downloads,
never bearer credentials off-origin; signed URLs are not persisted/output.

`DeadlineService.approve_document(document_id, approved_by, valid_from, valid_until)`
is an explicit maintenance operation. CLI additionally requires --confirm-official.
The operator reviews the document's source/type and period. Preparation never writes
the approved registry; approval checks content hash and validates a proposed registry
before atomic replacement, then calls the existing ingest_document pipeline. Existing
approvals are preserved across repeat preparations. Ordinary queries never call
operator approval. They can call the separate refresh stage described below.

## Query orchestration and file library refresh

`DeadlineQuery.document_mode` = auto (default), existing or refresh. `query()` wraps
`_query_existing()`, which retains the canonical evidence pipeline. Auto first calls
the existing pipeline: complete + matched_count>0 skips files only for non-exam queries.
Exam queries always refresh all scoped supported documents; zero/partial (or total
Canvas failure which may recover through approved document ingestion) checks files.
Refresh mode checks files first; existing never checks them. Bad query/course/auth
errors abort instead of running fallback. Scoped CourseResolver output controls
every library endpoint. The final query runs after update/ingestion and re-fetches
live Canvas, preserving reconciliation-before-filtering and count invariants.

DocumentLibraryRefresher lists course documents and Canvas Syllabus/Page HTML, then
tracks updated_at/modified_at/size/uuid
and content SHA-256 in documents/library-index.json. Unknown version markers require
download/hash comparison. Unchanged metadata plus valid artifacts skips download and
parsing; changed metadata with identical hash skips parsing. Hash/parser/validator
artifact mismatch triggers ingestion for approved sources. The index holds no signed
URLs. All new documents become inactive pending-review entries with separately persisted provisional
content/validation, not trusted facts. Filename hints never restrict automatic query coverage.

An approved registry's auto_refresh=true is standing operator authorization for
versions at that exact Canvas origin/course/file ID. Preserve type/period/approver,
update hash/path, append version_history and record version_basis before ingestion.
New IDs are never inherited by filename matching. With no standing authorization,
a changed file stays pending and refresh_blocked=true withholds old deadline facts.
Disappeared files and known version changes whose updates fail also block old facts;
successful approved content restoration clears the block. A limit must not classify
unprocessed inventory as deleted. Unknown state after a permissions/network failure
may preserve prior validated evidence with explicitly partial coverage.

QueryResult.file_library_check exposes state/skipped reason or check time, course
scope, actions and warnings. Any pending source/update failure/limit/no-match coverage
keeps totals partial. This timestamp verifies library checking, not extraction or
the freshness of a cached document. Registry/artifact replacement remains per-file;
if ingestion fails after a hash update, old mismatched artifacts cannot become facts.

## Boundaries

Codex interprets intent and formats results. The engine owns evidence authority,
date/course resolution, extraction validation, source reconciliation,
deduplication, filtering, ordering, counts and freshness. All external adapters
call `DeadlineService`; domain code never imports CLI/Skill code.

```text
Maintenance ingestion path:
DeadlineService.ingest_document(approved document_id)
  → OfficialDocumentRegistry → resolved current Course
  → DocumentParser (format dispatch; local OCR where applicable) → DeadlineExtractor → DeadlineCandidate
  → DeadlineValidator → DocumentRepository (SQLite ingestion artifacts)

Query path:
Codex semantic intent / CLI → DeadlineService
  → validate TimeIntent / TimeRangeResolver once before network
  → non-exam existing evidence sufficient? return; exams always DocumentLibraryRefresher
  → CourseDocumentInventory + CanvasCourseContentInventory
  → all supported file MIME/extensions + Canvas Syllabus/Pages; Modules fallback marked partial
  → changed approved versions: ingestion; new/unapproved versions: audited pending scan
  → reuse resolved TimeRange / CourseResolver / collection plan
  → live Canvas collectors + full-term Calendar Events + course ICS fallback for courses with Week N evidence
  → TeachingWeekResolver (course start/direct Week labels/recess records)
  → DocumentIngestionService.load_deadlines(courses) + persisted relative candidates
  → DeadlineNormalizer → DeadlineClassifier → DeadlineValidator
  → DeadlineReconciler → DeadlineDeduplicator → filter → sort → limit → count
  → cached-page literal keyword/context search (no fact conversion)
  → range/type-filtered ReferenceDeadline watchlist (separate from canonical count)
  → DeadlineQueryResult + reference_deadlines + document_content_matches → JSON → Codex
```

## Components

- `canvas/`: GET transport, auth, timeout/retry/pagination, safe application errors.
  No document authority, title classification, counters or date logic.
- `collectors/`: assignments, quizzes, discussions, Calendar Events and a bounded
  official course ICS fallback. No canonical selection or final count.
- `courses/`: retrieval models, deterministic matching and ambiguity.
- `documents/models.py`: OfficialDocument, provisional DocumentDraft, DocumentPage, ParsedDocument,
  DeadlineCandidate, CandidateValidation. Candidates are not Deadlines.
- `documents/registry.py`: operator-maintained explicit official-source/course/
  period/hash approval for supported course documents and Canvas course content on
  approved exact HTTPS hosts. No generic discovery or RAG.
- `documents/parser.py`: bounded format dispatch for PDF, DOCX, PPTX, XLSX, CSV,
  TXT/Markdown, RTF, HTML and standalone images. It verifies SHA-256, OOXML archive
  bounds and format-specific text units; PDF/image inputs have local OCR fallback.
- `documents/canvas_content.py`: bounded read-only discovery of Canvas Syllabus and
  published course Page HTML. It creates provisional artifacts, never trusted facts.
- `documents/ocr.py`: lazy PP-OCRv6 Small detection/recognition, bounded 150-DPI
  page rendering and per-line confidence. It proposes page text and owns no facts.
- `documents/refresh.py`: scoped library/version checks, pending discovery,
  standing authorized version updates, stale evidence blocking and query audit.
- `documents/extractor.py`: bounded proposals, rule-based default; injectable
  LLM extractor may use the same candidate schema. It cannot approve sources,
  write confirmed facts, choose dates or source priority.
- `deadlines/validator.py`: independent literal/page/scope/version/date/role
  validation plus unified aware Deadline invariants.
- `deadlines/teaching_weeks.py`: deterministic course-local Week N mapping from
  full-term Canvas Calendar Events or parsed official course ICS events. It never
  reads an external academic calendar, source document or natural-language prompt.
- `documents/repository.py`: atomic per-document SQLite replacement of parsed
  source units, candidate proposals and validation audit. SQL parameters are bound.
- `documents/ingestion.py`: ingestion orchestration and loading persisted evidence.
  Queries revalidate candidates against persisted text, never reopen source documents.
- `deadlines/normalizer.py`: Canvas personal overrides and validated document
  values into the single Deadline model, preserving provenance and precision.
- `deadlines/reconciler.py`: deterministic source policy and canonical values.
- `deadlines/deduplicator.py`: canonical logical-record uniqueness and provenance union.
- `deadlines/service.py`: the single client-independent application gateway.
- `cli/`, `skills/canvas-ddl/scripts`: arguments, service calls and serialization only.

## Authority configuration

The default registry is `documents/registry.json`; override with
CANVAS_DOCUMENT_REGISTRY. Default SQLite store: `data/documents.sqlite3`, override
with CANVAS_DOCUMENT_STORE. Paths resolve relative to the configured `.env`.
Canvas origin is allowed as an official source host; additional institution
hosts require CANVAS_DOCUMENT_HOSTS. Hosts are exact, not wildcard suffix matches.

Each active registry entry requires document_id, course_id/code/name,
document_name/kind, source_url, local artifact path, approved SHA-256, approved_by,
valid_from/valid_until. The human operator checks officiality and course/period
before setting these fields. This registry is trusted administrative configuration,
not self-authenticating evidence. Do not manufacture human approval or treat an
LLM's filename/URL claim as approval. Ingestion consumes registered IDs only;
there is no `--official` flag that makes arbitrary extractor input authoritative.

Document-only canonical timing is supported. SQLite is durable derived **document
evidence infrastructure**, not a cache of live Canvas truth. Canvas timing is
live; document claims remain tied to the approved content version and original
ingestion/validation timestamps. Missing/replaced/revoked registry records stop
their previous stored version from serving facts. Re-ingestion replaces one
document atomically and persists unresolved/rejected proposals for review.

## Candidate validation

Confirm only when scope/hash match, original evidence matches a complete literal
line of the indicated parsed source unit/location, the title matches deterministic evidence
parsing, exactly one explicit date/year is present, a due/scheduled role is clear,
date/time is legal, and its date lies inside the approved course period.
Proposal date text must match the literal date independently extracted by the
validator. Store status, rule version, checks/reasons and timestamp.

Supported literal dates: ISO yyyy-mm-dd, yyyy年M月D日, day Month year and Month
day, year (full/short English month names); optional single 24-hour HH:MM.
The current strict grammar leaves multiple dates/times, AM/PM/foreign timezone,
release/availability dates, negation/tentative wording and unknown layouts
unresolved or rejected. Relative academic weeks remain unresolved ingestion
candidates with reason `CANVAS_TEACHING_WEEK_REQUIRED`. Page-wide tentative/draft markers
require review, and cropped quotes cannot hide qualifications. It does not infer a year from
the term, an exam clock time from midnight, or a deadline from a lock timestamp.
At query time, persisted relative candidates may be converted only into non-canonical
ReferenceDeadline windows by `TeachingWeekResolver`. An empty scanned page marks coverage incomplete.

Loading stored candidates runs the validator again; stored confirmed flags or
LLM confidence cannot bypass checks. A changed validator version requires
re-ingestion. The evidence database is trusted ingestion output, not a defense
against a local administrator maliciously rewriting all artifacts and approvals.

## Model and public schema

Deadline keeps deadline_id, nullable canvas_resource_id, course metadata, title,
type, start/due/end, submission_status, source_type/url and last_verified_at.
It adds/uses sources: tuple[SourceReference], validation: ValidationInfo,
conflicts: tuple[SourceConflict], reconciliation_status, canonical_reason,
all_day_date/date_only and separate unlock/lock availability fields.

SourceReference supports Canvas ID or document ID/name/hash/page-compatible index/location/evidence_text,
source URL, ingestion time, verification/validation metadata, value_at/value_kind
and date precision. SourceConflict records field, canonical and alternative
value, the full alternative source and deterministic resolution reason.
Internal identities/type hints/personal-date priority are not raw model-facing
Canvas payloads. No Canvas ID is fabricated for document deadlines.

DeadlineCandidate is persisted independently even when unresolved/rejected;
only confirmed evidence can be normalized into a countable Deadline.

ReferenceDeadline is the high-recall, non-canonical model for a persisted Week N
candidate that can be bounded by Canvas course-calendar evidence. It stores
window_start_at/window_end_at, teaching_week or day precision, confidence,
resolution, document provenance, calendar anchors and reference validation. It
never enters reconciliation, deduplication or canonical count.

## Reconciliation vs deduplication

Reconciler processes validated records before filtering. Unique same-course,
normalized-title, classified-type Canvas logical anchors match document evidence even
when dates differ. Direct assignment identities group Canvas views; a uniquely
matching calendar view with the identical time can join that anchor. Same-source
distinct Canvas IDs, similar numbered names or ambiguous repeated document
instances do not silently collapse.

Canonical policy is coded, not prompted:

1. Live Canvas takes precedence for a uniquely identified same logical deadline.
2. Same dates combine sources; date-only document precision agrees with same-day Canvas.
3. Different dates select Canvas but retain conflicts and document evidence.
4. No Canvas counterpart: validated official document becomes canonical.
5. Explicit null personal Canvas due dates block resurrecting old document dates.
6. Ambiguous identity or conflicting official documents without Canvas remain unresolved,
   with no arbitrary latest-document tie-breaker. Affected records are diagnostic
   unresolved_deadlines, not counted; other canonical items may still be shown.

Reconciler emits canonical-value views plus identity/provenance tokens; Deduplicator
then collapses those views. Count is computed only after deduplicate/filter/limit.
Neither component is implemented in adapters or natural-language prompts.

Query-scoped document loading retrieves all approved ingested deadlines in the
selected courses, without prefiltering dates. Assignment collection already sees
course-wide personal dates. When a course has validated document claims or persisted
relative-week evidence, calendar collection checks that course across dates
(`all_events=true`) to detect moved matching events and build a teaching-week map;
the collector includes verified course-section contexts but never an unscoped user
calendar or full-account sync. It also reads the course's official ICS link as a
fallback because some UI-visible course events may not appear in Calendar Events.
Only same-origin `/feeds/calendars/course_*.ics` URLs are accepted, no bearer token
is sent, the response is size-bounded, and the URL is never persisted or returned.
Explicit `Week N` calendar events map directly.
Otherwise, one unambiguous Week 1/first-class/weekly-series-head anchor starts a
Monday–Sunday sequence and explicit recess/reading or Canvas `blackout_date` weeks
are skipped. Conflicting direct/start anchors
suppress that week. Normal queries without document claims use the existing bounded
calendar window.

## Application interfaces

`query(DeadlineQuery)`, `upcoming(days,...)`, `list_courses(reference=None)`,
`get_deadline(id)`, `ingest_document(document_id)`, `list_documents()`.
All CLI/Skill and future MCP paths use these methods. CLI commands are courses,
deadlines, upcoming, deadline, documents, ingest, prepare-documents, approve-document.
Existing four Skill scripts retain query interfaces and add --document-mode for
deadlines/upcoming. Their default auto may perform the staged engine refresh;
operator approval is always explicit and never a query fallback action.

Follow-up document IDs resolve through current registry, persisted validation
and live reconciliation. Old Canvas IDs retain their existing access/timing
checks. The canonical query response is authoritative for source conflicts;
details must not bypass multi-source policy to return an inconsistent winner.

## Completeness, freshness and counts

Public JSON: status/complete, resolved query, count, matched_count/course_counts,
deadlines, generated_at, warnings, coverage, document_summary, unresolved_deadlines
file_library_check, document_content_matches and evidence_scope. Count equals returned canonical list length. Before-limit
counts are engine-provided, not LLM arithmetic.

`live`/`live_partial`: no ingested document inventory in result. With document
inventory use `live_with_ingested_documents`/`mixed_partial`. Document timestamps
retain ingestion/validation semantics, never imply live document rereading. Partial
includes failed sources, missing approved ingestion/version, empty pages,
unresolved/rejected candidate coverage and unresolved reconciliation. Resolved
Canvas/document conflicts preserve warnings but do not invalidate a complete canonical count.

With no registered documents, expose a scope warning; successful Canvas coverage
does not imply all course documents have been discovered. Partial zero never
proves absence. Total Canvas collector failure may still yield verified ingested
document facts as explicitly partial; no stale Canvas cache fallback exists.
401 is fatal; live course access must still resolve. Stable errors expose no secrets.

## Test constraints

Default tests are offline with synthetic supported documents, fake collectors and HTTP fixtures.
Cover document-only canonical, agreement, conflict, moved dates before filtering,
relative weeks, ambiguity, wrong location/text/date/course/hash, LLM proposals,
stored-confirmed bypass attempts, revoked/replaced versions, no final evidence-query source reads,
date-only precision, conflicting documents, uncertain identity, partial freshness,
counts/status filters/sorting, security and read-only Canvas transport.
Also test sufficient-evidence skip, zero/partial auto refresh, forced/existing modes,
scoped file checks, unchanged/identical hashes, source-identity update permission,
pending sources, missing/update failures/limits and stale fact withholding/recovery.

Existing time/course/Canvas regression tests remain mandatory. No tests prove
natural-language skill selection by merely matching instruction wording.

## All-document inventory and provisional content contracts (v0.9; extends v0.5)

CourseDocumentInventory lists all Files metadata without a server-side MIME restriction;
application/octet-stream falls back to a supported extension. Supported types are
PDF, DOCX, PPTX, XLSX, CSV, TXT/Markdown, RTF, HTML and standalone images. There is
no default file-count cap in DocumentLibraryRefresher.refresh(courses, limit=None).
Transport retains a 30MB bound; parsers add format-specific page/unit, text and OOXML
expanded-size bounds. Files 403/unavailability triggers Modules
file-item metadata discovery with deduplication and incomplete-item pagination;
its coverage is module_fallback_partial, never full inventory. 401 remains fatal.
A supplied limit records unprocessed documents and must not imply deletion.

CanvasCourseContentInventory also discovers course Syllabus and published Page HTML
with per-item/total limits. DocumentScanService parses every unapproved supported
artifact through the same extraction/independent validation stages, storing source
units/audit in a separate .pending.sqlite3 database.
DocumentDraft is never a registry entry; date.min/date.max only permit syntax checks.
SOURCE_APPROVAL_REQUIRED and COURSE_PERIOD_APPROVAL_REQUIRED prevent confirmation;
reloading recomputes checks, so forged stored confirmed flags cannot grant authority.
Removed/inaccessible provisional records cannot be presented as current evidence.

DocumentContentSearcher searches every persisted source unit with deterministic academic
keywords and exposes at most 10 context excerpts per document, 1500 characters per
excerpt. match_count/truncated/excerpt_truncated expose output truncation, not an
exam count. Context can contain split-line exam/date evidence or unrelated uses of
“test”; neither is a validated Deadline. Output includes course, name/hash, page,
source URL, source_verified, scanned_at and unvalidated_excerpts. Skill may present
relevant text as an unconfirmed reference with risks, never choose source trust.
Each match additionally exposes `location`; page remains a compatible numeric unit index.

DocumentParser supports AES course PDFs readable with an empty user password.
Password-protected unreadable PDFs still fail safely; no guessing/password bypass,
PDF edits, embedded action execution or relaxation of existing parse bounds.

The PDF parser branch retains rotated text; zero-width layout failures may use the plain text
extractor. Pages with fewer than 16 native text characters may use local PP-OCRv6
Small. `PaddleOcrPageReader` lazily renders only those pages at configurable 96–300
DPI (150 default), batch size 1, using CPU by default. It disables orientation and
unwarping models to limit memory use. The model is initialized once per ingestion
process and downloaded into PaddleX's local cache on first use.

Persist per-unit extraction_mode, OCR engine and aligned line confidences. A deadline
line must meet CANVAS_OCR_MIN_CONFIDENCE (0.90 default) in addition to every existing
literal/source/period validation check. Low confidence is unresolved with
OCR_CONFIDENCE_REQUIRES_REVIEW. Missing dependencies, initialization/inference errors
and empty OCR output remain explicit incomplete coverage. Native text pages never
start OCR. Final queries use persisted evidence and do not load Paddle or reopen source documents.
This does not confer source authority or guarantee table ordering.


## Grading-based midterm reference inference

For “does this course have a midterm”, the Skill may use an engine-returned complete,
non-overlapping grading breakdown for the same course/term/version. If components
total 100%, no midterm share is listed and there is no positive midterm evidence,
it may say “按目前课程评分构成推断，应没有单独计分的期中考试” as a reference inference,
with percentages, document/page and source/coverage risks. Incomplete/truncated,
overlapping, alternative or ambiguous breakdowns cannot support it. A positive
midterm record/reference makes the negative inference inapplicable; source conflicts
remain engine-owned. A midterm may be embedded in homework/continuous assessment or
ungraded, so this never establishes absence of all midterm assessments. Adding weights
is permitted only for this presentation inference; it cannot modify authority,
validation, canonical deadlines, completeness or exam counts, or directly read source documents.



## Semantic time interface (v0.7)

Codex → TimeIntent → TimeRangeResolver → inclusive TimeRange → DeadlineService.
TimeIntent is a frozen, independently validated semantic parameter model in
`deadlines/time_intent.py`; from_dict/from_json reject unknown or inappropriate
fields, null input fields, duplicate keys and unsafe integer/zone/date values.
original_text and optional interpretation are audit strings, never executable
instructions or a parsing authority. Parameters, not these strings, control dates.

DeadlineQuery adds optional time_intent. ResolvedDeadlineQuery retains it and public
query JSON echoes it beside range_expression/start/end/timezone. Resolver accepts
TimeIntent or an explicit aware start/end pair exclusively. It rejects raw natural-language
expressions; no phrase whitelist or regex interpretation remains. It uses the injected aware
clock and requested/default IANA zone, local calendar arithmetic for periods and
UTC elapsed arithmetic for rolling windows and inclusive ends. Weekdays use 0=Mon
through 6=Sun; month arithmetic handles year changes and clips first-N-days at
month end. before excludes the named midnight boundary; remaining begins at now.
Reversed or >366-day elapsed ranges fail. Resolve once at query entry before any
Canvas or refresh side effects, then reuse through fast-path and final query.

CLI adds --time-intent and --time-intent-file (UTF-8/BOM allowed, bounded 32000 bytes,
8000 decoded characters). Parse schema before constructing the service. Invalid
input returns credential-free INVALID_TIME_INTENT; arithmetic errors return
INVALID_TIME_RANGE. Scripts forward flags without an independent implementation.
`upcoming(days)` constructs a rolling-days TimeIntent internally. Tests run the same document-only/reconciliation pipeline
with structured time, including unchanged canonical counts and provenance.
Full operation schema: [time-intent.md](../skills/canvas-ddl/references/time-intent.md).
No external academic calendar or engine-side natural-language/LLM call is used for
teaching weeks. The deterministic TeachingWeekResolver consumes Canvas API and
official course-feed records only.

Date-only facts retain their configured evidence calendar day. For a query-zone
override, compare query bounds in the evidence zone; overlapping source calendar
days can match, but the original date and unknown clock time remain explicit.
