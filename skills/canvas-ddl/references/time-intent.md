# Semantic time contract (v0.7)

Codex interprets the user's language; Python computes actual dates. This schema
limits executable operations, not natural-language vocabulary. Use
`deadlines.py --time-intent-file <absolute UTF-8 JSON path>` (recommended on Windows)
or `--time-intent <JSON>` through separated, safely quoted process arguments.
Do not combine with --start/--end. The engine rejects raw natural-language time;
the four existing scripts remain thin and do not contain a phrase whitelist or regex parser.

## Common fields

- kind: one operation below.
- original_text: required nonempty original time phrase, at most 2000 characters.
- timezone: optional IANA name; otherwise engine configuration (Asia/Singapore).
- interpretation: optional nonempty explanation of an adopted meaning, at most
  1000 characters. It documents semantic choices; it grants no factual authority.

Unknown/inapplicable fields, nulls, duplicate JSON keys, fractional/boolean integers
and malformed inputs fail with INVALID_TIME_INTENT. Offset is a required integer
from -366 to 366 for calendar/before operations, measured in that operation's unit.
Do not supply unused fields. Calendar windows include the entire final local day;
all computed windows must be ordered and at most 366 elapsed days.

| kind | Required parameters | Optional parameters | Meaning |
|---|---|---|---|
| calendar_day | offset | portion | Offset local day, midnight to inclusive day end |
| calendar_week | offset | weekday_start and weekday_end, portion | Offset Monday–Sunday calendar week; both weekday bounds or neither |
| calendar_weekend | offset | — | Saturday–Sunday of the offset calendar week |
| calendar_month | offset | days, portion | Offset calendar month; days selects its first N days, clipped at month end |
| rolling_days | days | — | Now through exactly N*24 elapsed hours later |
| date_range | start_date, end_date | — | Explicit literal YYYY-MM-DD dates, inclusive; use only dates established by user/verified engine anchor |
| before | boundary, offset | weekday | Now through just before a local midnight boundary; excludes that boundary |

Weekdays are integers 0=Monday through 6=Sunday, with start <= end. days is 1–366
for rolling_days, 1–31 for calendar_month. portion is full (default) or remaining;
remaining is allowed only at offset=0 for day/week/month, begins at max(now, span
start), and cannot accompany month days. A remaining span already in the past
fails rather than silently choosing another week. before boundary is day/week/month;
weekday is permitted only with week and defaults to Monday. A past boundary fails.

## Examples

下下周（两周后的完整自然周）：

```json
{"kind":"calendar_week","offset":2,"original_text":"下下周"}
```

接下来两周（从现在起 14×24 小时）：

```json
{"kind":"rolling_days","days":14,"original_text":"接下来两周"}
```

下周一到周三（包含周三全天）：

```json
{"kind":"calendar_week","offset":1,"weekday_start":0,"weekday_end":2,"original_text":"下周一到周三"}
```

下个月前 10 天：

```json
{"kind":"calendar_month","offset":1,"days":10,"original_text":"下个月前10天"}
```

月底之前（本月剩余时间，包含本月最后一天）：

```json
{"kind":"calendar_month","offset":0,"portion":"remaining","original_text":"月底之前","interpretation":"从现在到本月最后一天结束"}
```

下周一之前（不包含下周一）：

```json
{"kind":"before","boundary":"week","offset":1,"original_text":"下周一之前"}
```

本周五（即使今天已过周五，也保持本周的含义）：calendar_week offset=0,
weekday_start=4, weekday_end=4. 下个周末：calendar_weekend offset=1.
今天/明天：calendar_day offset=0/1. 这个月：calendar_month offset=0.

## Ambiguity and fact boundary

Do not ask the model to write relative start/end timestamps. Only the engine reads
now and handles timezone, leap days, month/year boundaries and DST. Always use the
returned query.start/end/timezone to communicate the adopted interval; time_intent
is echoed and range_expression retains original_text. generated_at is response
time, not a substitute for the resolved query range. One range is reused even if
file refresh crosses midnight.

“最近”, “月初”, “头两个星期”, a date without year, or “周五之前” with unclear week
may change the answer materially: clarify before querying. If adopting an obvious
low-impact meaning, put it in interpretation and state it in the answer. Engine
schema validation cannot establish that Codex correctly understood the user's words.
For “考试前”, obtain an engine-confirmed factual anchor first; explicit aware
--start/--end can express exact timestamps supplied by that anchor/user. Do not
anchor on unconfirmed candidates. Unsupported time semantics require clarification;
do not simulate them with invented fields or approximate factual dates.

Document Week 7 Friday / 第7周 remains unresolved at ingestion. A final query may return
it as a separate non-canonical ReferenceDeadline only when TeachingWeekResolver
can bound it from the course's Canvas calendar. TimeIntent/Codex never computes it.
Missing document years, exam dates and evidence conflicts cannot be inferred here.
This interface changes query interpretation only, never validation or canonical
counts. `upcoming --days` constructs a rolling-days TimeIntent internally. Raw
`--range` phrases are unsupported: all natural-language semantics come from the
invoking LLM/Skill. No backend LLM API, generic NLP parser or vector store is required.

Date-only sources retain their evidence-zone calendar day. A timezone override
may match an overlapping source day; do not claim an exact hour or definite
clock-time inclusion when the source supplies no time.
