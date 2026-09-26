# Skill specification

The maintained runtime contract is
[../skills/canvas-ddl/SKILL.md](../skills/canvas-ddl/SKILL.md). Read that file for
triggers, supported scripts, result interpretation, evidence presentation and
error/completeness rules. This pointer replaces the original structured-only
draft so there is one authoritative runtime instruction file.

The Skill presents live Canvas and pre-ingested validated official course-document
facts through DeadlineService. It never opens raw source files directly. For an approved
document that still needs semantic review, it may classify only the bounded, hash-bound
text chunks returned by DeadlineService and submit strict semantic candidate JSON back to
the engine. It never approves officiality, validates a candidate, computes counts, guesses
academic weeks or selects source priority.
