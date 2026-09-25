from io import BytesIO, StringIO
import hashlib
import json
from types import SimpleNamespace
import httpx
import pytest
from reportlab.pdfgen import canvas
from canvas_ddl.canvas.client import CanvasClient
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.documents.registry import OfficialDocumentRegistry
from canvas_ddl.documents.preparation import DocumentPreparationService
from canvas_ddl.documents.ingestion import DocumentIngestionService
from canvas_ddl.documents.repository import DocumentRepository
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.cli.main import main
from datetime import datetime, timezone

COURSE = Course("12345", "CS3244", "Machine Learning")
TOKEN = "test-private-token"


def pdf():
    out = BytesIO()
    c = canvas.Canvas(out)
    c.drawString(40, 800, "Midterm Exam: 2026-09-23 14:00")
    c.save()
    return out.getvalue()


def setup(tmp_path, files=None, handler=None):
    content = pdf()
    files = files if files is not None else [{"id": 99, "display_name": "Course Outline.pdf", "url": "https://canvas.example/files/99/download"}]
    requests = []
    def route(request):
        requests.append(request)
        if request.url.path == "/api/v1/courses/12345/files":
            return httpx.Response(200, json=files)
        return handler(request) if handler else httpx.Response(200, content=content)
    client = CanvasClient("https://canvas.example", TOKEN, transport=httpx.MockTransport(route))
    path = tmp_path / "registry.json"
    path.write_text('{"documents": []}', encoding="utf8")
    registry = OfficialDocumentRegistry(path, canvas_origin=client.base_url)
    preparation = DocumentPreparationService(client, registry)
    return preparation, registry, requests, content


def test_preparation_downloads_without_approving_or_leaking_url(tmp_path):
    prepare, registry, requests, content = setup(tmp_path)
    result = prepare.prepare([COURSE])
    row = json.loads((tmp_path / "pending-review.json").read_text())["documents"][0]
    assert result["requires_review"] and result["documents"][0]["state"] == "pending_review"
    assert row["active"] is False and row["approved_by"] == "" and row["valid_from"] is None
    assert row["course_id"] == COURSE.course_id and row["sha256"] == hashlib.sha256(content).hexdigest()
    assert registry.list_documents() == ()
    assert (tmp_path / row["path"]).read_bytes() == content
    assert TOKEN not in (tmp_path / "pending-review.json").read_text()
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in requests)


def test_explicit_review_registers_and_engine_ingests(tmp_path):
    prepare, registry, _, _ = setup(tmp_path)
    prepare.prepare([COURSE])
    ingestion = DocumentIngestionService(registry, DocumentRepository(tmp_path / "docs.sqlite3"),
        timezone="Asia/Singapore", base_url=prepare.client.base_url, clock=lambda: datetime.now(timezone.utc))
    service = DeadlineService(prepare.client, base_url=prepare.client.base_url, document_ingestion=ingestion,
        document_preparation=prepare, repository=SimpleNamespace(list_courses=lambda: [COURSE]), collectors=[])
    result = service.approve_document("canvas-12345-99", approved_by="maintainer", valid_from="2026-08-01", valid_until="2026-12-31")
    assert result["confirmed"] == 1 and len(registry.list_documents()) == 1


@pytest.mark.parametrize("approver", ["Codex", "LLM", "", "replace-me"])
def test_no_manufactured_approval(tmp_path, approver):
    prepare, registry, _, _ = setup(tmp_path)
    prepare.prepare([COURSE])
    with pytest.raises(ApplicationError):
        prepare.approve("canvas-12345-99", approved_by=approver, valid_from="2026-08-01", valid_until="2026-12-31")
    assert registry.list_documents() == ()


def test_changed_download_cannot_be_approved(tmp_path):
    prepare, registry, _, _ = setup(tmp_path)
    result = prepare.prepare([COURSE])
    from pathlib import Path
    Path(result["documents"][0]["path"]).write_bytes(b"changed")
    with pytest.raises(ApplicationError, match="unchanged"):
        prepare.approve("canvas-12345-99", approved_by="maintainer", valid_from="2026-08-01", valid_until="2026-12-31")
    assert registry.list_documents() == ()


def test_redirect_never_forwards_auth_to_approved_external_host(tmp_path):
    def route(request):
        if request.url.host == "canvas.example":
            return httpx.Response(302, headers={"Location": "https://cdn.example/document?signature=private"})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=pdf())
    prepare, _, requests, _ = setup(tmp_path, handler=route)
    prepare.allowed_hosts = ("cdn.example",)
    result = prepare.prepare([COURSE])
    assert result["documents"][0]["state"] == "pending_review"
    assert len(requests) == 3 and "signature" not in (tmp_path / "pending-review.json").read_text()


