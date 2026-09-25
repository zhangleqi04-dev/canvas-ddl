class QuizCollector:
    source_type = "canvas_quiz"

    def __init__(self, client):
        self.client = client

    def collect(self, *, course_id, start, end):
        return self.client.get_paginated(f"/api/v1/courses/{course_id}/quizzes")

