"""Conservative merging with preserved provenance and personal date priority."""
import re
from datetime import timezone
from dataclasses import replace
from .models import Deadline


def title_key(title: str) -> str:
    return re.sub(r"\s+", " ", title.casefold()).strip()


PRIORITY = {"canvas_assignment": 0, "canvas_quiz": 1, "canvas_discussion": 2,
            "canvas_calendar_assignment": 3, "canvas_calendar_event": 4, "official_document": 5}


class DeadlineDeduplicator:
    @staticmethod
    def same(a: Deadline, b: Deadline) -> bool:
        if a.course_id != b.course_id:
            return False
        if set(a.identities) & set(b.identities):
            return True
        # Two independently identified assignments/quizzes/events remain separate.
        if a.source_type == b.source_type or a.date_only or b.date_only:
            return False
        if a.type != b.type:
            return False
        return title_key(a.title) == title_key(b.title) and a.actionable_at.astimezone(timezone.utc) == b.actionable_at.astimezone(timezone.utc)

    @staticmethod
    def merge(a: Deadline, b: Deadline) -> Deadline:
        preferred, other = sorted((a, b), key=lambda d: (not d.authoritative_dates, PRIORITY.get(d.source_type, 9)))
        sources = tuple(dict.fromkeys(preferred.sources + other.sources))
        identities = tuple(dict.fromkeys(preferred.identities + other.identities))
        # Do not replace the authoritative due date with a generic calendar time.
        status = preferred.submission_status
        if status == "unknown" and other.submission_status != "unknown":
            status = other.submission_status
        return replace(preferred, sources=sources, identities=identities, submission_status=status,
                       conflicts=tuple(dict.fromkeys(preferred.conflicts + other.conflicts)))

    def deduplicate(self, records: list[Deadline]) -> list[Deadline]:
        result = []
        for record in sorted(records, key=lambda d: (PRIORITY.get(d.source_type, 9), d.deadline_id)):
            matches = [i for i, existing in enumerate(result) if self.same(existing, record)]
            if len(matches) == 1:
                result[matches[0]] = self.merge(result[matches[0]], record)
            else:
                # Multiple candidates are uncertain. Never bridge two logical items
                # through an unrelated exact-title calendar event.
                result.append(record)
        return result
