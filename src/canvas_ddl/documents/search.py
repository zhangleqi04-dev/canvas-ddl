"""Literal cached-page search; excerpts are evidence, never deadline facts."""
import re
from canvas_ddl.deadlines.classifier import DEFAULT_EXAM_KEYWORDS


class DocumentContentSearcher:
    def search(self, parsed, *, types=None, extra_exam_keywords=()):
        terms = []
        if types is None or "exam" in types:
            terms.extend(DEFAULT_EXAM_KEYWORDS + tuple(extra_exam_keywords))
        for kind, words in (("assignment", ("assignment", "homework", "due", "deadline", "作业", "截止")),
                            ("quiz", ("quiz", "小测", "测验")), ("discussion", ("discussion", "讨论"))):
            if types is None or kind in types:
                terms.extend(words)
        if not terms:
            return {"matches": [], "match_count": 0, "truncated": False}
        patterns = [r"(?<!\w)" + re.escape(t) + r"(?!\w)" if t.isascii() else re.escape(t) for t in terms if t]
        pattern = re.compile("|".join(patterns), re.I)
        matches = []
        for page in parsed.pages:
            lines = page.text.splitlines()
            covered_until = -1
            for i, line in enumerate(lines):
                if i <= covered_until or not pattern.search(line):
                    continue
                low, high = max(0, i - 2), min(len(lines), i + 4)
                excerpt = "\n".join(lines[low:high])
                matches.append({"page": page.page, "location": page.location, "extraction_mode": page.extraction_mode,
                                "evidence_text": excerpt[:1500], "excerpt_truncated": len(excerpt) > 1500})
                covered_until = high - 1
        return {"matches": matches[:10], "match_count": len(matches), "truncated": len(matches) > 10}
