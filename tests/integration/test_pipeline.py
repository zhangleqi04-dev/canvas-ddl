"""Real client/collectors/service/CLI exercised against deterministic HTTP fixtures."""
from datetime import datetime
from io import StringIO
from types import SimpleNamespace
import json
import httpx
import pytest
from canvas_ddl.canvas.client import CanvasClient
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.cli.main import main


def intent_args(intent):
    return ["--time-intent", json.dumps(intent.to_dict(), ensure_ascii=False)]


def make_service():
    def handler(request):
        path = request.url.path
        assert request.method == "GET"
        if path == "/api/v1/courses":
            return httpx.Response(200, json=[{"id": 12345, "course_code": "CS3244", "name": "Machine Learning"}])
        if path.endswith("/assignments"):
            return httpx.Response(200, json=[
                {"id": 1, "name": "Assignment 2", "due_at": "2026-09-23T15:59:00Z"},
                {"id": 2, "name": "Midterm Exam", "due_at": "2026-09-22T06:00:00Z"},
                {"id": 3, "name": "Quiz 3", "due_at": "2026-09-25T02:00:00Z", "quiz_id": 9},
                {"id": 4, "name": "Discussion 1", "due_at": "2026-09-19T12:00:00Z", "submission_types": ["discussion_topic"]},
                {"id": 5, "name": "Tomorrow's work", "due_at": "2026-09-18T10:00:00Z"},
            ])
        if path.endswith("/quizzes"):
            return httpx.Response(200, json=[{"id": 9, "assignment_id": 3, "title": "Quiz 3", "due_at": "2026-09-20T00:00:00Z"}])
        if path.endswith("/discussion_topics"):
            return httpx.Response(200, json=[{"id": 6, "assignment_id": 4, "title": "Discussion 1", "assignment": {"id": 4}}])
        if path == "/api/v1/calendar_events":
            rows = [{"id": 10, "title": "Midterm Exam", "start_at": "2026-09-22T06:00:00Z", "context_code": "course_12345"}] if request.url.params["type"] == "event" else [
                {"id": "assignment_2", "title": "Midterm Exam", "start_at": "2026-09-22T06:00:00Z", "context_code": "course_12345", "assignment": None}]
            return httpx.Response(200, json=rows)
        return httpx.Response(404)
    client = CanvasClient("https://canvas.example", "fixture-token", transport=httpx.MockTransport(handler))
    return DeadlineService(client, base_url="https://canvas.example", clock=lambda: datetime.fromisoformat("2026-09-17T13:05:00+08:00"))


@pytest.mark.parametrize("args,title", [
    (["deadlines", *intent_args(TimeIntent("calendar_week", "下周", offset=1)), "--type", "assignment"], "Assignment 2"),
    (["deadlines", *intent_args(TimeIntent("calendar_week", "下周", offset=1)), "--type", "exam"], "Midterm Exam"),
    (["deadlines", *intent_args(TimeIntent("rolling_days", "future", days=180)), "--course", "CS3244", "--limit", "1"], "Tomorrow's work"),
    (["deadlines", *intent_args(TimeIntent("calendar_weekend", "这个周末", offset=0))], "Discussion 1"),
    (["upcoming", "--days", "14", "--type", "quiz"], "Quiz 3"),
    (["deadlines", *intent_args(TimeIntent("calendar_day", "明天", offset=1))], "Tomorrow's work"),
])
def test_primary_acceptance_through_json_cli(args, title):
    output = StringIO()
    assert main(args, factory=lambda _: (make_service(), SimpleNamespace(token="fixture-token")), stdout=output) == 0
    result = json.loads(output.getvalue())
    assert result["status"] == "ok" and result["count"] == 1 == len(result["deadlines"])
    assert result["deadlines"][0]["title"] == title
    if title == "Midterm Exam":
        assert len(result["deadlines"][0]["sources"]) == 3
