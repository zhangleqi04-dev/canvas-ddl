# PRD — Multi-source Canvas DDL Assistant

**Status:** Implemented contract v0.10 · 2026-09-25
**Deployment:** single-user, local, read-only Canvas PAT  
**Interface:** Codex Skill / CLI; MCP optional and deferred

## Course-scoped automated preparation

Explicit maintenance `prepare-documents --course <reference>` may use the existing
Canvas .env to list course files and download up to 20 filename-hinted supported document candidates.
Course metadata, canonical source links and SHA-256 are filled automatically in
pending-review.json, with active=false and no approver/teaching period. Names are
discovery hints, never authority. Query fallback may check the selected courses'
library through the engine; it never performs full-account synchronization. No-match/access failures do not establish
document coverage. A subsequent explicit operator `approve-document` confirms the
reviewed source/type/period, validates the proposed registry, then runs ingestion.
Codex cannot manufacture approval or silently guess the course period. Explicitly
authorized use of official term/calendar evidence may establish the period with an
audit trail. Canvas storage hosts
are transport destinations only, never evidence authority; auth stays on-origin.

## Product goal

Answer upcoming academic deadline questions using an explicit, auditable evidence
base. Canvas structured data and **approved official course documents** jointly
provide evidence. A verified document-only deadline is valid without a Canvas record.
The system is a deadline engine, not a course tutor or general document chatbot.

Priority: correct range/course → evidence → validation → reconciliation →
deduplication → filtering → count/timezone → provenance → concise presentation.

## Included sources and exclusions

- Query-scoped live Canvas assignments, quizzes, dated discussions and calendar
  events; New Quizzes through their assignment metadata.
- Course-scoped full-term Canvas calendar evidence used to map relative teaching
  weeks, including verified section-level Calendar Events and the official course
  ICS feed returned by the Canvas Course object. The ICS feed is a fallback when
  Calendar Events is empty or incomplete. This uses the course's own start/Week
  labels and recess records; it does not require or consult an external academic
  calendar or unscoped personal calendar.
- Pre-ingested, explicitly approved official course documents tied to a course and
  approved teaching period: PDF, DOCX, PPTX, XLSX, CSV, TXT/Markdown, RTF, HTML and
  standalone PNG/JPEG/WebP/TIFF/BMP images. Canvas Syllabus and published course
  Pages are discovered as official course-content candidates and pass through the
  same pending-review and ingestion pipeline.
- Date-only schedules, precise deadlines, submission status when Canvas supplies
  it, source conflicts and validation diagnostics.

Exclude announcements, email, chat, unofficial notes, arbitrary web documents,
general document content Q&A, legacy DOC/PPT/XLS, ZIP expansion, audio/video
transcription, RAG/vector databases, tutoring, Canvas writes, assignment
submission, full-account synchronization, notifications, OAuth and multi-user hosting.

## Authority and validation

Officiality is established by an **operator-maintained document registry**, not
by filename, document self-description, LLM confidence or Codex reasoning. The registry
records approved course ID/code/name, document kind/name, source URL on a permitted
official host, SHA-256 content hash, approver, local immutable artifact and validity period.
This is a local trust configuration boundary: the operator must verify the
official source and course/period before approving it. A matching URL alone is
not cryptographic proof of origin. Codex must not self-approve a source or invent
an approver. Extraction cannot modify the registry or its authority.

Ingestion is a distinct stage before the final document evidence query. A format
dispatcher selects the bounded PDF, DOCX, PPTX, XLSX, CSV, text, RTF, HTML or image
parser. PDF pages and standalone/embedded presentation images may use local
PP-OCRv6 Small when native text is insufficient:

```text
approved registry → DocumentParser → DeadlineExtractor → DeadlineCandidate
                  → DeadlineValidator → persisted ingestion evidence
                                     → confirmed normalized Deadline
```

The shipped extractor is rule-based. Future/injected LLM extractors may propose
candidates only; independent deterministic validation must still establish the
literal title, source location, evidence text, explicit date/year/time, deadline role,
course binding and approved document version/period. Extraction confidence never
confirms a fact. `confirmed`, `unresolved`, `rejected` are distinct statuses.

Every document-derived deadline preserves document ID/name/hash, original source
URL, format-specific location (page, slide, paragraph, table row, sheet row, text
line, HTML block or image), original evidence text, ingestion timestamp and
validation status, checks, time and rule version. The final evidence query does not
open or parse original documents;
only a preceding update stage can ingest a changed/missing artifact.

