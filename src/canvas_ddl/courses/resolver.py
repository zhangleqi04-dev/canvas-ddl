import re
from collections.abc import Mapping, Sequence
from canvas_ddl.canvas.errors import ApplicationError
from .models import Course


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


class CourseResolver:
    def __init__(self, aliases: Mapping[str, str] | None = None):
        self.aliases = {normalized(k): v for k, v in (aliases or {}).items()}

    def resolve(self, reference: str, courses: Sequence[Course]) -> Course:
        if not isinstance(reference, str) or not reference.strip():
            raise ApplicationError("INVALID_QUERY", "Course reference must not be empty.")
        value = reference.strip()
        groups = [
            [c for c in courses if c.course_id == value],
            [c for c in courses if c.course_code == value],
            [c for c in courses if c.course_name == value],
            [c for c in courses if normalized(value) in (normalized(c.course_code), normalized(c.course_name))],
            [c for c in courses if c.course_id == self.aliases.get(normalized(value))],
        ]
        for candidates in groups:
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise ApplicationError("AMBIGUOUS_COURSE", "Multiple matching courses were found.", tuple(candidates))
        raise ApplicationError("COURSE_NOT_FOUND", "No accessible course matches that reference.")
