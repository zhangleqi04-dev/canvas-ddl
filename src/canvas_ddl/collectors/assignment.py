class AssignmentCollector:
    source_type = "canvas_assignment"

    def __init__(self, client):
        self.client = client

    def collect(self, *, course_id, start, end):
        # Canvas has no arbitrary due-date window here; fetch this course's list.
        return self.client.get_paginated(f"/api/v1/courses/{course_id}/assignments", params={
            "include[]": "submission", "override_assignment_dates": "true"})

