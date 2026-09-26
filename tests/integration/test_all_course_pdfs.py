import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_document_refresh import setup, query, pdf, COURSE
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.documents.inventory import CoursePdfInventory


def rename(remote, name, *, mime=None):
    original = remote.get_paginated
    def listing(*args, **kwargs):
        rows = original(*args, **kwargs)
        for row in rows:
            row["display_name"] = name
            if mime:
                row["content-type"] = mime
        return rows
    remote.get_paginated = listing


def test_unhinted_canvas_pdf_is_registered_ingested_and_queryable(tmp_path):
    service, remote, registry, _ = setup(tmp_path, registered=False)
    rename(remote, "COURSE101_Introduction.pdf")
    result = query(service)
    report = result["file_library_check"]
    assert report["coverage"][0]["listed_pdf_count"] == report["coverage"][0]["processed_pdf_count"] == 1
    assert report["actions"][0]["ingestion"]["pages"] == 1
    summary = result["document_summary"][0]
    assert summary["source_verified"] is True and summary["confirmed"] == 1
    assert result["count"] == 1 and registry.list_documents()[0].source_authority == "canvas_api"
    match = result["document_content_matches"][0]
    assert match["source_verified"] is True and match["unvalidated_excerpts"]
    assert "Midterm Exam" in match["matches"][0]["evidence_text"]


@pytest.mark.parametrize("password,readable", [("", True), ("required-user-password", False)])
def test_encrypted_canvas_pdf_requires_readability_not_separate_approval(tmp_path, password, readable):
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    service, remote, registry, _ = setup(tmp_path, registered=False)
    writer = PdfWriter()
    writer.append_pages_from_reader(PdfReader(BytesIO(pdf())))
    writer.encrypt(password, owner_password="fixture-owner", algorithm="AES-256")
    out = BytesIO()
    writer.write(out)
    remote.content = out.getvalue()
    result = query(service)
    assert len(registry.list_documents()) == 1
    if readable:
        assert result["count"] == 1
        assert result["file_library_check"]["actions"][0]["ingestion"]["pages"] == 1
        assert result["document_summary"][0]["source_verified"] is True
    else:
        assert result["count"] == 0
        assert any("DOCUMENT_PARSE_FAILED" in warning for warning in result["warnings"])


def test_layout_failure_uses_audited_plain_text_and_suppresses_library_diagnostics(tmp_path, monkeypatch, capsys):
    import logging
    from pypdf._page import PageObject
    service, remote, registry, _ = setup(tmp_path, registered=False)
    original = PageObject.extract_text
    def extract(page, *args, **kwargs):
        if kwargs.get("extraction_mode") == "layout":
            logging.getLogger("pypdf.synthetic.child").warning("unsafe-library-diagnostic")
            raise ZeroDivisionError
        return original(page, *args, **kwargs)
    monkeypatch.setattr(PageObject, "extract_text", extract)
    result = query(service)
    assert result["file_library_check"]["actions"][0]["ingestion"]["plain_fallback_pages"] == [1]
    assert result["document_content_matches"][0]["matches"][0]["extraction_mode"] == "plain"
    assert result["count"] == 1 and registry.list_documents()[0].source_authority == "canvas_api"
    assert "unsafe-library-diagnostic" not in capsys.readouterr().err


def test_multiline_exam_context_is_searched_even_when_no_deadline_line_matches(tmp_path):
    from io import BytesIO
    from reportlab.pdfgen import canvas
    service, remote, _, _ = setup(tmp_path, registered=False)
    out = BytesIO()
    c = canvas.Canvas(out)
    c.drawString(40, 800, "Midterm Exam")
    c.drawString(40, 780, "Date: 2026-09-24")
    c.save()
    remote.content = out.getvalue()
    result = query(service)
    assert result["document_summary"][0]["candidate_issues"] == []
    context = result["document_content_matches"][0]["matches"][0]
    assert "Midterm Exam" in context["evidence_text"] and "2026-09-24" in context["evidence_text"]
    assert context["page"] == 1 and result["count"] == 0


