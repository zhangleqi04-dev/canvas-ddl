# ARCHITECTURE — Multi-source Deadline Engine

**Contract v0.12.3 · 2026-09-26**. Product: [PRD_DDL_ONLY.md](PRD_DDL_ONLY.md).
Rules: [AGENTS.md](AGENTS.md). Runtime Skill: [../skills/canvas-ddl/SKILL.md](../skills/canvas-ddl/SKILL.md).

## Document preparation interface

`DeadlineService.prepare_documents(course_references, limit=20)` resolves one to ten
explicit courses and downloads filename-hinted supported files from the authenticated
Canvas Files API. DocumentPreparationService derives a canonical Canvas course/file URL,
content hash and local immutable path, then calls `register_canvas_api`. Registry validation
requires the exact Canvas origin, course path and `canvas-{course_id}-{file_id}` identity.
Canvas Syllabus and published Pages use the same authority path. These sources do not use
operator approval or a manually supplied teaching period.

GET-only downloads use bounded server-issued URLs; bearer credentials stay on the Canvas
origin and signed download URLs are never persisted. Configured CDN/document hosts are
transport allowlists, not authority. `DeadlineService.approve_document(...)` remains only
for external/manual official course documents; it requires a real operator, verified
course/type/period and unchanged hash. Ordinary Canvas refresh never calls it.

## Query orchestration and file library refresh

`DeadlineQuery.document_mode` = auto (default), existing or refresh. Auto and refresh
verify every scoped supported document before all deadline queries, including complete
positive broad and non-exam results. There is no positive-result freshness bypass.
`existing` is the explicit cached/offline opt-out and never checks. Query/course/auth errors abort before fallback. The final query re-fetches live
Canvas after update/ingestion, preserving reconcile-before-filter and count invariants.

DocumentLibraryRefresher lists Canvas Files and Canvas Syllabus/Page HTML and tracks remote
metadata plus SHA-256 in `documents/library-index.json`. Unchanged metadata with current
parser/validator artifacts skips download and parsing; uncertain metadata uses a bounded
download/hash comparison. Every new authenticated Canvas resource is automatically
registered and ingested. Existing Canvas resources update their immutable path, hash and
version history automatically. A failed, disappeared or known-invalid current version sets
`refresh_blocked` and withholds old facts. External/manual sources remain operator-managed;
they cannot inherit trust from a matching filename. Modules fallback is partial and a scan
limit cannot mark unprocessed files deleted.

`file_library_check` exposes scope, coverage, actions, warnings and check time. It is library
check metadata, not document extraction time. Missing reviews, access/download/parse failures,
unprocessed items and incomplete inventory keep totals partial.

## Boundaries

Codex interprets intent and formats results. The engine owns evidence authority,
date/course resolution, extraction validation, source reconciliation,
deduplication, filtering, ordering, counts and freshness. All external adapters
call `DeadlineService`; domain code never imports CLI/Skill code.

```text
Maintenance ingestion path:
DeadlineService.ingest_document(trusted document_id)
  → OfficialDocumentRegistry → resolved current Course
  → DocumentParser (format dispatch; local OCR where applicable) → StructuralChunker
  → LightSemanticPrefilter (broad temporal anchors only; no deadline classification)
  → bounded Codex Skill semantic review (`codex-semantic-v2`)
  → SemanticDeadlineCandidate → DeadlineValidator
  → DocumentRepository (parsed units + semantic review + validation audit)

Query path:
Codex semantic intent / CLI → DeadlineService
  → validate TimeIntent / TimeRangeResolver once before network
  → unless explicitly existing: always run DocumentLibraryRefresher for scoped courses
  → CourseDocumentInventory + CanvasCourseContentInventory
  → all supported file MIME/extensions + Canvas Syllabus/Pages; Modules fallback marked partial
  → new/changed Canvas API resources: automatic registration + ingestion; external/manual: operator trust
  → reuse resolved TimeRange / CourseResolver / collection plan
  → live Canvas collectors + full-term Calendar Events + course ICS fallback for courses with Week N evidence
  → TeachingWeekResolver (course start/direct Week labels/recess records)
  → DocumentIngestionService.load_deadlines(courses) + persisted relative candidates
  → DeadlineNormalizer → DeadlineClassifier → DeadlineValidator
  → DeadlineReconciler → DeadlineDeduplicator → filter → sort → limit → count
  → cached-page literal keyword/context search (no fact conversion)
  → range/type-filtered ReferenceDeadline watchlist (separate from canonical count)
  → DeadlineQueryResult + unconfirmed evidence channels → JSON → Codex
  → confirmed results + separate relevant unconfirmed references
```

