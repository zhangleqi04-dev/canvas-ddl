from dataclasses import replace
from datetime import datetime
from io import StringIO
import hashlib
import json
import sqlite3
import pytest
from reportlab.pdfgen import canvas
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.courses.models import Course
from canvas_ddl.documents.registry import OfficialDocumentRegistry
from canvas_ddl.documents.repository import DocumentRepository
from canvas_ddl.documents.ingestion import DocumentIngestionService
from canvas_ddl.documents.extractor import DeadlineExtractor
from canvas_ddl.documents.models import DeadlineCandidate
from canvas_ddl.documents.ocr import OcrPageResult, OcrUnavailable
from canvas_ddl.documents.parser import DocumentParser
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.time_intent import TimeIntent
from canvas_ddl.deadlines.service import DeadlineService
from canvas_ddl.serialization import encode

NOW = datetime.fromisoformat("2026-09-17T13:05:00+08:00")
COURSE = Course("12345", "CS3244", "Machine Learning")


class Courses:
    def list_courses(self):
        return [COURSE]


class Client:
    def get(self, *args, **kwargs):
        raise ApplicationError("SOURCE_UNAVAILABLE", "No resource")

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


def setup_documents(tmp_path, lines, *, extractor=None, parser=None, document_id="outline", second_page=False):
    pdf = tmp_path / (document_id + ".pdf")
    c = canvas.Canvas(str(pdf))
    if second_page:
        c.drawString(40, 800, "CS3244 Course Outline")
        c.showPage()
    for i, line in enumerate(lines):
        c.drawString(40, 800 - i * 20, line)
    if not lines:
        c.showPage()
    c.save()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    registry_path = tmp_path / "registry.json"
    rows = json.loads(registry_path.read_text())["documents"] if registry_path.exists() else []
    rows.append({"document_id": document_id, "course_id": "12345", "course_code": "CS3244", "course_name": "Machine Learning",
                 "document_name": document_id + ".pdf", "document_kind": "course_outline", "source_url": "https://canvas.example/courses/12345/files/99",
                 "path": pdf.name, "sha256": digest, "approved_by": "fixture-maintainer", "valid_from": "2026-08-01", "valid_until": "2026-12-31"})
    registry_path.write_text(json.dumps({"documents": rows}), encoding="utf8")
    registry = OfficialDocumentRegistry(registry_path, canvas_origin="https://canvas.example")
    repository = DocumentRepository(tmp_path / "documents.sqlite3")
    ingestion = DocumentIngestionService(registry, repository, timezone="Asia/Singapore", base_url="https://canvas.example",
                                         clock=lambda: NOW, extractor=extractor, parser=parser)
    return ingestion, pdf


def service(ingestion, rows=(), error=None, collectors=None, teaching_calendar_collector=None):
    return DeadlineService(Client(), base_url="https://canvas.example", repository=Courses(), clock=lambda: NOW,
                           collectors=collectors or [Collector(rows, error)], document_ingestion=ingestion,
                           teaching_calendar_collector=teaching_calendar_collector)


def assignment(day=23, title="Midterm Exam", **extra):
    return {"id": 1, "name": title, "due_at": f"2026-09-{day:02}T06:00:00Z", **extra}


def query(engine, intent=None, **options):
    intent = intent or TimeIntent("calendar_week", "下周", offset=1)
    return encode(engine.query(DeadlineQuery(time_intent=intent, **options)))