## Exhaustive scoped course-document checks for exams

DeadlineQuery.document_mode defaults to auto. An exam list/count query always
checks all supported documents in every selected course, even when live Canvas
already supplies a complete positive answer. A Canvas match cannot prove no
document-only exams exist.
Other queries retain the complete-positive fast path; empty/partial answers refresh.
`existing` explicitly disables checking; `refresh` always checks before querying.
All checks are course-scoped, never a background full-account synchronization.

Automatic refresh lists course files without a remote MIME filter, selecting every
supported modern document by MIME or extension regardless of filename. There is no
default 20-file limit.
An explicit limit reports unprocessed coverage. On unavailable/forbidden Files,
Modules file items may supply accessible documents; this fallback is always partial and
never proves complete course inventory. Access, parse and empty-page failures remain
visible. New documents and Canvas Syllabus/Page HTML are content-scanned into an
isolated provisional store, retaining source units/locations, original text,
candidates and independent validation audit.
Source/period approval is required before any proposal can become a Deadline.
Provisional `DocumentDraft` period bounds are syntax-only placeholders, not authority.

The update stage uses DocumentParser → DeadlineExtractor → DeadlineCandidate →
DeadlineValidator before the final query. Existing approved sources use the approved
store; unapproved scans use `data/documents.pending.sqlite3` (derived from the configured
store path). Their candidates remain unresolved/rejected even if literal dates parse.
Pending proposals and raw text excerpts never enter canonical counts. The engine also
searches all persisted page text for academic keywords and returns bounded context as
`document_content_matches`, including multi-line evidence that extraction may miss.
This is literal evidence search, not general RAG or independent Skill document reading.
Excerpts are unvalidated; statistical tests/examples are not scheduled examinations.
Adapters may quote relevant reference text with its source/page and explicit risk,
without asserting source officiality, inferring dates or computing exam counts.

Unchanged metadata/hash with valid artifacts reuses stored content without parsing.
Changed/missing metadata triggers a hash comparison. An approved exact Canvas file
with operator-authorized auto_refresh=true may update its hash/path/history while
preserving course/type/period/human approval. New IDs cannot inherit approval by name.
Unauthorized changed versions stay pending and block old facts. Complete inventory
absence and known update failures also block old facts; unknown state under access
failure remains partial. Module-only inventory must not mark unseen approved files
as deleted. `file_library_check` exposes scope=all_course_documents, coverage, actions,
warnings and check time separately from ingestion/validation timestamps.

## Multi-source canonical deadlines

```text
live Canvas + persisted validated official document evidence
→ normalize → classify → validate → reconcile → deduplicate
→ filter → sort → limit → count → structured JSON → Codex presentation
```

`DeadlineReconciler` owns source agreement/conflict and canonical selection.
Live Canvas timing defaults to higher priority **only for the same logical item**.
Matching requires the same course, normalized title and classified type with a
unique Canvas logical anchor; linked Canvas views can share an anchor. Different
courses, numbered assessments and ambiguous repeated instances never merge by
guesswork. Unique matching uses identity without requiring equal dates, so moved
deadlines still reconcile before range filtering. Date-only evidence agrees with
a precise Canvas time on the same local date without inventing a document clock time.

- Same item/same date: one canonical deadline with multiple sources.
- Same item/different date: live Canvas value selected, document value/evidence retained
  in `conflicts`, warning emitted. A resolved conflict alone does not make count partial.
- Document-only confirmed item: canonical value comes from validated official evidence.
- Explicitly undated live Canvas item: do not resurrect its old document due date;
  withhold the document timing as an unresolved conflict.
- Multiple matching Canvas items/repeated instances: unresolved identity, no
  guessed merge; affected document items appear in `unresolved_deadlines`, not count.
- Official documents disagree without a live Canvas anchor: no arbitrary newest-document
  winner; unresolved conflict requires review and is not counted.

`DeadlineDeduplicator` separately prevents multiple canonical views of the same
logical deadline from inflating count. Reconciliation is not moved into the Skill.

## Functional contracts

- `DeadlineService.query(DeadlineQuery)` accepts an LLM-normalized structured TimeIntent or two
  explicit aware ISO timestamps, human-readable course reference or verified course IDs,
  type/status filters, optional limit and document_mode=auto/existing/refresh.
