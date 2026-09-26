from datetime import datetime
from io import BytesIO, StringIO
import hashlib
import json
from types import SimpleNamespace
import pytest
from reportlab.pdfgen import canvas
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.documents.registry import OfficialDocumentRegistry
from canvas_ddl.documents.repository import DocumentRepository
from canvas_ddl.documents.ingestion import DocumentIngestionService
from canvas_ddl.documents.preparation import DocumentPreparationService
from canvas_ddl.documents.refresh import DocumentLibraryRefresher
from canvas_ddl.documents.semantic import SCHEMA_VERSION
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.cli.main import main
from canvas_ddl.serialization import encode

NOW = datetime.fromisoformat("2026-09-17T13:05:00+08:00")
COURSE = Course("12345", "CS3244", "Machine Learning")


def pdf(day=23, text=None):
    out = BytesIO()
    c = canvas.Canvas(out)
    c.drawString(40, 800, text or f"Midterm Exam: 2026-09-{day} 14:00")
    c.save()
    return out.getvalue()


class Remote:
    base_url = "https://canvas.example"
    def __init__(self):
        self.content, self.version = pdf(), "v1"
        self.list_error = self.download_error = None
        self.requests, self.downloads, self.extra = [], 0, []
        self.missing = False
    def get_paginated(self, path, **kwargs):
        self.requests.append(path)
        if self.list_error:
            raise ApplicationError(self.list_error, "Unavailable")
        return ([] if self.missing else [{"id": 99, "display_name": "Course Outline.pdf",
            "url": self.base_url + "/files/99/download", "updated_at": self.version, "size": len(self.content)}]) + self.extra
    def download_pdf(self, *args, **kwargs):
        self.downloads += 1
        if self.download_error:
            raise ApplicationError(self.download_error, "Unavailable")
        return self.content
    def close(self):
        pass


class Collector:
    source_type = "canvas_assignment"
    def __init__(self, rows=(), error=None):
        self.rows, self.error = list(rows), error
    def collect(self, **kwargs):
        if self.error:
            raise ApplicationError(self.error, "Unavailable")
        return self.rows


def setup(tmp_path, *, registered=True, auto=True, ingested=True, rows=(), error=None,
          document_id="outline", document_text=None):
    remote = Remote()
    if document_text is not None:
        remote.content = pdf(text=document_text)
    path = tmp_path / "outline.pdf"
    path.write_bytes(remote.content)
    row = {"document_id": document_id, "course_id": COURSE.course_id, "course_code": COURSE.course_code,
        "course_name": COURSE.course_name, "document_name": "Course Outline.pdf", "document_kind": "course_outline",
        "source_url": remote.base_url + "/courses/12345/files/99", "path": path.name,
        "sha256": hashlib.sha256(remote.content).hexdigest(), "approved_by": "fixture-human",
        "valid_from": "2026-08-01", "valid_until": "2026-12-31", "auto_refresh": auto}
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"documents": [row] if registered else []}), encoding="utf8")
    registry = OfficialDocumentRegistry(registry_path, canvas_origin=remote.base_url)
    ingestion = DocumentIngestionService(registry, DocumentRepository(tmp_path / "docs.sqlite3"),
        timezone="Asia/Singapore", base_url=remote.base_url, clock=lambda: NOW)
    if registered and ingested:
        ingestion.ingest(document_id, COURSE)
    prepare = DocumentPreparationService(remote, registry)
    refresh = DocumentLibraryRefresher(prepare, ingestion, clock=lambda: NOW)
    service = DeadlineService(remote, base_url=remote.base_url, document_ingestion=ingestion,
        document_preparation=prepare, document_refresher=refresh, clock=lambda: NOW,
        repository=SimpleNamespace(list_courses=lambda: [COURSE]), collectors=[Collector(rows, error)])
    return service, remote, registry, refresh


def query(service, mode="refresh"):
    return encode(service.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下周", offset=1),
                                              types=("exam",), document_mode=mode)))


