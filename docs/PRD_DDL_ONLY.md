# PRD — Multi-source Canvas DDL Assistant

**Status:** Implemented contract v0.12.3 · 2026-09-26
**Deployment:** single-user, local, read-only Canvas PAT  
**Interface:** Codex Skill / CLI; MCP optional and deferred

## Course-scoped automated preparation

Authenticated Canvas Files, Syllabus and published Pages are course-scoped evidence
sources. The engine verifies Canvas origin, course path, resource ID and content hash,
then registers them automatically with `source_authority=canvas_api`; human source
approval and a manually entered teaching period are not required. Explicit
`prepare-documents --course <reference>` may download up to 20 filename-hinted files,
while automatic exam refresh scans every accessible supported document without that
name/count limit. No-match, inventory fallback and access/parse failures remain visible
and cannot establish complete coverage.

External URLs and manually supplied local files remain a separate operator trust path.
Only that path uses pending-review plus `approve-document` with a real approver, verified
course/type and teaching period. Codex cannot manufacture approval. Canvas storage/CDN
hosts are transport destinations only; authentication stays on the Canvas origin and an
arbitrary download URL cannot claim `canvas_api` authority.

## Product goal

Answer upcoming academic deadline questions using an explicit, auditable evidence
base. Canvas structured data and **trusted course documents** jointly provide evidence. A verified document-only deadline is valid without a Canvas record.
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
- Pre-ingested course documents: authenticated course-scoped Canvas Files, Syllabus
  and published Pages are trusted automatically after exact API identity checks;
  external/manual official documents require operator trust and an approved teaching
  period. Supported artifacts are PDF, DOCX, PPTX, XLSX, CSV, TXT/Markdown, RTF, HTML
  and standalone PNG/JPEG/WebP/TIFF/BMP images.
- Date-only schedules, precise deadlines, submission status when Canvas supplies
  it, source conflicts and validation diagnostics.

Exclude announcements, email, chat, unofficial notes, arbitrary web documents,
general document content Q&A, legacy DOC/PPT/XLS, ZIP expansion, audio/video
transcription, RAG/vector databases, tutoring, Canvas writes, assignment
submission, full-account synchronization, notifications, OAuth and multi-user hosting.

## Authority and validation

Source authority has two explicit paths. `canvas_api` means the file/content identity was
returned by an authenticated Canvas request for the selected course and matched the exact
Canvas origin, course path and resource ID. This path needs no human source approval.
`operator` means a maintainer verified an external/manual official document, its course,
kind, SHA-256 and teaching period. Filename, document self-description, LLM confidence,
Codex reasoning and arbitrary URLs grant no authority. Extraction cannot modify these
trust decisions.

Ingestion is distinct from the final evidence query. A bounded format parser handles PDF,
DOCX, PPTX, XLSX, CSV, text, RTF, HTML or image input; local PP-OCRv6 Small is used only
for low-text PDF pages and eligible images.

```text
Canvas API scoped identity or operator trust
→ DocumentParser → StructuralChunker → broad temporal-anchor prefilter
→ bounded Codex semantic review (codex-semantic-v2)
→ SemanticDeadlineCandidate → DeadlineValidator
→ persisted review/validation evidence → confirmed normalized Deadline
```

Codex returns one event per logical schedule with exact source title, evidence and
`date_expression`, plus `normalized_date` (`YYYY-MM-DD` or null) and `normalized_time`
(`HH:MM` or null). It may resolve numeric order, ordinal dates and AM/PM from semantics.
Python independently verifies that the normalized result is a literal-compatible reading,
that a missing year has one unambiguous full-document context year, and that source
authority, course, hash, location, role, enum and OCR thresholds hold. This removes the
former “one regex date only” restriction without making LLM output a fact. Codex cannot
decide trust, priority, deduplication or count. Incomplete review withholds that document.

Every document deadline preserves document ID/name/hash, authority, original URL,
format-specific location, exact evidence/date expression, normalized value, ingestion time
and validation audit. Final queries use persisted evidence and do not reopen source files;
only the update stage parses new or changed artifacts.

## Fresh scoped course-document checks for every deadline query