## Components

- `canvas/`: GET transport, auth, timeout/retry/pagination, safe application errors.
  No document authority, title classification, counters or date logic.
- `collectors/`: assignments, quizzes, discussions, Calendar Events and a bounded
  official course ICS fallback. No canonical selection or final count.
- `courses/`: retrieval models, deterministic matching and ambiguity.
- `documents/models.py`: OfficialDocument, provisional DocumentDraft, DocumentPage, ParsedDocument,
  DeadlineCandidate, CandidateValidation. Candidates are not Deadlines.
- `documents/registry.py`: validates two authority modes: exact authenticated Canvas
  origin/course/resource identity (`canvas_api`) or explicit external/manual operator
  course/period/hash trust (`operator`). No generic discovery or RAG.
- `documents/parser.py`: bounded format dispatch for PDF, DOCX, PPTX, XLSX, CSV,
  TXT/Markdown, RTF, HTML and standalone images. It verifies SHA-256, OOXML archive
  bounds and format-specific text units; PDF/image inputs have local OCR fallback.
- `documents/canvas_content.py`: bounded read-only discovery of Canvas Syllabus and
  published course Page HTML. Exact course-scoped API identity grants `canvas_api` authority.
- `documents/ocr.py`: lazy PP-OCRv6 Small detection/recognition, bounded 150-DPI
  page rendering and per-line confidence. It proposes page text and owns no facts.
- `documents/refresh.py`: scoped library/version checks, Canvas automatic registration/
  version updates, stale evidence blocking, external pending discovery and query audit.
- `documents/extractor.py`: legacy/high-recall rule prefilter retained for migration
  and provisional diagnostics; it is not authoritative in runtime semantic mode.
- `documents/semantic.py`: deterministic structural chunks, a broad temporal-anchor
  prefilter, plus strict Codex review
  schema. It verifies exact chunk/title/evidence/date anchoring before producing
  candidates and supports several events/dates per chunk. Source text is untrusted.
- `deadlines/validator.py`: independent literal/page/scope/version/date/role
  validation plus unified aware Deadline invariants.
- `deadlines/teaching_weeks.py`: deterministic course-local Week N mapping from
  full-term Canvas Calendar Events or parsed official course ICS events. It never
  reads an external academic calendar, source document or natural-language prompt.
- `documents/repository.py`: atomic per-document SQLite replacement of parsed
  source units, semantic reviews, candidate proposals and validation audit. SQL parameters are bound.
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
CANVAS_DOCUMENT_REGISTRY. The default evidence store is `data/documents.sqlite3`; override
with CANVAS_DOCUMENT_STORE. Paths resolve relative to `.env`.

A `canvas_api` row is created only by authenticated course-scoped discovery. Validation
requires HTTPS, the configured Canvas hostname, an exact `/courses/{course}/files/{id}`
(or Canvas Syllabus/Page) path, matching document ID, supported canonical local artifact,
course identity and SHA-256. `approved_by` must be empty; valid_from/valid_until are not used
for semantic date acceptance. Additional `CANVAS_DOCUMENT_HOSTS` are download transports and
cannot satisfy this authority check.

An `operator` row represents an external/manual official source and still requires document
ID, course ID/code/name, supported kind/name, permitted HTTPS source URL, local path, SHA-256,
real `approved_by` and valid_from/valid_until. Codex, LLM, placeholder identities and document
self-claims are rejected. Ingestion consumes trusted registry IDs only.

Document-only canonical timing is supported. SQLite is durable derived evidence, not a cache
of live Canvas truth. Registry removal, replacement or `refresh_blocked` prevents old stored
versions from serving facts; re-ingestion atomically replaces one document and retains audit.

## Candidate validation

For Codex semantic candidates, confirmation requires matching document/course/hash, exact
engine-issued evidence and date spans, supported role/type/status, and a valid source trust
path. Schema v2 separates the literal `date_expression` from `normalized_date` and
`normalized_time`. Python accepts a normalized value only when it matches a possible literal
reading: full ISO/Chinese/English dates, numeric day/month or month/day with two/four-digit
year, ordinal English month dates with one unambiguous document context year, and 12/24-hour
clock values. This allows multiple dates/events per chunk. Impossible mappings, unclear
year context, ambiguity, tentative/availability text and insufficient OCR confidence remain
unresolved or rejected. Operator documents additionally enforce approved course period;
Canvas API documents enforce authenticated origin and course scope.

