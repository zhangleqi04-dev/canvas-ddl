"""Download course-scoped documents; authenticated Canvas course sources are trusted."""
import hashlib
import json
import re
from datetime import date
from canvas_ddl.canvas.errors import ApplicationError
from .formats import document_format, canonical_extension


def suggested_kind(name):
    for pattern, kind in ((r"syllabus|教学大纲", "syllabus"),
                          (r"(?:course[ _-]*)?outline|课程大纲", "course_outline"),
                          (r"(?:course[ _-]*)?handout|课程手册", "course_handout")):
        if re.search(pattern, name, re.I):
            return kind
    return None


class DocumentPreparationService:
    def __init__(self, client, registry, *, allowed_hosts=()):
        self.client, self.registry = client, registry
        self.allowed_hosts = allowed_hosts

    def prepare(self, courses, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ApplicationError("INVALID_QUERY", "Document preparation limit must be between 1 and 20.")
        root = self.registry.path.parent
        root.mkdir(parents=True, exist_ok=True)
        pending_path = root / "pending-review.json"
        # Existing pending entries are retained across course-scoped preparations.
        try:
            pending = json.loads(pending_path.read_text(encoding="utf-8"))["documents"] if pending_path.exists() else []
            by_id = {row["document_id"]: row for row in pending}
        except (ValueError, TypeError, KeyError, OSError):
            raise ApplicationError("INVALID_DOCUMENT_DRAFT", "The pending document review file is invalid.") from None
        results, warnings = [], []
        for course in courses:
            try:
                files = self.client.get_paginated(f"/api/v1/courses/{course.course_id}/files")
            except ApplicationError as error:
                if error.code == "CANVAS_AUTH_FAILED":
                    raise
                warnings.append({"course_code": course.course_code, "error_code": error.code})
                continue
            matches = 0
            for file in files:
                name = str(file.get("display_name") or file.get("filename") or "")
                kind = suggested_kind(name)
                format_name = document_format(file)
                file_id = str(file.get("id", ""))
                if not kind or format_name is None or not file_id.isdecimal():
                    continue
                if file.get("locked_for_user") or file.get("hidden_for_user"):
                    continue
                matches += 1
                if len(results) >= limit:
                    warnings.append({"error_code": "DOCUMENT_PREPARATION_LIMIT", "message": "Additional matching files were not downloaded."})
                    break
                identifier = f"canvas-{course.course_id}-{file_id}"
                result = {"document_id": identifier, "course_code": course.course_code, "document_name": name}
                try:
                    if hasattr(self.client, "download_document"):
                        content = self.client.download_document(file.get("url", ""), format_name=format_name,
                                                                allowed_hosts=self.allowed_hosts)
                    elif format_name == "pdf":
                        content = self.client.download_pdf(file.get("url", ""), allowed_hosts=self.allowed_hosts)
                    else:
                        raise ApplicationError("DOCUMENT_FORMAT_UNSUPPORTED", "The course document transport does not support this format.")
                    digest = hashlib.sha256(content).hexdigest()
                    # Canvas filenames never become local paths. Hash versions are immutable.
                    relative = f"downloaded/{identifier}-{digest}{canonical_extension(format_name)}"
                    destination = root / relative
                    destination.parent.mkdir(exist_ok=True)
                    if destination.exists():
                        if destination.read_bytes() != content:
                            raise ValueError
                    else:
                        with destination.open("xb") as out:
                            out.write(content)
                    row = {"document_id": identifier, "course_id": course.course_id,
                           "course_code": course.course_code, "course_name": course.course_name,
                           "document_name": name, "document_kind": kind,
                           "source_url": f"{self.client.base_url}/courses/{course.course_id}/files/{file_id}",
                           "path": relative, "sha256": digest}
                    self.register_canvas_api(row)
                    by_id.pop(identifier, None)
                    result.update(state="registered", path=str(destination), suggested_kind=kind, sha256=digest,
                                  source_authority="canvas_api")
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    result.update(state="download_failed", error_code=error.code)
                    if error.code == "DOCUMENT_DOWNLOAD_HOST_UNAPPROVED" and error.candidates:
                        result["download_host_review"] = error.candidates[0]
                except (OSError, ValueError, TypeError):
                    result.update(state="download_failed", error_code="DOCUMENT_DOWNLOAD_FAILED")
                results.append(result)
            if not matches:
                warnings.append({"course_code": course.course_code, "error_code": "NO_HINTED_DOCUMENT_CANDIDATES",
                                 "message": "No filename-matched supported document candidates were found; document coverage is not established."})
        self._write(pending_path, {"documents": list(by_id.values())})
        return {"status": "partial" if warnings or any(r["state"] != "registered" for r in results) else "ok",
                "requires_review": False, "documents": results, "warnings": warnings,
                "review_file": str(pending_path)}

    def register_canvas_api(self, row):
        """Register an authenticated, course-scoped Canvas API document without human approval."""
        record = dict(row)
        record.update(approved_by="", source_authority="canvas_api", valid_from="0001-01-01",
                      valid_until="9999-12-31", active=True, auto_refresh=True, refresh_blocked=False)
        try:
            data = json.loads(self.registry.path.read_text(encoding="utf-8-sig")) if self.registry.path.exists() else {"documents": []}
            data["documents"] = [item for item in data["documents"] if item["document_id"] != record["document_id"]] + [record]
            temporary = self.registry.path.with_suffix(".canvas-api.tmp")
            try:
                self._write(temporary, data)
                from .registry import OfficialDocumentRegistry
                check = OfficialDocumentRegistry(temporary, canvas_origin=self.client.base_url,
                                                 official_hosts=tuple(self.registry.hosts - {self.registry.canvas_host}))
                check.get(record["document_id"])
                temporary.replace(self.registry.path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.registry.get(record["document_id"])
        except ApplicationError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise ApplicationError("DOCUMENT_REVIEW_FAILED", "Canvas course document registration failed.") from None

    def promote_canvas_api(self, document):
        """Migrate a legacy approved Canvas row after its scoped API identity is observed."""
        try:
            relative = document.path.resolve().relative_to(self.registry.path.parent.resolve()).as_posix()
        except ValueError:
            raise ApplicationError("INVALID_DOCUMENT_REGISTRY", "A Canvas document path is outside the document store.") from None
        return self.register_canvas_api({
            "document_id": document.document_id,
            "course_id": document.course_id,
            "course_code": document.course_code,
            "course_name": document.course_name,
            "document_name": document.document_name,
            "document_kind": document.document_kind,
            "source_url": document.source_url,
            "path": relative,
            "sha256": document.sha256,
        })

    def approve(self, document_id, *, approved_by, valid_from, valid_until):
        """Retained for non-Canvas external sources that require explicit operator trust."""
        try:
            first, last = date.fromisoformat(valid_from), date.fromisoformat(valid_until)
            if first > last:
                raise ValueError
            pending = json.loads((self.registry.path.parent / "pending-review.json").read_text(encoding="utf-8"))["documents"]
            row = next(dict(r) for r in pending if r["document_id"] == document_id)
            content = (self.registry.path.parent / row["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != row["sha256"]:
                raise ValueError
            row.update(approved_by=approved_by, valid_from=valid_from, valid_until=valid_until, active=True, refresh_blocked=False)
            data = json.loads(self.registry.path.read_text(encoding="utf-8-sig")) if self.registry.path.exists() else {"documents": []}
            data["documents"] = [r for r in data["documents"] if r["document_id"] != document_id] + [row]
            # Validate proposed registry before replacing the current authority configuration.
            from .registry import OfficialDocumentRegistry
            temporary = self.registry.path.with_suffix(".review.tmp")
            try:
                self._write(temporary, data)
                check = OfficialDocumentRegistry(temporary, canvas_origin=self.client.base_url,
                                                 official_hosts=tuple(self.registry.hosts))
                check.get(document_id)
                temporary.replace(self.registry.path)
            finally:
                temporary.unlink(missing_ok=True)
        except ApplicationError:
            raise
        except (OSError, ValueError, TypeError, KeyError, StopIteration):
            raise ApplicationError("DOCUMENT_REVIEW_FAILED", "Document approval requires an unchanged prepared course document, a human approver and a valid course period.") from None

    @staticmethod
    def _write(path, data):
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