def test_unapproved_redirect_refused_before_request(tmp_path):
    prepare, registry, requests, _ = setup(tmp_path, handler=lambda r: httpx.Response(302, headers={"Location": "https://foreign.example/file"}))
    result = prepare.prepare([COURSE])
    assert result["documents"][0]["error_code"] == "DOCUMENT_DOWNLOAD_HOST_UNAPPROVED"
    assert len(requests) == 2 and registry.list_documents() == ()


def test_canvas_storage_destination_supported_without_evidence_approval(tmp_path):
    def route(request):
        if request.url.host == "canvas.example":
            return httpx.Response(302, headers={"Location": "https://a123-99.cluster273.canvas-user-content.com/file"})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=pdf())
    prepare, registry, _, _ = setup(tmp_path, handler=route)
    assert prepare.prepare([COURSE])["documents"][0]["state"] == "pending_review"
    assert registry.list_documents() == ()


def test_similar_storage_hostname_does_not_bypass_allowlist(tmp_path):
    prepare, _, requests, _ = setup(tmp_path, handler=lambda r: httpx.Response(302,
        headers={"Location": "https://a123-99.cluster273.canvas-user-content.com.attacker.example/file"}))
    assert prepare.prepare([COURSE])["documents"][0]["state"] == "download_failed"
    assert len(requests) == 2


@pytest.mark.parametrize("response", [httpx.Response(200, content=b"<html>login</html>"),
    httpx.Response(200, headers={"Content-Length": str(31 * 1024 * 1024)}, content=b"%PDF-test"), httpx.Response(403)])
def test_failed_downloads_are_not_registered(tmp_path, response):
    prepare, registry, _, _ = setup(tmp_path, handler=lambda r: response)
    result = prepare.prepare([COURSE])
    assert result["status"] == "partial" and result["documents"][0]["state"] == "download_failed"
    assert registry.list_documents() == ()


def test_only_hinted_visible_supported_documents_and_safe_local_names(tmp_path):
    files = [{"id": i, "display_name": name, "url": "https://canvas.example/files/99/download", **options}
             for i, name, options in [(1, "lecture.pdf", {}), (2, "Outline.docx", {}),
             (3, "Syllabus.pdf", {"locked_for_user": True}), (4, "../../Course Outline.pdf", {})]]
    prepare, _, _, _ = setup(tmp_path, files=files)
    result = prepare.prepare([COURSE])
    assert len(result["documents"]) == 2
    assert {item["document_id"] for item in result["documents"]} == {"canvas-12345-2", "canvas-12345-4"}
    rows = json.loads((tmp_path / "pending-review.json").read_text())["documents"]
    assert any(row["path"].startswith("downloaded/canvas-12345-4-") for row in rows)


def test_limit_and_repeat_keep_registry_approval_untouched(tmp_path):
    files = [{"id": i, "display_name": "Outline.pdf", "url": "https://canvas.example/files/99/download"} for i in (1, 2)]
    prepare, registry, _, _ = setup(tmp_path, files=files)
    result = prepare.prepare([COURSE], limit=1)
    assert len(result["documents"]) == 1 and result["status"] == "partial"
    prepare.approve("canvas-12345-1", approved_by="maintainer", valid_from="2026-08-01", valid_until="2026-12-31")
    prepare.prepare([COURSE], limit=1)
    assert registry.get("canvas-12345-1").approved_by == "maintainer"


def test_cli_requires_explicit_courses_and_official_confirmation(tmp_path):
    calls = []
    service = SimpleNamespace(client=SimpleNamespace(close=lambda: None),
        prepare_documents=lambda courses, limit: calls.append((courses, limit)) or {"status": "ok"})
    factory = lambda env: (service, SimpleNamespace(token=TOKEN))
    assert main(["prepare-documents", "--course", "CS3244"], factory=factory, stdout=StringIO()) == 0
    assert calls == [(["CS3244"], 20)]
    assert main(["prepare-documents"], factory=factory, stdout=StringIO()) == 2
    assert main(["approve-document", "--document", "id", "--approved-by", "maintainer", "--valid-from", "2026-08-01", "--valid-until", "2026-12-31"], factory=factory, stdout=StringIO()) == 2
