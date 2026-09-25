class DiscussionCollector:
    source_type = "canvas_discussion"

    def __init__(self, client):
        self.client = client

    def collect(self, *, course_id, start, end):
        rows = self.client.get_paginated(f"/api/v1/courses/{course_id}/discussion_topics")
        return [row for row in rows if not row.get("is_announcement")]