DeadlineQuery.document_mode defaults to auto. Every deadline query checks all supported
documents in every selected course before returning a final result, even when live Canvas
or persisted evidence already supplies a complete positive answer. A positive assignment
cannot prove that no PDF-only exam exists in a broad DDL query, and an old positive
document deadline cannot prove that the current file still has the same date.
`existing` explicitly disables checking for a user-requested cached/offline operation;
`refresh` also checks before querying. All checks are course-scoped, never a background
full-account synchronization.

Automatic refresh lists course files without a remote MIME filter, selecting every
supported modern document by MIME or extension regardless of filename. There is no
default 20-file limit.
An explicit limit reports unprocessed coverage. On unavailable/forbidden Files,
Modules file items may supply accessible documents; this fallback is always partial and
never proves complete course inventory. Access, parse and empty-page failures remain visible. New authenticated Canvas
Files/Syllabus/Pages register automatically and enter the main evidence pipeline. External
or manual documents may be content-scanned into an isolated provisional store, but require
operator trust before any proposal can become a Deadline. Provisional `DocumentDraft`
period bounds are syntax-only placeholders, not authority.

The update stage uses DocumentParser → StructuralChunker → LightSemanticPrefilter → Codex
semantic review → DeadlineCandidate → DeadlineValidator before the final query. A legacy
rule extractor may produce migration diagnostics, but runtime facts are withheld until the
hash-bound Codex review is complete. Trusted records use the main store; provisional
external/manual scans use `data/documents.pending.sqlite3`.
Pending proposals and raw text excerpts never enter canonical counts. The engine also
searches all persisted page text for academic keywords and returns bounded context as
`document_content_matches`, including multi-line evidence that extraction may miss.
This is literal evidence search, not general RAG or independent Skill document reading.
Excerpts are unvalidated; statistical tests/examples are not scheduled examinations.
Adapters may quote relevant reference text with its source/page and explicit risk,
without asserting source officiality, inferring dates or computing exam counts.
Relevant unconfirmed evidence must be presented in a separate section whether the
canonical result contains zero or many confirmed items. This prevents a confirmed hit
from hiding other plausible course arrangements that remain under review.

Unchanged metadata/hash with valid artifacts reuses stored content without parsing.
Changed/missing metadata triggers a hash comparison. An authenticated Canvas resource
updates its hash/path/history automatically; a new Canvas ID is accepted only after a fresh
course-scoped API identity check. External/manual source IDs and versions cannot inherit
operator trust by filename. Failed current versions block old facts. Complete inventory
absence and known update failures also block old facts; unknown state under access
failure remains partial. Module-only inventory must not mark unseen trusted files
as deleted. `file_library_check` exposes scope=all_course_documents, coverage, actions,
warnings and check time separately from ingestion/validation timestamps.

## Multi-source canonical deadlines

