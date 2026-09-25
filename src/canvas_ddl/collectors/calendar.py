from datetime import timedelta
from canvas_ddl.canvas.errors import ApplicationError


def belongs_to_course(row: dict, course_id: str, section_ids=()) -> bool:
    target = f"course_{course_id}"
    codes = {row.get("context_code"), row.get("effective_context_code")}
    codes.update(str(row.get("all_context_codes") or "").split(","))
    return target in codes or bool(codes & {f"course_section_{value}" for value in section_ids})


class CalendarCollector:
    source_type = "canvas_calendar_event"

    def __init__(self, client, kind="event"):
        self.client = client
        self.kind = kind
        if kind == "assignment":
            self.source_type = "canvas_calendar_assignment"

    def collect(self, *, course_id, start, end):
        # Slightly widen date-only API filtering; the domain applies the exact range.
        rows = self.client.get_paginated("/api/v1/calendar_events", params={
            "context_codes[]": f"course_{course_id}", "type": self.kind,
            "start_date": (start.date() - timedelta(days=1)).isoformat(),
            "end_date": (end.date() + timedelta(days=1)).isoformat(),
        })
        return self._visible(rows, course_id)

    def collect_for_reconciliation(self, *, course_id, start, end):
        # Course-scoped lookup across dates prevents a moved Canvas event from
        # disappearing while its old PDF date remains inside the user's window.
        # Calendar UI course meetings may be section-scoped, so include only
        # sections verified through this course's read-only sections endpoint.
        section_ids = self._section_ids(course_id)
        contexts = [f"course_{course_id}"] + [f"course_section_{value}" for value in section_ids]
        rows = self.client.get_paginated("/api/v1/calendar_events", params={
            "context_codes[]": contexts if len(contexts) > 1 else contexts[0],
            "type": self.kind, "all_events": "true"})
        return self._visible(rows, course_id, section_ids)

    def _section_ids(self, course_id):
        try:
            rows = self.client.get_paginated(f"/api/v1/courses/{course_id}/sections", params={})
        except AttributeError:
            return ()
        except ApplicationError as error:
            if error.code == "CANVAS_AUTH_FAILED":
                raise
            return ()
        return tuple(str(row["id"]) for row in rows if row.get("id") is not None)

    @staticmethod
    def _visible(rows, course_id, section_ids=()):
        return [row for row in rows if belongs_to_course(row, str(course_id), section_ids)
                and row.get("workflow_state") != "deleted" and not row.get("hidden")
                and not (row.get("appointment_group_id") and row.get("own_reservation") is False)]
