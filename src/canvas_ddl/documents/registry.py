"""Trusted document registry: authenticated Canvas identity or explicit operator trust."""
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from canvas_ddl.canvas.errors import ApplicationError
from .formats import document_format
from .models import OfficialDocument

KINDS = ("syllabus", "course_outline", "course_handout", "official_course_pdf",
         "official_course_document", "canvas_syllabus", "canvas_page")


class OfficialDocumentRegistry:
    def __init__(self, path: Path, *, canvas_origin: str, official_hosts: tuple[str, ...] = ()):
        self.path = Path(path)
        self.canvas_origin = canvas_origin.rstrip("/")
        self.canvas_host = urlsplit(canvas_origin).hostname
        self.hosts = {urlsplit(canvas_origin).hostname, *official_hosts}

    def list_documents(self, course_ids=None) -> tuple[OfficialDocument, ...]:
        if not self.path.exists():
            return ()
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8-sig"))["documents"]
            result, seen = [], set()
            for row in rows:
                if row.get("active", True) is False:
                    continue
                identifier = row["document_id"]
                source = urlsplit(row["source_url"])
                authority = row.get("source_authority", "operator")
                approver = str(row.get("approved_by") or "").strip()
                course_link = re.search(r"/courses/(\d+)(?:/|$)", source.path)
                file_link = re.fullmatch(r"/courses/(\d+)/files/(\d+)", source.path)
                content_link = (row["document_kind"] == "canvas_syllabus"
                                and source.path == f"/courses/{row['course_id']}/assignments/syllabus") or (
                                row["document_kind"] == "canvas_page"
                                and source.path.startswith(f"/courses/{row['course_id']}/pages/"))
                canvas_native = (authority == "canvas_api" and source.hostname == self.canvas_host
                                 and ((file_link and file_link[1] == str(row["course_id"])
                                       and identifier == f"canvas-{row['course_id']}-{file_link[2]}")
                                      or content_link))
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", identifier) or identifier in seen:
                    raise ValueError
                seen.add(identifier)
                if (source.scheme != "https" or source.hostname not in self.hosts or source.username or source.password
                        or source.query or source.fragment or not source.path or not str(row["course_id"]).isdecimal()
                        or row["document_kind"] not in KINDS or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
                        or authority not in ("operator", "canvas_api")
                        or (authority == "operator" and (not approver
                            or approver.casefold() in ("codex", "llm", "extractor", "auto", "canvas-api")
                            or approver.casefold().startswith(("replace", "placeholder"))))
                        or (authority == "canvas_api" and (not canvas_native or approver))
                        or (course_link and course_link[1] != str(row["course_id"]))
                        or not row["course_code"].strip() or not row["document_name"].strip()
                        or type(row.get("auto_refresh", False)) is not bool
                        or type(row.get("refresh_blocked", False)) is not bool):
                    raise ValueError
                first, last = date.fromisoformat(row["valid_from"]), date.fromisoformat(row["valid_until"])
                if first > last:
                    raise ValueError
                local = (self.path.parent / row["path"]).resolve()
                # Canvas may report a supported MIME type while its display name has no
                # extension.  The downloader has already selected and persisted the
                # canonical local extension from authenticated API metadata.
                if ((authority == "operator" and document_format(row["document_name"]) is None)
                        or document_format(local.name) is None):
                    raise ValueError
                result.append(OfficialDocument(identifier, str(row["course_id"]), row["course_code"], row["course_name"],
                                               row["document_name"], row["document_kind"], row["source_url"], local,
                                               row["sha256"], approver, first, last,
                                               row.get("auto_refresh", False), row.get("refresh_blocked", False), authority))
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            raise ApplicationError("INVALID_DOCUMENT_REGISTRY", "The trusted course-document registry is invalid.") from None
        return tuple(d for d in result if course_ids is None or d.course_id in course_ids)

    def get(self, document_id):
        match = [d for d in self.list_documents() if d.document_id == document_id]
        if not match:
            raise ApplicationError("DOCUMENT_NOT_REGISTERED", "Only trusted Canvas-native or operator-approved course documents may be ingested.")
        return match[0]
