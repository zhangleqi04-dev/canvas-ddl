# Skill specification

The maintained runtime contract is
[../skills/canvas-ddl/SKILL.md](../skills/canvas-ddl/SKILL.md). Read that file for
triggers, supported scripts, result interpretation, evidence presentation and
error/completeness rules. This pointer replaces the original structured-only
draft so there is one authoritative runtime instruction file.

The Skill presents live Canvas and pre-ingested validated trusted course-document
facts through DeadlineService. It never opens raw source files directly. Authenticated
Canvas Files/Syllabus/Pages register automatically; operator approval applies only to
external/manual sources. For a trusted
document that still needs semantic review, it may classify only the bounded, hash-bound
text chunks returned by DeadlineService and submit strict semantic candidate JSON back to
the engine, preserving the exact date expression and adding ISO normalized date/time fields.
It never decides source trust, validates a candidate, computes counts, guesses
academic weeks or selects source priority.

Default `document_mode=auto` verifies the selected courses' supported document
inventory before every deadline query, including broad DDL and positive non-exam
queries. Unchanged files reuse persisted artifacts. Only an explicit user request for
cached/existing or offline evidence uses `document_mode=existing` to skip this check.

Presentation is intentionally two-channel. Confirmed items come only from canonical
deadlines. Whenever the engine also returns relevant possible scheduling evidence, the
Skill shows it separately as **参考安排（未确认）**, with provenance and the exact reason
it remains uncertain, even when confirmed items already exist. These references never
change the confirmed count or acquire an inferred date/window.
