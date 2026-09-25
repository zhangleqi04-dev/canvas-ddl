from canvas_ddl.canvas.errors import ApplicationError
from .models import Course


class CourseRepository:
    def __init__(self, client, allowed_ids: tuple[str, ...] = ()):
        self.client = client
        self.allowed_ids = allowed_ids

    def list_courses(self) -> list[Course]:
        rows = self.client.get_paginated("/api/v1/courses", params={"enrollment_state": "active"})
        result = [Course(str(row["id"]), str(row.get("course_code") or ""), str(row.get("name") or ""))
                  for row in rows if row.get("id") and not row.get("access_restricted_by_date")
                  and row.get("workflow_state") not in ("deleted", "completed")
                  and (not self.allowed_ids or str(row["id"]) in self.allowed_ids)]
        if self.allowed_ids and set(self.allowed_ids) - {c.course_id for c in result}:
            raise ApplicationError("PERMISSION_DENIED", "Some configured courses are not accessible in the active Canvas course list.")
        return result