- `upcoming(days=7, ...)`, `list_courses(course_reference=None)`, `get_deadline(id)`.
- `ingest_document(document_id)` processes an already approved registry record;
  `list_documents()` exposes inventory/validation/ingestion diagnostics.
- Skill scripts: deadlines.py, upcoming.py, courses.py, deadline_details.py.
- CLI: courses, deadlines, upcoming, deadline, documents, ingest, prepare-documents,
  approve-document. deadlines/upcoming accept --document-mode.

Default timezone is Asia/Singapore. Codex understands time language and emits
TimeIntent; Python validates it and computes the window using the real clock.
Supported operations are calendar day/week/weekend/month, weekday subsets,
month's first N days, current-period remaining time, rolling N elapsed days,
explicit date ranges and time before a calendar boundary. These are bounded
operations, not a closed list of natural-language phrases. Week = Monday–Sunday.
Rolling N days = N*24 elapsed hours. All ranges are inclusive, aware and bounded
to 366 elapsed days. The result echoes intent and resolved start/end/timezone.
Semantic ambiguity requires clarification or an explicitly disclosed harmless
interpretation; engine validation cannot prove AI semantic correctness.

CLI deadlines accepts --time-intent JSON or --time-intent-file UTF-8 JSON, exclusive
with --start/--end. The engine has no natural-language phrase whitelist or regex
fallback; adapters must normalize time language before calling it. `upcoming --days`
constructs a rolling-days TimeIntent internally. No new backend LLM API is needed.
Schema and examples: [time-intent.md](../skills/canvas-ddl/references/time-intent.md).
Time interpretation never invents a factual exam date or alters document validation.
Event-relative queries require an engine-confirmed anchor first.

`Week 7 Friday`/`第7周` is stored unresolved at ingestion
(`CANVAS_TEACHING_WEEK_REQUIRED`). At query time, `TeachingWeekResolver` may bound
it using full-term Canvas course-calendar evidence from Calendar Events and, when
available, the course's official same-origin ICS feed. Explicit `Week N` calendar
labels are high-confidence anchors. An unambiguous first-class/Week 1 or weekly
series-head event plus explicit recess/reading-week or `blackout_date` records
provides a medium-confidence sequence; those
break weeks do not increment the teaching-week number. Conflicting anchors produce
no mapping. Neither an LLM nor Codex computes the date, and no external academic
calendar is required.

A mapped relative week becomes a `ReferenceDeadline`, not a canonical `Deadline`:
`Week 7 Friday` has day precision, while bare `Week 7` has a Monday–Sunday window.
It is filtered against the requested range/type and appears in the separate
high-recall reference list. It never enters canonical count, because the document
still does not state an exact factual timestamp. Approved official documents may
produce medium/high-confidence references; provisional Canvas course documents may
produce low-confidence references with source approval risk preserved.
Missing-year inference, guessed AM/PM, tentative/cancelled wording, unlock/lock
conversion or best-effort presentation cannot promote a candidate to a confirmed fact.
For this user's low-risk reference preference, adapters may quote clear official
month/day candidates as unconfirmed reference arrangements with location/source and
specific uncertainty. Term context stays separate; no inferred canonical timestamp,
query-window inclusion or exam count follows from such references.
Unsupported layout or time expressions remain unresolved rather than silently
guessed. Scanned/image-only pages use local PP-OCRv6 Small during ingestion. OCR
text is persisted with page, extraction mode, engine and line confidence. OCR is
perception input, not authority: the ordinary extractor and independent validator
still run, and a candidate below the configured confidence threshold stays
unresolved. OCR failures keep page/document coverage partial rather than dropping
the failure or aborting unrelated readable pages.

## Result and model requirements

Deadline retains its original fields plus multiple `SourceReference` values,
`validation`, `conflicts`, `reconciliation_status`, `canonical_reason` and
date-only semantics. Document deadlines have `canvas_resource_id=null`.

```text
result.count == len(result.deadlines)
result.reference_count == len(result.reference_deadlines)
```

`ReferenceDeadline` contains course/title/type, inferred window start/end,
day-or-teaching-week precision, resolution/confidence, document evidence and every
Canvas calendar anchor. It is visibly non-canonical and does not affect
matched_count, course_counts or canonical exam totals.

Count follows reconciliation, deduplication, filtering and limit. `matched_count`
and `course_counts` are computed before limit. Counts refer to logical items,
not unique courses. Unvalidated/unresolved candidates are never countable.