def test_general_positive_query_still_checks_library_and_finds_pdf_only_exam(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False, rows=[{"id": 1, "name": "Assignment 1", "due_at": "2026-09-23T06:00:00Z"}])
    result = encode(service.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下周", offset=1),
                                                types=None)))
    assert result["count"] == 2
    assert {deadline["type"] for deadline in result["deadlines"]} == {"assignment", "exam"}
    assert result["file_library_check"]["scope"] == "all_course_documents"
    assert remote.requests == ["/api/v1/courses/12345/files"] and remote.downloads == 1


def test_auto_positive_assignment_refreshes_changed_document_date(tmp_path):
    service, remote, _, _ = setup(
        tmp_path,
        rows=[{"id": 1, "name": "Homework 3", "due_at": "2026-09-23T06:00:00Z"}],
        document_text="Assignment 2 due: 2026-09-23 14:00",
    )
    remote.content = pdf(text="Assignment 2 due: 2026-09-24 14:00")
    remote.version = "v2"

    result = encode(service.query(DeadlineQuery(
        time_intent=TimeIntent("calendar_week", "下周", offset=1),
        types=("assignment",),
        document_mode="auto",
    )))

    assignment = next(deadline for deadline in result["deadlines"] if deadline["title"] == "Assignment 2")
    assert assignment["due_at"].startswith("2026-09-24T14:00:00")
    assert all(not (deadline["title"] == "Assignment 2" and
                    deadline["due_at"].startswith("2026-09-23")) for deadline in result["deadlines"])
    assert result["file_library_check"]["actions"][0]["state"] == "updated"


def test_exam_query_checks_pdfs_even_when_canvas_already_has_exam(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False, rows=[{"id": 1, "name": "Midterm Exam", "due_at": "2026-09-23T06:00:00Z"}])
    result = query(service, "auto")
    assert result["count"] == 1 and result["file_library_check"]["scope"] == "all_course_documents"
    assert remote.downloads == 1 and result["document_summary"][0]["source_verified"] is True
    assert registry_authority(result, service) == "canvas_api"


def registry_authority(_result, service):
    return service.document_ingestion.registry.list_documents()[0].source_authority


def test_zero_triggers_authenticated_canvas_registration_and_ingestion(tmp_path):
    service, remote, registry, _ = setup(tmp_path, registered=False)
    result = query(service, "auto")
    assert result["count"] == 1 and result["complete"]
    assert result["file_library_check"]["actions"][0]["state"] == "ingested"
    assert remote.downloads == 1 and registry.list_documents()[0].source_authority == "canvas_api"
    query(service, "auto")
    assert remote.downloads == 1  # Metadata-unchanged pending PDF is not redownloaded.


def test_canvas_api_document_needs_no_source_approval_but_semantics_are_withheld(tmp_path):
    service, remote, registry, _ = setup(tmp_path, registered=False)
    service.document_ingestion.require_semantic_review = True
    remote.content = pdf(text="Nonlinear Programming Midterm Exam: 02/10/26")

    initial = encode(service.query(DeadlineQuery(
        time_intent=TimeIntent("date_range", "2 October", start_date="2026-10-02", end_date="2026-10-02"),
        types=("exam",), document_mode="auto",
    )))
    assert initial["count"] == 0 and not initial["complete"]
    assert initial["document_summary"][0]["source_authority"] == "canvas_api"
    assert initial["document_summary"][0]["valid_from"] is None
    assert initial["document_summary"][0]["valid_until"] is None
    assert initial["document_summary"][0]["state"] == "semantic_review_required"
    document = registry.list_documents()[0]
    assert document.approved_by == "" and document.source_authority == "canvas_api"

    batch = service.semantic_review_requests(document.document_id)
    request = batch["requests"][0]
    payload = {"schema_version": SCHEMA_VERSION, "document_id": document.document_id,
               "document_sha256": batch["document_sha256"], "reviews": [{
        "request_id": request["request_id"], "reason_code": "scheduled_assessment", "events": [{
            "title": "Nonlinear Programming Midterm Exam", "type": "exam",
            "semantic_status": "scheduled", "date_expression": "02/10/26",
            "normalized_date": "2026-10-02", "normalized_time": None,
            "value_kind": "start_at", "evidence_text": "Nonlinear Programming Midterm Exam: 02/10/26",
        }],
    }]}
    applied = service.apply_semantic_review(document.document_id, payload)
    assert applied["complete"] and applied["confirmed"] == 1

    result = encode(service.query(DeadlineQuery(
        time_intent=TimeIntent("date_range", "2 October", start_date="2026-10-02", end_date="2026-10-02"),
        types=("exam",), document_mode="existing",
    )))
    assert result["count"] == 1
    assert result["deadlines"][0]["start_at"] == "2026-10-02T00:00:00+08:00"
    assert result["deadlines"][0]["sources"][0]["source_authority"] == "canvas_api"