Legacy candidates retain complete-line/exactly-one-date checks but are withheld when runtime
semantic review is required. Persist schema/request/reason, original and normalized values,
status, rule version, checks and time. Final queries parse stored strict reviews again and
require the exact candidate set; missing, extra or altered rows withhold the document. Week N
remains unresolved at ingestion and may only become a non-canonical ReferenceDeadline through
TeachingWeekResolver. Stored confirmed flags and LLM confidence cannot bypass revalidation.

## Model and public schema

Deadline keeps deadline_id, nullable canvas_resource_id, course metadata, title,
type, start/due/end, submission_status, source_type/url and last_verified_at.
It adds/uses sources: tuple[SourceReference], validation: ValidationInfo,
conflicts: tuple[SourceConflict], reconciliation_status, canonical_reason,
all_day_date/date_only and separate unlock/lock availability fields.

SourceReference supports Canvas ID or document ID/name/hash/page-compatible index/location/evidence_text,
source authority, source URL, ingestion time, verification/validation metadata, value_at/value_kind
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

Query-scoped document loading retrieves all trusted ingested deadlines in the
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
operator approval applies only to external/manual sources and is never a query fallback action; Canvas API registration is automatic.

Follow-up document IDs resolve through current registry, persisted validation
and live reconciliation. Old Canvas IDs retain their existing access/timing
checks. The canonical query response is authoritative for source conflicts;
details must not bypass multi-source policy to return an inconsistent winner.

## Completeness, freshness and counts

Public JSON: status/complete, resolved query, count, matched_count/course_counts,
deadlines, generated_at, warnings, coverage, document_summary, unresolved_deadlines
file_library_check, document_content_matches and evidence_scope. Count equals returned canonical list length. Before-limit
counts are engine-provided, not LLM arithmetic.

Presentation projects this response into two independent views. The confirmed view uses
only canonical `deadlines`. The unconfirmed view considers `reference_deadlines`,
`unresolved_deadlines`, candidate issues and `document_content_matches`, and is emitted
whenever semantically relevant possible scheduling evidence exists, including when the
confirmed view is non-empty. Codex may remove obvious non-scheduling keyword contexts,
but it cannot promote an excerpt, resolve its date/source, or change counts. Every shown
reference retains exact evidence, provenance/location and its unresolved reason. Without
an engine-resolved reference window, the presentation states that time-range membership
is unknown.

`live`/`live_partial`: no ingested document inventory in result. With document
inventory use `live_with_ingested_documents`/`mixed_partial`. Document timestamps
retain ingestion/validation semantics, never imply live document rereading. Partial
includes failed sources, missing trusted ingestion/version, incomplete Codex semantic review, empty pages,
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
Also test positive/zero/partial auto refresh, broad-query PDF-only discovery,
changed positive document dates, forced/existing modes,
scoped file checks, unchanged/identical hashes, source-identity update permission,
pending sources, missing/update failures/limits and stale fact withholding/recovery.

Existing time/course/Canvas regression tests remain mandatory. No tests prove
natural-language skill selection by merely matching instruction wording.
Presentation acceptance fixtures must cover relevant unconfirmed evidence beside both
zero and positive canonical results, unchanged counts/provenance, unresolved time-window
wording, and exclusion of practice/example/statistical/negated contexts.

## All-document inventory and provisional content contracts

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
with per-item/total limits. Authenticated Canvas Files/Syllabus/Pages register automatically and use the main evidence
store. DocumentScanService handles only untrusted external/manual supported artifacts,
storing source units/audit in a separate `.pending.sqlite3` database.
DocumentDraft is never a registry entry; date.min/date.max only permit syntax checks.
SOURCE_APPROVAL_REQUIRED and COURSE_PERIOD_APPROVAL_REQUIRED prevent provisional external/manual confirmation;
reloading recomputes checks, so forged stored confirmed flags cannot grant authority.
Removed/inaccessible provisional records cannot be presented as current evidence.

DocumentContentSearcher searches every persisted source unit with deterministic academic
keywords and exposes at most 10 context excerpts per document, 1500 characters per
excerpt. match_count/truncated/excerpt_truncated expose output truncation, not an
exam count. Context can contain split-line exam/date evidence or unrelated uses of
“test”; neither is a validated Deadline. Output includes course, name/hash, page,
source URL, source_verified, scanned_at and unvalidated_excerpts. Skill may present
relevant text as an unconfirmed reference with risks, never choose source trust.
This separate presentation is required whenever relevant possible scheduling evidence
exists; a nonzero canonical count must not suppress it.
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



## Semantic time interface

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
