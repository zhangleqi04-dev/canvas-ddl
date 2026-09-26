"""Produces proposals only. The validator independently checks every claim."""
import hashlib
import re
from .models import DeadlineCandidate

MONTHS = "January February March April May June July August September October November December".split()
MONTH_PATTERN = "(?:" + "|".join(m + "|" + m[:3] for m in MONTHS) + ")"
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\d{4}年\d{1,2}月\d{1,2}日|\b\d{1,2}\s+" + MONTH_PATTERN + r"\s+\d{4}\b|\b" + MONTH_PATTERN + r"\s+\d{1,2},?\s+\d{4}\b", re.I)
ITEM_PATTERN = re.compile(r"\b(?:assignment|homework|exam|examination|midterm|mid-term|quiz|test|discussion|lecture|class|meeting|handout)\b|作业|考试|期中|小测|测验|讨论|课程安排", re.I)
RELATIVE_PATTERN = re.compile(r"\bweek\s*\d+\b|第\s*\d+\s*周|TBA|TBC|待定|暂定", re.I)
AMBIGUOUS_DATE_PATTERN = re.compile(MONTH_PATTERN + r"|\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|due|deadline)\b|\d{1,2}/\d{1,2}|截止", re.I)


def evidence_title(text: str) -> str:
    date_match = DATE_PATTERN.search(text) or RELATIVE_PATTERN.search(text)
    prefix = text[:date_match.start()] if date_match else text
    prefix = re.sub(r"\s*(?:due(?:\s+on)?|deadline|scheduled(?:\s+on)?|on|截止(?:日期)?|提交截止)\s*[:：]?\s*$", "", prefix, flags=re.I)
    return prefix.strip(" \t:：-–—|")


class DeadlineExtractor:
    """Legacy rule prefilter retained for migration diagnostics, never runtime authority."""
    def extract(self, parsed, document) -> tuple[DeadlineCandidate, ...]:
        result = []
        for page in parsed.pages:
            for raw_line in page.text.splitlines():
                line = raw_line.strip()
                if not ITEM_PATTERN.search(line):
                    continue
                matches = list(DATE_PATTERN.finditer(line))
                if not matches and not RELATIVE_PATTERN.search(line) and not AMBIGUOUS_DATE_PATTERN.search(line):
                    continue
                expression = matches[0][0] if len(matches) == 1 else None
                key = hashlib.sha256(f"{document.document_id}:{parsed.sha256}:{page.page}:{line}".encode()).hexdigest()[:24]
                result.append(DeadlineCandidate(key, document.document_id, document.course_id, evidence_title(line),
                                                page.page, line, expression, location=page.location))
        return tuple(dict.fromkeys(result))