def test_changed_approved_version_ingested_before_filter_and_count(tmp_path):
    service, remote, registry, _ = setup(tmp_path)
    old_hash = registry.get("outline").sha256
    remote.content, remote.version = pdf(30), "v2"
    result = query(service)
    assert result["count"] == result["matched_count"] == 0  # New date is outside next week.
    assert result["file_library_check"]["actions"][0]["state"] == "updated"
    row = json.loads(registry.path.read_text())["documents"][0]
    assert row["sha256"] != old_hash and row["version_history"][0]["sha256"] == old_hash
    assert row["approved_by"] == "fixture-human"
    future = service.query(DeadlineQuery(time_intent=TimeIntent("rolling_days", "future", days=180),
                                         types=("exam",), document_mode="existing"))
    assert future.count == 1 and future.deadlines[0].start_at.day == 30


def test_unchanged_library_reuses_artifacts_without_pdf_parse(tmp_path, monkeypatch):
    service, remote, _, _ = setup(tmp_path)
    query(service)  # First check seeds metadata/hash index.
    def forbidden(*args, **kwargs):
        raise AssertionError("Unchanged PDF was parsed")
    monkeypatch.setattr(service.document_ingestion.parser, "parse", forbidden)
    remote.download_error = "DOCUMENT_DOWNLOAD_FAILED"
    result = query(service)
    assert result["count"] == 1 and remote.downloads == 1
    assert result["file_library_check"]["actions"][0]["state"] == "unchanged"


def test_changed_metadata_same_hash_does_not_reparse(tmp_path, monkeypatch):
    service, remote, _, _ = setup(tmp_path)
    query(service)
    remote.version = "v2"
    monkeypatch.setattr(service.document_ingestion.parser, "parse", lambda *a, **k: pytest.fail("Reparsed same hash"))
    assert query(service)["count"] == 1 and remote.downloads == 2


def test_canvas_api_version_updates_without_a_second_approval(tmp_path):
    service, remote, registry, _ = setup(tmp_path, auto=False)
    remote.content, remote.version = pdf(30), "v2"
    result = query(service)
    assert result["count"] == 0 and result["complete"] and not registry.get("outline").refresh_blocked
    assert query(service, "existing")["count"] == 0
    query(service)
    assert remote.downloads == 1


def test_legacy_exact_canvas_row_migrates_after_authenticated_listing(tmp_path):
    service, _, registry, _ = setup(tmp_path, document_id="canvas-12345-99")
    assert registry.get("canvas-12345-99").source_authority == "operator"
    result = query(service)
    document = registry.get("canvas-12345-99")
    assert result["count"] == 1
    assert document.source_authority == "canvas_api" and document.approved_by == ""


def test_missing_remote_document_withheld_and_recovers_on_reappearance(tmp_path):
    service, remote, registry, _ = setup(tmp_path)
    remote.missing = True
    result = query(service)
    assert result["count"] == 0 and registry.get("outline").refresh_blocked
    remote.missing = False
    assert query(service)["count"] == 1 and not registry.get("outline").refresh_blocked


def test_missing_ingestion_automatically_built_from_approved_source(tmp_path):
    service, remote, _, _ = setup(tmp_path, ingested=False)
    result = query(service, "auto")
    assert result["count"] == 1 and result["file_library_check"]["actions"][0]["state"] == "ingested"


def test_changed_invalid_pdf_never_resurrects_old_artifact(tmp_path):
    service, remote, registry, _ = setup(tmp_path)
    remote.content, remote.version = b"%PDF-broken", "v2"
    result = query(service)
    assert result["count"] == 0 and not result["complete"]
    assert result["file_library_check"]["actions"][0]["state"] == "failed"
    assert registry.get("outline").sha256 == hashlib.sha256(remote.content).hexdigest()


