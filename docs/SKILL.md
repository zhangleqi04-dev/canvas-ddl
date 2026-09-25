# Skill specification

The maintained runtime contract is
[../skills/canvas-ddl/SKILL.md](../skills/canvas-ddl/SKILL.md). Read that file for
triggers, supported scripts, result interpretation, evidence presentation and
error/completeness rules. This pointer replaces the original structured-only
draft so there is one authoritative runtime instruction file.

The Skill presents live Canvas and pre-ingested validated official course-document
facts through DeadlineService. It never opens source documents, extracts deadlines, approves
officiality, computes counts, guesses academic weeks or selects source priority.
