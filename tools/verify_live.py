"""Opt-in read-only live check; prints summaries, not credentials or raw bodies."""
import json
from time import perf_counter
from canvas_ddl.bootstrap import build_deadline_service
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.serialization import encode, redact


def main():
    service, config = build_deadline_service(".env")
    reports = []
    try:
        begin = perf_counter()
        courses = service.list_courses()
        reports.append({"check": "courses", "count": len(courses), "seconds": round(perf_counter() - begin, 2)})
        selected = tuple(c.course_id for c in courses)
        for label, query in [
            ("all_courses_next_week_assignments", DeadlineQuery(
                time_intent=TimeIntent(
                    kind="calendar_week", original_text="next week", offset=1,
                    timezone=config.timezone,
                ),
                types=("assignment",), course_ids=selected,
            )),
            ("all_courses_next_14_days", DeadlineQuery(
                time_intent=TimeIntent(
                    kind="rolling_days", original_text="next 14 days", days=14,
                    timezone=config.timezone,
                ),
                course_ids=selected,
            )),
        ]:
            begin = perf_counter()
            result = service.query(query)
            data = encode(result)
            assert data["count"] == len(data["deadlines"])
            reports.append({"check": label, "status": data["status"], "complete": data["complete"],
                            "count": data["count"], "seconds": round(perf_counter() - begin, 2),
                            "timezone": data["query"]["timezone"], "start": data["query"]["start"], "end": data["query"]["end"],
                            "warnings": data["warnings"],
                            "confirmed_items": [{"course_code": d["course_code"], "title": d["title"], "type": d["type"],
                                                 "due_at": d["due_at"], "start_at": d["start_at"]} for d in data["deadlines"]]})
            if label == "all_courses_next_14_days" and result.deadlines:
                begin = perf_counter()
                detail = service.get_deadline(result.deadlines[0].deadline_id)
                reports.append({"check": "live_details", "course_code": detail.course_code, "title": detail.title,
                                "submission_status": detail.submission_status, "seconds": round(perf_counter() - begin, 2)})
    finally:
        service.client.close()
    print(json.dumps(redact(reports, config.token), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