```text
live Canvas + persisted validated trusted document evidence
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
- `ingest_document(document_id)` parses a trusted registry record and stages semantic review;
  `semantic_review_requests(document_id)` returns bounded untrusted chunks and
  `apply_semantic_review(document_id, payload)` validates/persists Codex review batches;
  `list_documents()` exposes inventory/validation/ingestion diagnostics.
- Skill scripts: deadlines.py, upcoming.py, courses.py, deadline_details.py,
  semantic_review_requests.py and semantic_ingest.py.
- CLI additionally exposes semantic-review-requests and semantic-ingest; courses,
  deadlines, upcoming, deadline, documents, ingest, prepare-documents and
  approve-document remain. deadlines/upcoming accept --document-mode.

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
still does not state an exact factual timestamp. Trusted Canvas/API or operator documents may produce medium/high-confidence references;
provisional external/manual documents may produce low-confidence references with trust risk preserved.
An ungrounded missing-year or AM/PM mapping, tentative/cancelled wording, unlock/lock
conversion or best-effort presentation cannot promote a candidate to a confirmed fact.
For this user's low-risk reference preference, adapters may quote clear official
month/day candidates as unconfirmed reference arrangements with location/source and
specific uncertainty. Term context stays separate; no inferred canonical timestamp,
query-window inclusion or exam count follows from such references.
Unsupported layout or time expressions remain unresolved rather than silently
guessed. Scanned/image-only pages use local PP-OCRv6 Small during ingestion. OCR
text is persisted with page, extraction mode, engine and line confidence. OCR is
perception input, not authority: Codex semantic review and the independent validator
still run, and a candidate below the configured confidence threshold stays
unresolved. OCR failures keep page/document coverage partial rather than dropping
the failure or aborting unrelated readable pages.

## Result and model requirements

Deadline retains its original fields plus multiple `SourceReference` values,
`validation`, `conflicts`, `reconciliation_status`, `canonical_reason` and
date-only semantics. Document SourceReference exposes `source_authority` so callers
can distinguish authenticated Canvas identity from operator trust. Document deadlines
have `canvas_resource_id=null`.

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

The Codex presentation has two result channels:

1. **已确认** uses only `deadlines` and the engine-provided canonical counts.
2. **参考安排（未确认）** uses relevant `reference_deadlines`,
   `unresolved_deadlines`, candidate issues and cached document content matches.

The second channel is mandatory whenever relevant possible scheduling evidence exists,
even if the first channel is non-empty. Each reference keeps its exact evidence,
course, source/document, location and explicit blocking reason. A reference without an
engine-resolved window must say that its inclusion in the requested time period is
unknown. Keyword frequency is never a reference total. Practice exams, examples,
statistical tests, teaching content, grading weight alone and cancelled/negated items
do not qualify as possible scheduled deadlines.

JSON includes status, complete, query, deadlines, count, warnings, coverage,
document_summary, unresolved_deadlines and evidence_scope. `ok` is complete only
within successfully fetched Canvas sources and the explicitly registered document
inventory; it does not prove that every course document has been registered.

Freshness: `live`, `live_partial`, `live_with_ingested_documents`, `mixed_partial`.
Document ingestion/validation times never become pretend live-document verification
times. Missing trusted ingestion, hash/version mismatch, unresolved candidates,
empty pages, unavailable sources and unresolved reconciliation make results partial.
Partial zero does not prove absence. Document facts may be shown as ingested evidence
when Canvas collection fails, with partial status; no stale Canvas fallback exists.
401 remains an authentication error; course access must resolve via the service.

## Security and performance

CANVAS_TOKEN (or CANVAS_API_TOKEN compatibility alias) stays local. Never print,
log or include it/auth headers in model-facing data. Canvas performs GET only;
local ingestion writes only its evidence store. Allowlisting a document download host never
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
   cannot become confirmed facts. Fabricated Codex spans/dates and incomplete review are rejected/withheld.
6. Canvas API origin/course/resource binding and operator document hash/period binding are enforced.
7. Existing evidence queries continue without original documents or parsing. Unchanged
   refresh checks also reuse persisted evidence without parsing.
8. Source failures, unresolved conflicts, date-only precision, count invariants,
   secrets and Canvas read-only behavior are covered by offline tests.
9. Auto checks all scoped supported documents and Canvas course content for broad,
    exam and non-exam queries, including complete positive results. A broad positive
    Canvas assignment must not hide a new PDF-only exam, and a changed document date
    must replace the old positive date before filtering/counting. Unchanged artifacts,
    forced/existing modes and explicit course scope are tested.
10. Canvas automatic registration/versioning, external-source review, unchanged/hash-identical
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
    through the same chunk/prefilter/Codex-review/validator pipeline, preserve format-specific
    locations, and do not reopen the source during final queries. Canvas Syllabus and published Pages are discovered, automatically trusted through
    their authenticated course scope, and ingested as HTML without human source approval.
14. A deadline without a legacy keyword is found through Codex semantic review;
    statistical `test` text yields an audited negative review; one chunk can bind
    multiple dates to distinct events without Python's former exactly-one-date rule.
15. Final queries rebuild the exact candidate set from persisted strict reviews;
    missing, extra or altered review/candidate rows withhold that document's facts.
16. `02/10/26`, ordinal month dates and AM/PM normalize through Codex v2 and are
    independently grounded by Python; impossible/fabricated mappings remain non-canonical.
17. Presentation scenarios cover zero and positive canonical counts with relevant
    unconfirmed evidence. Both must display the separate reference section, preserve
    source/location/reason, leave canonical counts unchanged, avoid assigning unresolved
    evidence to a time window, and suppress practice/example/statistical/negated matches.


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
