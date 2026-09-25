"""Public schemas, with final credential redaction at the adapter boundary."""
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from .canvas.errors import ApplicationError
from .deadlines.models import Deadline, DeadlineQueryResult


def encode(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Deadline):
        internal = {"identities", "explicit_type", "submission_types", "authoritative_dates"}
        return {k: encode(v) for k, v in asdict(value).items() if k not in internal}
    if isinstance(value, DeadlineQueryResult):
        q = value.query
        return {
            "status": "ok" if value.complete else "partial", "complete": value.complete,
            "query": {"range_expression": q.time_range.expression, "start": encode(q.time_range.start),
                      "time_intent": q.time_intent.to_dict() if q.time_intent else None,
                      "end": encode(q.time_range.end), "timezone": q.time_range.timezone, "course": q.course,
                      "course_ids": encode(q.course_ids), "types": encode(q.types),
                      "submission_statuses": encode(q.submission_statuses), "limit": q.limit},
            "count": value.count, "matched_count": value.matched_count,
            "count_scope": "verified_records" if not value.complete else "returned_records",
            "deadlines": [encode(d) for d in value.deadlines], "generated_at": encode(value.generated_at),
            "data_freshness": value.data_freshness, "warnings": encode(value.warnings),
            "coverage": encode(value.coverage),
            "course_counts": [{"course_id": cid, "course_code": code, "count": count} for cid, code, count in value.course_counts],
            "document_summary": encode(value.document_summary), "unresolved_deadlines": [encode(d) for d in value.unresolved_deadlines],
            "file_library_check": encode(value.file_library_check),
            "document_content_matches": encode(value.document_content_matches),
            "reference_count": value.reference_count,
            "reference_deadlines": encode(value.reference_deadlines),
            "evidence_scope": "live_canvas_registered_documents_and_labeled_provisional_references",
        }
    if is_dataclass(value):
        return encode(asdict(value))
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return value


def error_result(error: ApplicationError):
    result = {"status": "needs_input" if error.code == "AMBIGUOUS_COURSE" else "error",
              "error": {"code": error.code, "message": error.message}}
    if error.candidates:
        result["candidates"] = encode(error.candidates)
    return result


def redact(value, secret: str):
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {redact(k, secret): redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    return value