def test_permission_failure_keeps_last_validated_evidence_explicitly_partial(tmp_path):
    service, remote, registry, _ = setup(tmp_path)
    remote.list_error = "PERMISSION_DENIED"
    result = query(service)
    assert result["count"] == 1 and not result["complete"]
    assert any("latest document version is unverified" in w for w in result["warnings"])
    assert not registry.get("outline").refresh_blocked  # Unknown state is not a deletion.


def test_file_library_auth_failure_is_fatal(tmp_path):
    service, remote, _, _ = setup(tmp_path)
    remote.list_error = "CANVAS_AUTH_FAILED"
    with pytest.raises(ApplicationError) as error:
        query(service)
    assert error.value.code == "CANVAS_AUTH_FAILED"


def test_download_failure_after_known_version_change_withholds_old_date(tmp_path):
    service, remote, registry, _ = setup(tmp_path)
    query(service)
    remote.version, remote.download_error = "v2", "DOCUMENT_DOWNLOAD_FAILED"
    result = query(service)
    assert result["count"] == 0 and not result["complete"] and registry.get("outline").refresh_blocked
    remote.download_error = None
    assert query(service)["count"] == 1 and not registry.get("outline").refresh_blocked


def test_total_canvas_failure_can_recover_through_approved_document_ingestion(tmp_path):
    service, remote, _, _ = setup(tmp_path, ingested=False, error="SOURCE_UNAVAILABLE")
    result = query(service, "auto")
    assert result["count"] == 1 and not result["complete"] and remote.downloads == 1


def test_refresh_is_scoped_to_resolved_course(tmp_path):
    service, remote, _, _ = setup(tmp_path)
    service.repository = SimpleNamespace(list_courses=lambda: [COURSE, Course("67890", "CS9999", "Another course")])
    service.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下周", offset=1),
                                course_reference="CS3244", document_mode="refresh"))
    assert remote.requests == ["/api/v1/courses/12345/files"]


def test_existing_mode_never_checks_even_partial_zero(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    result = query(service, "existing")
    assert result["count"] == 0 and remote.requests == [] and result["file_library_check"] is None


def test_auto_partial_positive_still_checks_library(tmp_path):
    service, remote, _, _ = setup(tmp_path, error="SOURCE_UNAVAILABLE")
    result = query(service, "auto")
    assert result["count"] == 1 and not result["complete"] and remote.downloads == 1


def test_no_filename_hint_does_not_hide_known_approved_file(tmp_path):
    service, remote, _, _ = setup(tmp_path)
    original = remote.get_paginated
    def renamed(*args, **kwargs):
        rows = original(*args, **kwargs)
        rows[0]["display_name"] = "revised.pdf"
        return rows
    remote.get_paginated = renamed
    remote.content, remote.version = pdf(24), "v2"
    assert query(service)["deadlines"][0]["start_at"].startswith("2026-09-24")


def test_refresh_limit_does_not_mistake_unprocessed_file_for_deleted(tmp_path):
    service, remote, registry, refresh = setup(tmp_path)
    remote.extra = [{"id": 100, "display_name": "Syllabus.pdf", "url": remote.base_url + "/files/100/download", "updated_at": "v1"}]
    data = json.loads(registry.path.read_text())
    second = dict(data["documents"][0], document_id="second", source_url=remote.base_url + "/courses/12345/files/100")
    data["documents"].append(second)
    registry.path.write_text(json.dumps(data), encoding="utf8")
    report = refresh.refresh([COURSE], limit=1)
    assert not report["complete"] and not registry.get("second").refresh_blocked


def test_cli_force_refresh_and_engine_invalid_mode(tmp_path):
    service, remote, _, _ = setup(tmp_path)
    out = StringIO()
    intent = json.dumps(TimeIntent("calendar_week", "下周", offset=1).to_dict(), ensure_ascii=False)
    assert main(["deadlines", "--time-intent", intent, "--type", "exam", "--document-mode", "refresh"],
        factory=lambda _: (service, SimpleNamespace(token="fake")), stdout=out) == 0
    assert json.loads(out.getvalue())["file_library_check"]["state"] == "checked"
    with pytest.raises(ApplicationError):
        query(service, "guess")