@pytest.mark.parametrize("structured", [False, True])
def test_pdf_only_canonical_with_evidence_and_no_query_pdf_read(tmp_path, monkeypatch, structured):
    ingestion, pdf = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"], second_page=True)
    engine = service(ingestion)
    result = engine.ingest_document("outline")
    assert result["confirmed"] == 1 and result["pages"] == 2
    pdf.unlink()
    monkeypatch.setattr(ingestion.parser, "parse", lambda *a, **k: pytest.fail("Query must not parse PDFs"))
    data = encode(engine.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下一整个自然周", offset=1),
                                            types=("exam",)))) if structured else query(engine, types=("exam",))
    assert data["status"] == "ok" and data["count"] == 1 == len(data["deadlines"])
    d = data["deadlines"][0]
    assert d["canvas_resource_id"] is None and d["canonical_reason"] == "validated_official_document"
    evidence = d["sources"][0]
    assert evidence["document_name"] == "outline.pdf" and evidence["page"] == 2
    assert evidence["evidence_text"] == "Midterm Exam: 2026-09-23 14:00"
    assert evidence["validation"]["status"] == "confirmed" and evidence["document_sha256"]
    assert d["due_at"] is None and d["start_at"] == "2026-09-23T14:00:00+08:00"
    assert data["data_freshness"] == "live_with_ingested_documents"
    assert engine.get_deadline(d["deadline_id"]).title == "Midterm Exam"


@pytest.mark.parametrize("structured", [False, True])
def test_same_date_merge_and_canvas_pdf_conflict(tmp_path, structured):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"])
    ingestion.ingest("outline", COURSE)
    def exam_query(engine):
        return encode(engine.query(DeadlineQuery(time_intent=TimeIntent("calendar_week", "下周", offset=1),
                                                types=("exam",)))) if structured else query(engine, types=("exam",))
    data = exam_query(service(ingestion, [assignment()]))
    assert data["count"] == 1 and data["deadlines"][0]["reconciliation_status"] == "agreed"
    assert len(data["deadlines"][0]["sources"]) == 2
    data = exam_query(service(ingestion, [assignment(24)]))
    assert data["count"] == 1 and data["complete"]
    d = data["deadlines"][0]
    assert d["due_at"] == "2026-09-24T14:00:00+08:00" and d["reconciliation_status"] == "resolved_conflict"
    assert d["conflicts"][0]["alternative_value"] == "2026-09-23T14:00:00+08:00"
    assert d["conflicts"][0]["resolution"] == "live_canvas_preferred"
    assert d["conflicts"][0]["alternative_source"]["page"] == 1


