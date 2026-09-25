"""Centralized configurable classification; no LLM or prose parsing."""
import re
from dataclasses import replace
from .models import Deadline, TYPES

DEFAULT_EXAM_KEYWORDS = ("exam", "examination", "midterm", "mid-term", "test", "期中", "期末考试", "考试")


class DeadlineClassifier:
    def __init__(self, extra_exam_keywords: tuple[str, ...] = ()):
        self.keywords = DEFAULT_EXAM_KEYWORDS + extra_exam_keywords

    def classify(self, deadline: Deadline) -> Deadline:
        if deadline.explicit_type in TYPES:
            return replace(deadline, type=deadline.explicit_type)
        return replace(deadline, type=self.classify_title(deadline.title, deadline.type,
                                                         deadline.submission_types))

    def classify_title(self, value, fallback="event", submission_types=()):
        title = value.casefold().strip()
        exam = bool(re.fullmatch(r"final(?:\s+\d+)?", title))
        for keyword in self.keywords:
            word = keyword.casefold().strip()
            if not word:
                continue
            if word == "final" and re.search(r"\bfinal\s+(?:project|report|presentation|assignment)\b", title):
                continue
            # Word boundaries for Latin phrases prevent "test" matching "latest".
            pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)" if word.isascii() else re.escape(word)
            exam = exam or bool(re.search(pattern, title))
        if exam:
            return "exam"
        if "online_quiz" in submission_types:
            return "quiz"
        if "discussion_topic" in submission_types:
            return "discussion"
        return fallback