def test_default_scan_does_not_stop_at_twenty_pdfs(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    remote.extra = [{"id": i, "display_name": f"Lecture{i}.pdf", "updated_at": "v1", "url": remote.base_url + f"/files/{i}/download"} for i in range(100, 125)]
    result = query(service)
    report = result["file_library_check"]
    assert report["coverage"][0]["listed_pdf_count"] == 26
    assert report["coverage"][0]["unprocessed_pdf_count"] == 0
    assert len(report["actions"]) == len(result["document_summary"]) == remote.downloads == 26
    assert all(a["ingestion"]["confirmed"] == 1 for a in report["actions"])


def test_mime_detects_pdf_without_filename_extension(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    rename(remote, "Course Information", mime="application/pdf")
    result = query(service)
    assert result["file_library_check"]["coverage"][0]["listed_pdf_count"] == 1
    assert result["document_summary"][0]["confirmed"] == 1


def test_unchanged_canvas_pdf_reuses_parsed_artifact(tmp_path, monkeypatch):
    service, remote, _, _ = setup(tmp_path, registered=False)
    query(service)
    monkeypatch.setattr(service.document_ingestion.parser, "parse", lambda *a, **k: pytest.fail("Reparsed unchanged provisional PDF"))
    result = query(service)
    assert remote.downloads == 1 and result["file_library_check"]["actions"][0]["state"] == "unchanged"
    assert result["document_summary"][0]["confirmed"] == 1


def test_changed_canvas_pdf_replaces_old_deadline(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    query(service)
    remote.content, remote.version = pdf(25), "v2"
    result = query(service)
    assert result["count"] == 1
    assert result["deadlines"][0]["start_at"].startswith("2026-09-25")


def test_stored_canvas_validation_flag_cannot_override_revalidation(tmp_path):
    service, remote, registry, _ = setup(tmp_path, registered=False)
    query(service)
    path = service.document_ingestion.repository.path
    with sqlite3.connect(path) as db:
        cid, raw = db.execute("SELECT candidate_id,validation_json FROM candidates").fetchone()
        value = json.loads(raw)
        value["status"] = "rejected"
        db.execute("UPDATE candidates SET validation_json=? WHERE candidate_id=?", (json.dumps(value), cid))
    result = query(service)
    assert result["count"] == 1 and result["document_summary"][0]["confirmed"] == 1
    assert registry.list_documents()[0].source_authority == "canvas_api"


def test_forged_extraction_is_rejected_in_provisional_scan(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    original = service.document_ingestion.extractor
    service.document_ingestion.extractor = SimpleNamespace(extract=lambda p,d: tuple(replace(c,page=99) for c in original.extract(p,d)))
    result = query(service)
    issue = result["document_summary"][0]["candidate_issues"][0]
    assert issue["status"] == "rejected" and "EVIDENCE_OR_TITLE_NOT_SUPPORTED" in issue["reasons"]
    assert result["count"] == 0


def test_removed_canvas_pdf_withholds_previous_fact(tmp_path):
    service, remote, _, _ = setup(tmp_path, registered=False)
    query(service)
    remote.missing = True
    result = query(service)
    assert result["document_summary"][0]["state"] == "remote_version_unavailable"
    assert result["document_summary"][0]["candidate_issues"] == []


def test_module_fallback_finds_unhinted_pdf_when_files_tab_forbidden():
    requests = []
    def listing(path, **kwargs):
        requests.append(path)
        if path.endswith("/files"):
            raise ApplicationError("PERMISSION_DENIED", "Restricted")
        if path.endswith("/modules"):
            return [{"id": 1, "items_count": 2, "items": []}]
        return [{"type": "File", "content_id": 99}, {"type": "File", "content_id": 99}]
    def get(path):
        requests.append(path)
        return {"id": 99, "display_name": "Lecture1.pdf", "url": "https://canvas.example/files/99/download"}
    files, warnings = CoursePdfInventory(SimpleNamespace(get_paginated=listing, get=get)).list("12345")
    assert len(files) == 1 and warnings
    assert requests == ["/api/v1/courses/12345/files", "/api/v1/courses/12345/modules", "/api/v1/courses/12345/modules/1/items", "/api/v1/courses/12345/files/99"]


def test_module_fallback_cannot_claim_full_inventory_or_missing_known_file(tmp_path):
    service, remote, registry, refresh = setup(tmp_path)
    refresh.inventory = SimpleNamespace(list=lambda cid: ([], ["Files inaccessible; partial modules only"]))
    result = query(service)
    assert result["count"] == 1 and not result["complete"]
    assert not registry.get("outline").refresh_blocked
    assert result["file_library_check"]["coverage"][0]["state"] == "module_fallback_partial"


def test_module_fallback_auth_failure_is_fatal():
    def listing(path, **kwargs):
        raise ApplicationError("PERMISSION_DENIED" if path.endswith("/files") else "CANVAS_AUTH_FAILED", "Restricted")
    with pytest.raises(ApplicationError) as error:
        CoursePdfInventory(SimpleNamespace(get_paginated=listing)).list("12345")
    assert error.value.code == "CANVAS_AUTH_FAILED"


def test_octet_stream_pdf_is_not_removed_by_remote_mime_filter():
    def listing(path, **kwargs):
        assert "content_types[]" not in (kwargs.get("params") or {})
        return [{"id": 1, "filename": "Exam Info.pdf", "content-type": "application/octet-stream"},
                {"id": 2, "filename": "notes.txt", "content-type": "text/plain"}]
    files, warnings = CoursePdfInventory(SimpleNamespace(get_paginated=listing)).list("12345")
    assert len(files) == 1 and files[0]["id"] == 1 and not warnings


def test_canvas_document_fact_survives_structured_collector_failure(tmp_path):
    service, remote, registry, _ = setup(tmp_path, registered=False, error="SOURCE_UNAVAILABLE")
    result = query(service)
    assert result["status"] == "partial" and result["count"] == 1
    assert result["document_content_matches"][0]["matches"] and registry.list_documents()[0].source_authority == "canvas_api"
