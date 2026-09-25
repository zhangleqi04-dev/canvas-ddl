import httpx
import pytest
from io import BytesIO
from canvas_ddl.canvas.client import CanvasClient
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.collectors.assignment import AssignmentCollector
from canvas_ddl.collectors.discussion import DiscussionCollector
from canvas_ddl.collectors.calendar import CalendarCollector
from canvas_ddl.collectors.calendar_feed import CourseCalendarFeedCollector
from canvas_ddl.config import load_config
from canvas_ddl.courses.repository import CourseRepository
from datetime import datetime

TOKEN = "fixture-only-secret"
BASE = "https://canvas.example"


def client(handler):
    return CanvasClient(BASE, TOKEN, transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_pagination_all_pages_personal_overrides_and_get_only():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET" and request.headers["Authorization"] == "Bearer " + TOKEN
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        assert request.url.params["include[]"] == "submission"
        assert request.url.params["override_assignment_dates"] == "true"
        return httpx.Response(200, json=[{"id": 1}], headers={"Link": f'<{BASE}/api/v1/courses/1/assignments?page=2>; rel="next"'})
    c = client(handler)
    assert AssignmentCollector(c).collect(course_id="1", start=None, end=None) == [{"id": 1}, {"id": 2}]
    assert len(requests) == 2


@pytest.mark.parametrize("url", ["https://foreign.example/api/v1/courses", "http://canvas.example/api/v1/courses", "https://canvas.example/elsewhere", "https://person@canvas.example/api/v1/courses"])
def test_unsafe_pagination_never_sends_auth(url):
    calls = []
    c = client(lambda request: (calls.append(request) or httpx.Response(200, json=[], headers={"Link": f'<{url}>; rel="next"'})))
    with pytest.raises(ApplicationError):
        c.get_paginated("/api/v1/courses")
    assert len(calls) == 1


@pytest.mark.parametrize("status,code", [(401, "CANVAS_AUTH_FAILED"), (403, "PERMISSION_DENIED"), (404, "SOURCE_UNAVAILABLE"), (503, "CANVAS_UNAVAILABLE"), (302, "CANVAS_UNAVAILABLE")])
def test_errors_no_response_body_or_headers_exposed(status, code):
    c = client(lambda _: httpx.Response(status, text=TOKEN, headers={"Location": "https://foreign.example"}))
    with pytest.raises(ApplicationError) as error:
        c.get("/api/v1/courses")
    assert error.value.code == code and TOKEN not in str(error.value)


def test_timeout_no_secret_or_cache():
    calls = []
    def fail(request):
        calls.append(request)
        raise httpx.ReadTimeout(TOKEN)
    with pytest.raises(ApplicationError) as error:
        client(fail).get("/api/v1/courses")
    assert len(calls) == 3 and TOKEN not in str(error.value)


def test_course_calendar_feed_is_same_origin_bounded_and_never_receives_bearer_token():
    calls = []
    body = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
    def handler(request):
        calls.append(request)
        assert "Authorization" not in request.headers
        return httpx.Response(200, content=body)
    c = client(handler)
    assert c.download_calendar_feed(BASE + "/feeds/calendars/course_fixture-secret.ics") == body
    assert len(calls) == 1


def test_generic_document_download_validates_ooxml_container_and_keeps_auth_on_origin():
    from docx import Document
    stream = BytesIO()
    document = Document()
    document.add_paragraph("Midterm Exam: 2026-09-23")
    document.save(stream)
    body = stream.getvalue()
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return httpx.Response(200, content=body)
    assert client(handler).download_document(BASE + "/files/1/download", format_name="docx") == body
    assert len(requests) == 1
    with pytest.raises(ApplicationError):
        client(lambda _: httpx.Response(200, content=b"not-a-docx")).download_document(
            BASE + "/files/1/download", format_name="docx")


@pytest.mark.parametrize("url", [
    "https://foreign.example/feeds/calendars/course_secret.ics",
    "http://canvas.example/feeds/calendars/course_secret.ics",
    "https://canvas.example/api/v1/courses/1",
    "https://person@canvas.example/feeds/calendars/course_secret.ics",
])
def test_unsafe_course_calendar_feed_url_is_rejected_without_request_or_secret_leak(url):
    calls = []
    c = client(lambda request: (calls.append(request) or httpx.Response(200, content=b"BEGIN:VCALENDAR")))
    with pytest.raises(ApplicationError) as error:
        c.download_calendar_feed(url)
    assert error.value.code == "CANVAS_CALENDAR_FEED_UNAVAILABLE"
    assert "secret" not in str(error.value) and calls == []


def test_course_calendar_feed_collector_reads_course_ics_and_parses_auditable_events():
    ics = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:lecture-series\r
DTSTART;TZID=Asia/Singapore:20260811T100000\r
SUMMARY;LANGUAGE=en:COURSE105 Lecture\r
RRULE:FREQ=WEEKLY;COUNT=13\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:recess\r
DTSTART;VALUE=DATE:20260922\r
SUMMARY:No class (Recess Week)\r
CATEGORIES:BLACKOUT\r
END:VEVENT\r
END:VCALENDAR\r
"""
    class FeedClient:
        def get(self, path):
            assert path == "/api/v1/courses/11005"
            return {"id": 11005, "calendar": {"ics": BASE + "/feeds/calendars/course_hidden.ics"}}
        def download_calendar_feed(self, url):
            assert url.endswith("course_hidden.ics")
            return ics
    rows = CourseCalendarFeedCollector(FeedClient(), clock=lambda: datetime.now()).collect(course_id="11005")
    assert len(rows) == 2
    assert rows[0]["series_head"] and rows[0]["rrule"] == "FREQ=WEEKLY;COUNT=13"
    assert rows[1]["all_day_date"] == "2026-09-22" and rows[1]["blackout_date"]


def test_rate_limit_and_pagination_loop_and_invalid_shape():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "0"}) if len(calls) == 1 else httpx.Response(200, json=[])
    assert client(handler).get_paginated("/api/v1/courses") == [] and len(calls) == 2
    c = client(lambda _: httpx.Response(200, json=[], headers={"Link": f'<{BASE}/api/v1/courses>; rel="next"'}))
    with pytest.raises(ApplicationError, match="pagination"):
        c.get_paginated("/api/v1/courses")
    c = client(lambda _: httpx.Response(200, json={"error": TOKEN}))
    with pytest.raises(ApplicationError, match="shape"):
        c.get_paginated("/api/v1/courses")


def test_calendar_context_section_all_day_and_explicit_window():
    def handler(request):
        assert request.url.params["context_codes[]"] == "course_1"
        assert request.url.params["start_date"] == "2026-09-20"
        assert "all_events" not in request.url.params
        return httpx.Response(200, json=[
            {"id": 1, "context_code": "course_1"},
            {"id": 2, "context_code": "course_section_6", "effective_context_code": "course_1"},
            {"id": 3, "context_code": "user_1"},
            {"id": 4, "context_code": "course_1", "hidden": True},
            {"id": 5, "context_code": "course_1", "workflow_state": "deleted"},
        ])
    rows = CalendarCollector(client(handler)).collect(course_id="1", start=datetime.fromisoformat("2026-09-21T00:00:00+08:00"), end=datetime.fromisoformat("2026-09-27T23:59:59+08:00"))
    assert [r["id"] for r in rows] == [1, 2]


def test_full_course_calendar_includes_only_verified_course_sections():
    def handler(request):
        if request.url.path == "/api/v1/courses/1/sections":
            return httpx.Response(200, json=[{"id": 6}, {"id": 7}])
        assert request.url.path == "/api/v1/calendar_events"
        assert request.url.params.get_list("context_codes[]") == ["course_1", "course_section_6", "course_section_7"]
        return httpx.Response(200, json=[
            {"id": 1, "context_code": "course_section_6"},
            {"id": 2, "context_code": "course_section_99"},
            {"id": 3, "context_code": "course_1"},
        ])
    rows = CalendarCollector(client(handler)).collect_for_reconciliation(
        course_id="1", start=datetime.fromisoformat("2026-08-01T00:00:00+08:00"),
        end=datetime.fromisoformat("2026-12-31T23:59:59+08:00"))
    assert [row["id"] for row in rows] == [1, 3]


def test_discussions_exclude_announcements():
    c = client(lambda _: httpx.Response(200, json=[{"id": 1}, {"id": 2, "is_announcement": True}]))
    assert DiscussionCollector(c).collect(course_id="1", start=None, end=None) == [{"id": 1}]


def test_courses_allowlist_and_access_restriction():
    c = client(lambda _: httpx.Response(200, json=[{"id": 1, "name": "Course", "course_code": "CS"}, {"id": 2, "access_restricted_by_date": True}]))
    assert CourseRepository(c).list_courses()[0].course_id == "1"
    with pytest.raises(ApplicationError):
        CourseRepository(c, ("2",)).list_courses()


def test_env_alias_precedence_and_no_secret_repr(tmp_path):
    file = tmp_path / ".env"
    file.write_text(f"CANVAS_BASE_URL={BASE}\nCANVAS_API_TOKEN={TOKEN}\n", encoding="utf-8-sig")
    config = load_config(file, environ={})
    assert config.token == TOKEN and config.timezone == "Asia/Singapore" and TOKEN not in repr(config)
    assert config.ocr_enabled and config.ocr_device == "cpu" and config.ocr_dpi == 150
    assert config.ocr_min_confidence == 0.90
    assert load_config(file, environ={"CANVAS_TOKEN": "new-fixture"}).token == "new-fixture"
    for base in ["http://canvas.example", "https://canvas.example/api/v1", "https://user:pass@canvas.example", "bad"]:
        with pytest.raises(ApplicationError):
            load_config(environ={"CANVAS_BASE_URL": base, "CANVAS_TOKEN": TOKEN})


@pytest.mark.parametrize("change", [
    {"CANVAS_OCR_ENABLED": "maybe"}, {"CANVAS_OCR_DEVICE": "gpu"},
    {"CANVAS_OCR_DPI": "600"}, {"CANVAS_OCR_MIN_CONFIDENCE": "1.1"},
])
def test_invalid_ocr_config_is_rejected(change):
    env = {"CANVAS_BASE_URL": BASE, "CANVAS_TOKEN": TOKEN, **change}
    with pytest.raises(ApplicationError) as error:
        load_config(environ=env)
    assert error.value.code == "INVALID_CONFIG"