JSON includes status, complete, query, deadlines, count, warnings, coverage,
document_summary, unresolved_deadlines and evidence_scope. `ok` is complete only
within successfully fetched Canvas sources and the explicitly registered document
inventory; it does not prove that every course document has been registered.

Freshness: `live`, `live_partial`, `live_with_ingested_documents`, `mixed_partial`.
Document ingestion/validation times never become pretend live-document verification
times. Missing approved ingestion, hash/version mismatch, unresolved candidates,
empty pages, unavailable sources and unresolved reconciliation make results partial.
Partial zero does not prove absence. Document facts may be shown as ingested evidence
when Canvas collection fails, with partial status; no stale Canvas fallback exists.
401 remains an authentication error; course access must resolve via the service.

## Security and performance

CANVAS_TOKEN (or CANVAS_API_TOKEN compatibility alias) stays local. Never print,
log or include it/auth headers in model-facing data. Canvas performs GET only;
local ingestion writes only its evidence store. Official host approval never
exposes an arbitrary authenticated HTTP capability to Codex.

Keep sufficient-evidence queries lightweight; unchanged files must not be reparsed.
No query-time OCR/model calls or full-account sync. OCR runs only while ingesting
new/changed low-text PDF pages or image inputs and cached artifacts serve later queries. A document-dependent refresh may
take longer than the fast path. Course lookup <3s/common DDL queries <5s are targets.

## Acceptance and required tests

Preserve: 下周作业、下周考试及时间、课程下一个DDL、周末DDL、未来14天quiz、明天DDL。
Additionally verify:

1. Document-only explicit deadline passes validation and enters canonical query results.
2. Canvas/document same-date item has both evidence sources and is counted once.
3. Canvas/document conflict selects live Canvas and preserves the alternative/evidence.
4. Reconciliation before filtering prevents moved dates from leaving obsolete document hits.
5. Ambiguous, relative-week, missing-year, forged location/text/scope/date proposals
   cannot become confirmed facts, including proposals from an injected LLM extractor.
6. Approved document hash/version/course binding and re-ingestion are enforced.
7. Existing evidence queries continue without original documents or parsing. Unchanged
   refresh checks also reuse persisted evidence without parsing.
8. Source failures, unresolved conflicts, date-only precision, count invariants,
   secrets and Canvas read-only behavior are covered by offline tests.
9. Complete positive non-exam queries skip files; exam queries always check all scoped supported documents and Canvas course content. Empty/partial queries refresh before
   counting; forced/existing modes and explicit course scope are tested.
10. New-source review, same-file authorized versions, unchanged/hash-identical
    files, disappearance, update/permission failures and recovery preserve counts.
11. Scanned PDF deadlines pass through PP-OCRv6 Small and deterministic validation;
    low-confidence OCR remains unresolved, native-text pages bypass OCR, and OCR
    provenance survives normalization and serialization.
12. Canvas Week labels and unambiguous first-class/recess records map Week N into
    a bounded reference window; recess is skipped, Friday narrows to one day,
    conflicting/missing anchors do not resolve, tentative text stays excluded,
    Calendar Events and course ICS fallback are covered, and canonical count remains
    unchanged. Feed requests never carry the Canvas bearer token; foreign or
    unexpected feed URLs are rejected.
13. DOCX, PPTX, XLSX, CSV, TXT/Markdown, RTF, HTML and standalone image inputs pass
    through the same extractor/validator/persistence pipeline, preserve format-specific
    locations, and do not reopen the source during final queries. Canvas Syllabus and
    published Pages are discovered and scanned as provisional HTML; approval remains
    mandatory before confirmed facts.


## Grading-based midterm reference inference

For “does this course have a midterm”, the Skill may use an engine-returned complete,
non-overlapping grading breakdown for the same course/term/version. If components
total 100%, no midterm share is listed and there is no positive midterm evidence,
it may say “按目前课程评分构成推断，应没有单独计分的期中考试” as a reference inference,
with percentages, document/location and source/coverage risks. Incomplete/truncated,
overlapping, alternative or ambiguous breakdowns cannot support it. A positive
midterm record/reference makes the negative inference inapplicable; source conflicts
remain engine-owned. A midterm may be embedded in homework/continuous assessment or
ungraded, so this never establishes absence of all midterm assessments. Adding weights
is permitted only for this presentation inference; it cannot modify authority,
validation, canonical deadlines, completeness or exam counts, or directly read source documents.