def test_shifted_date_reconciliation_before_time_filter(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"])
    ingestion.ingest("outline", COURSE)
    engine = service(ingestion, [assignment(30)])
    assert query(engine)["count"] == 0  # old PDF date must not remain next week
    assert query(engine, TimeIntent("rolling_days", "未来14天", days=14))["count"] == 1


def test_canvas_calendar_outside_window_is_verified_for_documents(tmp_path):
    from canvas_ddl.collectors.calendar import CalendarCollector
    class CalendarClient(Client):
        def get_paginated(self, path, params):
            if path.endswith("/sections"):
                return []
            assert params["all_events"] == "true" and params["context_codes[]"] == "course_12345"
            return [{"id": 6, "context_code": "course_12345", "title": "Midterm Exam", "start_at": "2026-09-30T06:00:00Z"}]
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"])
    ingestion.ingest("outline", COURSE)
    engine = service(ingestion, collectors=[CalendarCollector(CalendarClient())])
    assert query(engine)["count"] == 0


def test_structured_query_timezone_preserves_document_calendar_day(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Quiz 3: 2026-09-23"])
    assert ingestion.ingest("outline", COURSE)["confirmed"] == 1
    intent = TimeIntent("date_range", "2026年9月22日（纽约时间）", start_date="2026-09-22",
                        end_date="2026-09-22", timezone="America/New_York")
    result = encode(service(ingestion).query(DeadlineQuery(time_intent=intent, types=("quiz",))))
    # NY Sep22 spans Singapore Sep22–23. A date-only source has no known clock
    # time, so preserve the source date and match its overlapping calendar day.
    assert result["count"] == 1 and result["deadlines"][0]["date_only"]
    assert result["deadlines"][0]["all_day_date"] == "2026-09-23"
    assert result["query"]["timezone"] == "America/New_York"


@pytest.mark.parametrize("text,reason", [
    ("Midterm Exam: Week 7 Friday", "CANVAS_TEACHING_WEEK_REQUIRED"),
    ("Midterm Exam may be on 2026-09-23 14:00", "AMBIGUOUS_OR_NON_DEADLINE_TEXT"),
    ("Assignment 2: 2026-09-23", "DEADLINE_ROLE_REQUIRED"),
    ("Midterm Exam: 2026-09-23 or 2026-09-24", "EXPLICIT_UNIQUE_DATE_REQUIRED"),
    ("Quiz 1 opens: 2026-09-23", "AMBIGUOUS_OR_NON_DEADLINE_TEXT"),
    ("Exam: 2026-09-23 2:00 pm", "TIME_FORMAT_REQUIRES_REVIEW"),
    ("Midterm Exam: September 23", "EXPLICIT_UNIQUE_DATE_REQUIRED"),
    ("Assignment 2 due next Friday", "EXPLICIT_UNIQUE_DATE_REQUIRED"),
])
def test_ambiguous_proposals_never_confirmed(tmp_path, text, reason):
    ingestion, _ = setup_documents(tmp_path, [text])
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 0 and report["unresolved"] == 1
    data = query(service(ingestion))
    assert data["count"] == 0 and data["status"] == "partial"
    assert data["document_summary"][0]["candidate_issues"][0]["reasons"] == [reason]


@pytest.mark.parametrize("text,type_,due,date_only", [
    ("Assignment 2 due: 2026-09-23 23:59", "assignment", "2026-09-23T23:59:00+08:00", False),
    ("Quiz 3: 23 September 2026", "quiz", None, True),
    ("Midterm Exam: September 23, 2026 14:00", "exam", None, False),
])
def test_explicit_official_deadlines_validated(tmp_path, text, type_, due, date_only):
    ingestion, _ = setup_documents(tmp_path, [text])
    assert ingestion.ingest("outline", COURSE)["confirmed"] == 1
    d = query(service(ingestion), types=(type_,))["deadlines"][0]
    assert d["type"] == type_ and d["due_at"] == due and d["date_only"] is date_only


def test_llm_proposal_wrong_date_page_title_or_scope_is_not_a_fact(tmp_path):
    class Proposer:
        def extract(self, parsed, document):
            base = DeadlineExtractor().extract(parsed, document)[0]
            return tuple(replace(base, candidate_id=f"{i:024x}", **change) for i, change in enumerate([
                {"date_expression": "2026-09-30"}, {"page": 99}, {"title": "Final Exam"},
                {"course_id": "999"}, {"evidence_text": "Final Exam: 2026-09-23 14:00"},
            ], 1))
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"], extractor=Proposer())
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 0 and report["unresolved"] + report["rejected"] == 5
    assert query(service(ingestion))["count"] == 0


def test_unregistered_unofficial_hash_change_and_course_mismatch(tmp_path):
    ingestion, pdf = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    with pytest.raises(ApplicationError) as error:
        ingestion.ingest("unregistered", COURSE)
    assert error.value.code == "DOCUMENT_NOT_REGISTERED"
    with pytest.raises(ApplicationError) as error:
        ingestion.ingest("outline", Course("999", "CS", "Wrong course"))
    assert error.value.code == "DOCUMENT_COURSE_MISMATCH"
    pdf.write_bytes(pdf.read_bytes() + b"changed")
    with pytest.raises(ApplicationError) as error:
        ingestion.ingest("outline", COURSE)
    assert error.value.code == "DOCUMENT_HASH_MISMATCH"
    file = ingestion.registry.path
    content = json.loads(file.read_text())
    content["documents"][0]["source_url"] = "https://unofficial.example/notes.pdf"
    file.write_text(json.dumps(content))
    with pytest.raises(ApplicationError):
        ingestion.registry.list_documents()


def test_document_replacement_requires_ingestion_and_old_version_cannot_return(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    path = ingestion.registry.path
    data = json.loads(path.read_text())
    data["documents"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    result = query(service(ingestion))
    assert result["count"] == 0 and result["status"] == "partial"
    assert result["document_summary"][0]["state"] == "not_ingested_or_version_mismatch"


def test_document_only_when_canvas_fetch_fails_is_explicitly_partial(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    result = query(service(ingestion, error="CANVAS_UNAVAILABLE"))
    assert result["count"] == 1 and result["data_freshness"] == "mixed_partial" and not result["complete"]
    with pytest.raises(ApplicationError) as error:
        query(service(ingestion, error="CANVAS_AUTH_FAILED"))
    assert error.value.code == "CANVAS_AUTH_FAILED"


def test_different_courses_similar_titles_and_multiple_canvas_matches(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    result = query(service(ingestion, [assignment(title="Midterm Exam 2")]))
    assert result["count"] == 2
    result = query(service(ingestion, [assignment(), {**assignment(24), "id": 2}]))
    assert not result["complete"] and result["count"] == 2
    assert result["unresolved_deadlines"][0]["reconciliation_status"] == "ambiguous"


def test_conflicting_official_documents_do_not_choose_newest_silently(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"], document_id="outline")
    ingestion.ingest("outline", COURSE)
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-24"], document_id="handout")
    ingestion.ingest("handout", COURSE)
    result = query(service(ingestion))
    assert result["count"] == 0 and not result["complete"] and len(result["unresolved_deadlines"]) == 2
    assert result["unresolved_deadlines"][0]["conflicts"][0]["resolution"] == "unresolved_document_conflict"


def test_live_canvas_explicitly_undated_does_not_resurrect_pdf_due(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Assignment 2 due: 2026-09-23 23:59"])
    ingestion.ingest("outline", COURSE)
    result = query(service(ingestion, [{"id": 1, "name": "Assignment 2", "due_at": None}]))
    assert result["count"] == 0 and not result["complete"]
    assert result["unresolved_deadlines"][0]["canonical_reason"] == "live_canvas_unscheduled"


def test_persisted_confirmed_flag_cannot_bypass_validator(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: Week 7 Friday"])
    ingestion.ingest("outline", COURSE)
    with sqlite3.connect(ingestion.repository.path) as db:
        stored = json.loads(db.execute("SELECT validation_json FROM candidates").fetchone()[0])
        stored["status"] = "confirmed"
        db.execute("UPDATE candidates SET validation_json=?", (json.dumps(stored),))
    assert query(service(ingestion))["count"] == 0


def test_canvas_course_calendar_maps_relative_week_to_reference_not_canonical(tmp_path):
    class CalendarRows:
        source_type = "canvas_calendar_event"

        def collect_for_reconciliation(self, **_kwargs):
            return [
                {"id": 10, "title": "First lecture", "start_at": "2026-08-11T02:00:00Z"},
                {"id": 11, "title": "No class (Recess Week)", "start_at": "2026-09-22T02:00:00Z"},
            ]

        def collect(self, **kwargs):
            return self.collect_for_reconciliation(**kwargs)

    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: Week 7 Friday"])
    ingestion.ingest("outline", COURSE)
    engine = service(ingestion, collectors=[CalendarRows()])
    data = encode(engine.query(DeadlineQuery(
        start=datetime.fromisoformat("2026-10-02T00:00:00+08:00"),
        end=datetime.fromisoformat("2026-10-02T23:59:59+08:00"), types=("exam",))))
    assert data["count"] == 0 and data["reference_count"] == 1
    reference = data["reference_deadlines"][0]
    assert reference["title"] == "Midterm Exam" and reference["type"] == "exam"
    assert reference["window_start_at"].startswith("2026-10-02T00:00:00")
    assert reference["window_end_at"].startswith("2026-10-02T23:59:59")
    assert reference["date_precision"] == "day" and reference["confidence"] == "medium"
    assert reference["validation"]["status"] == "reference"
    assert len(reference["sources"]) == 3


def test_course_ics_fallback_maps_relative_week_when_rest_calendar_has_no_rows(tmp_path):
    class EmptyCalendar:
        source_type = "canvas_calendar_event"
        def collect_for_reconciliation(self, **_kwargs):
            return []
        collect = collect_for_reconciliation

    class FeedRows:
        def collect(self, **_kwargs):
            return [
                {"id": "ics-lecture", "title": "COURSE105 Lecture", "start_at": "2026-08-11T10:00:00+08:00",
                 "series_head": True, "rrule": "FREQ=WEEKLY;COUNT=13"},
                {"id": "ics-recess", "title": "Recess Week", "all_day_date": "2026-09-22",
                 "blackout_date": True},
            ]

    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: Week 7 Friday"])
    ingestion.ingest("outline", COURSE)
    data = encode(service(ingestion, collectors=[EmptyCalendar()], teaching_calendar_collector=FeedRows()).query(
        DeadlineQuery(start=datetime.fromisoformat("2026-10-02T00:00:00+08:00"),
                      end=datetime.fromisoformat("2026-10-02T23:59:59+08:00"), types=("exam",))))
    assert data["count"] == 0 and data["reference_count"] == 1
    assert data["reference_deadlines"][0]["window_start_at"].startswith("2026-10-02")
    assert {source["source_type"] for source in data["reference_deadlines"][0]["sources"]} == {
        "official_document", "canvas_calendar_feed"
    }
    feed_coverage = [item for item in data["coverage"] if item["source_type"] == "canvas_calendar_feed"]
    assert feed_coverage[0]["state"] == "available" and feed_coverage[0]["record_count"] == 2


def test_tentative_relative_week_never_enters_reference_results(tmp_path):
    class CalendarRows:
        source_type = "canvas_calendar_event"
        def collect_for_reconciliation(self, **_kwargs):
            return [{"id": 10, "title": "First lecture", "start_at": "2026-08-11T02:00:00Z"}]
        collect = collect_for_reconciliation

    ingestion, _ = setup_documents(tmp_path, ["Tentative Midterm Exam: Week 7 Friday"])
    ingestion.ingest("outline", COURSE)
    data = encode(service(ingestion, collectors=[CalendarRows()]).query(DeadlineQuery(
        start=datetime.fromisoformat("2026-09-25T00:00:00+08:00"),
        end=datetime.fromisoformat("2026-09-25T23:59:59+08:00"), types=("exam",))))
    assert data["reference_count"] == 0


def test_old_parser_artifact_is_withheld_until_reingestion(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    with sqlite3.connect(ingestion.repository.path) as db:
        stored = json.loads(db.execute("SELECT parsed_json FROM documents").fetchone()[0])
        stored["parser_version"] = "pypdf-layout-v2"
        db.execute("UPDATE documents SET parsed_json=?", (json.dumps(stored),))
    result = query(service(ingestion))
    assert result["count"] == 0 and not result["complete"]
    assert result["document_summary"][0]["state"] == "parser_version_mismatch"


def test_truncated_llm_quote_cannot_hide_tentative_qualification(tmp_path):
    class Cropped:
        def extract(self, parsed, document):
            return (DeadlineCandidate("1" * 24, document.document_id, document.course_id, "Midterm Exam", 1,
                                      "Midterm Exam: 2026-09-23 14:00", "2026-09-23", "llm-proposal"),)
    ingestion, _ = setup_documents(tmp_path, ["Tentative: Midterm Exam: 2026-09-23 14:00"], extractor=Cropped())
    assert ingestion.ingest("outline", COURSE)["confirmed"] == 0
    assert query(service(ingestion))["count"] == 0


def test_page_header_qualification_and_repeated_instances_stay_unresolved(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Tentative assessment schedule", "Midterm Exam: 2026-09-23 14:00"])
    assert ingestion.ingest("outline", COURSE)["confirmed"] == 0
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00", "Midterm Exam: 2026-09-24 14:00"], document_id="handout")
    assert ingestion.ingest("handout", COURSE)["confirmed"] == 2
    result = query(service(ingestion, [assignment()]))
    assert result["count"] == 1 and not result["complete"]


def test_revoked_document_and_out_of_period_or_invalid_dates(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2027-09-23", "Quiz 1: 2026-02-30"])
    assert ingestion.ingest("outline", COURSE)["rejected"] == 2
    path = ingestion.registry.path
    content = json.loads(path.read_text())
    content["documents"][0]["active"] = False
    path.write_text(json.dumps(content))
    assert query(service(ingestion))["count"] == 0


def test_same_date_only_agreement_and_same_date_time_conflict(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    data = query(service(ingestion, [assignment()]))
    assert data["deadlines"][0]["reconciliation_status"] == "agreed"
    assert not data["deadlines"][0]["date_only"]
    ingestion, _ = setup_documents(tmp_path, ["Quiz 1: 2026-09-23 15:00"], document_id="handout")
    ingestion.ingest("handout", COURSE)
    data = query(service(ingestion, [assignment(title="Quiz 1", is_quiz_assignment=True)]), types=("quiz",))
    assert data["count"] == 1 and data["deadlines"][0]["conflicts"]


def test_stable_document_id_details_after_canvas_counterpart_appears(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"])
    ingestion.ingest("outline", COURSE)
    prior = query(service(ingestion))["deadlines"][0]["deadline_id"]
    engine = service(ingestion, [assignment(30)])
    assert engine.get_deadline(prior).due_at.day == 30
    class DetailClient(Client):
        def get(self, *args, **kwargs):
            return assignment(30)
    engine.client = DetailClient()
    d = engine.get_deadline("course_12345_assignment_1")
    assert d.conflicts and len(d.sources) == 2


def test_atomic_reingestion_and_bad_pdf(tmp_path):
    ingestion, pdf = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    ingestion.ingest("outline", COURSE)
    ingestion.ingest("outline", COURSE)
    assert query(service(ingestion))["count"] == 1
    pdf.write_bytes(b"not a PDF")
    content = json.loads(ingestion.registry.path.read_text())
    content["documents"][0]["sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    ingestion.registry.path.write_text(json.dumps(content))
    with pytest.raises(ApplicationError) as error:
        ingestion.ingest("outline", COURSE)
    assert error.value.code == "DOCUMENT_PARSE_FAILED"


def test_ingestion_and_document_canonical_details_through_cli(tmp_path):
    from types import SimpleNamespace
    from canvas_ddl.cli.main import main
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"])
    engine = service(ingestion)
    factory = lambda _: (engine, SimpleNamespace(token="fixture-token"))
    out = StringIO()
    assert main(["ingest", "--document", "outline"], factory=factory, stdout=out) == 0
    assert json.loads(out.getvalue())["ingestion"]["confirmed"] == 1
    out = StringIO()
    assert main(["documents"], factory=factory, stdout=out) == 0
    assert json.loads(out.getvalue())["documents"][0]["confirmed"] == 1
    identifier = query(engine)["deadlines"][0]["deadline_id"]
    out = StringIO()
    assert main(["deadline", "--id", identifier], factory=factory, stdout=out) == 0
    data = json.loads(out.getvalue())
    assert data["data_freshness"] == "ingested_document"
    assert data["deadline"]["sources"][0]["evidence_text"]


def test_date_only_plus_precise_pdf_agreement_preserves_precision(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"], document_id="outline")
    ingestion.ingest("outline", COURSE)
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"], document_id="handout")
    ingestion.ingest("handout", COURSE)
    result = query(service(ingestion))
    assert result["count"] == 1 and result["complete"]
    d = result["deadlines"][0]
    assert not d["date_only"] and d["start_at"] == "2026-09-23T14:00:00+08:00"
    assert d["sources"][0]["document_name"] == "handout.pdf" and len(d["sources"]) == 2


def test_date_only_pdf_does_not_bridge_two_disagreeing_precise_pdf_times(tmp_path):
    for identifier, text in [("outline", "Midterm Exam: 2026-09-23"),
                             ("handout", "Midterm Exam: 2026-09-23 14:00"),
                             ("syllabus", "Midterm Exam: 2026-09-23 15:00")]:
        ingestion, _ = setup_documents(tmp_path, [text], document_id=identifier)
        ingestion.ingest(identifier, COURSE)
    result = query(service(ingestion))
    assert result["count"] == 0 and not result["complete"]


def test_empty_scanned_page_is_explicitly_incomplete(tmp_path):
    ingestion, _ = setup_documents(tmp_path, [])
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 0 and report["warnings"]
    result = query(service(ingestion))
    assert result["count"] == 0 and not result["complete"]


def test_scanned_pdf_uses_local_ocr_and_preserves_provenance(tmp_path):
    class FakeOcr:
        def recognize_pdf_page(self, content, page_index):
            assert content.startswith(b"%PDF") and page_index == 0
            return OcrPageResult("Midterm Exam: 2026-09-23 14:00", (0.98,))

    parser = DocumentParser(ocr=FakeOcr())
    ingestion, _ = setup_documents(tmp_path, [], parser=parser)
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 1 and report["ocr_pages"] == [1] and not report["warnings"]
    result = query(service(ingestion), types=("exam",))
    source = result["deadlines"][0]["sources"][0]
    assert source["extraction_mode"] == "ocr_ppocrv6_small"
    assert source["ocr_engine"] == "PP-OCRv6_small" and source["ocr_confidence"] == 0.98
    assert source["validation"]["checks"][-2:] == ["OCR_PP_OCRV6_SMALL", "OCR_CONFIDENCE_THRESHOLD"]


def test_low_confidence_ocr_deadline_requires_review(tmp_path):
    class FakeOcr:
        def recognize_pdf_page(self, content, page_index):
            return OcrPageResult("Midterm Exam: 2026-09-23 14:00", (0.72,))

    ingestion, _ = setup_documents(tmp_path, [], parser=DocumentParser(ocr=FakeOcr()))
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 0 and report["unresolved"] == 1
    result = query(service(ingestion), types=("exam",))
    assert result["count"] == 0
    assert result["document_summary"][0]["candidate_issues"][0]["reasons"] == ["OCR_CONFIDENCE_REQUIRES_REVIEW"]


def test_native_text_page_does_not_start_ocr(tmp_path):
    class ForbiddenOcr:
        def recognize_pdf_page(self, *args):
            raise AssertionError("OCR should be a fallback only")

    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23 14:00"],
                                   parser=DocumentParser(ocr=ForbiddenOcr()))
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 1 and report["ocr_pages"] == []


def test_ocr_unavailable_keeps_scanned_document_partial(tmp_path):
    class MissingOcr:
        def recognize_pdf_page(self, *args):
            raise OcrUnavailable("fixture")

    ingestion, _ = setup_documents(tmp_path, [], parser=DocumentParser(ocr=MissingOcr()))
    report = ingestion.ingest("outline", COURSE)
    assert report["confirmed"] == 0 and "OCR is unavailable" in report["warnings"][0]
    result = query(service(ingestion), types=("exam",))
    assert result["count"] == 0 and not result["complete"]
    assert "OCR is unavailable" in result["warnings"][0]


def test_wrong_canvas_course_url_and_nonhuman_approval_not_trusted(tmp_path):
    ingestion, _ = setup_documents(tmp_path, ["Midterm Exam: 2026-09-23"])
    path = ingestion.registry.path
    original = json.loads(path.read_text())
    for change in [{"approved_by": "codex"}, {"approved_by": "replace-with-name"},
                   {"document_kind": "unofficial_notes"}, {"source_url": "https://canvas.example/courses/999/files/99"}]:
        content = json.loads(json.dumps(original))
        content["documents"][0].update(change)
        path.write_text(json.dumps(content))
        with pytest.raises(ApplicationError):
            ingestion.registry.list_documents()
