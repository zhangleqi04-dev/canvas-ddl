"""Check the scoped remote library before querying document evidence."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from canvas_ddl.canvas.errors import ApplicationError
from .preparation import DocumentPreparationService, suggested_kind
from .models import CandidateValidation, ParsedDocument
from .inventory import CourseDocumentInventory
from .formats import document_format, canonical_extension
from .canvas_content import CanvasCourseContentInventory


class DocumentLibraryRefresher:
    def __init__(self, preparation, ingestion, *, clock):
        self.preparation, self.ingestion, self.clock = preparation, ingestion, clock
        self.client, self.registry = preparation.client, preparation.registry
        self.inventory = CourseDocumentInventory(self.client)
        self.content_inventory = CanvasCourseContentInventory(self.client)
        self.scanner = ingestion.scanner

    def refresh(self, courses, *, limit=None):
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ApplicationError("INVALID_QUERY", "A document scan limit must be positive.")
        root = self.registry.path.parent
        root.mkdir(parents=True, exist_ok=True)
        index_path, pending_path = root / "library-index.json", root / "pending-review.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf8")) if index_path.exists() else {}
            pending = json.loads(pending_path.read_text(encoding="utf8")) if pending_path.exists() else {"documents": []}
            if not isinstance(index, dict) or not isinstance(pending["documents"], list):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "The local document library index is invalid.") from None
        drafts = {r["document_id"]: r for r in pending["documents"]}
        trusted = self.registry.list_documents(tuple(c.course_id for c in courses))
        known, actions, warnings, complete = {}, [], [], True
        trusted_by_id = {doc.document_id: doc for doc in trusted}
        for doc in trusted:
            source = urlsplit(doc.source_url)
            match = re.fullmatch(r"/courses/(\d+)/files/(\d+)", source.path)
            if match and match[1] == doc.course_id and source.netloc == urlsplit(self.client.base_url).netloc:
                key = (doc.course_id, match[2])
                if key in known:
                    raise ApplicationError("INVALID_DOCUMENT_REGISTRY", "Multiple trusted records refer to the same Canvas file.")
                known[key] = doc
            elif doc.document_kind not in ("canvas_syllabus", "canvas_page"):
                complete = False
                warnings.append(f"Document {doc.document_id}: source cannot be refreshed through the Canvas file library.")
        processed, coverage = 0, []
        for course in courses:
            try:
                files, inventory_warnings = self.inventory.list(course.course_id)
                warnings.extend(inventory_warnings)
                inventory_complete = not inventory_warnings
                complete = complete and inventory_complete
            except ApplicationError as error:
                if error.code == "CANVAS_AUTH_FAILED":
                    raise
                complete = False
                warnings.append(f"{course.course_code}: file library check failed ({error.code}); latest document version is unverified.")
                coverage.append({"course_id": course.course_id, "course_code": course.course_code,
                    "state": "unavailable", "error_code": error.code,
                    "listed_document_count": None, "processed_document_count": 0,
                    "listed_pdf_count": None, "processed_pdf_count": 0})
                continue
            seen = {str(f.get("id", "")) for f in files if str(f.get("id", "")).isdecimal()}
            matched = False
            course_processed = 0
            for file in files:
                fid = str(file.get("id", ""))
                if not fid.isdecimal():
                    continue
                doc = known.get((course.course_id, fid))
                name = str(file.get("display_name") or file.get("filename") or "")
                format_name = document_format(file)
                if format_name is None:
                    continue
                kind = doc.document_kind if doc else suggested_kind(name) or "official_course_document"
                matched = True
                if limit is not None and processed >= limit:
                    complete = False
                    warnings.append("File library update limit reached; remaining candidates were not checked.")
                    break
                processed += 1
                course_processed += 1
                identifier = doc.document_id if doc else f"canvas-{course.course_id}-{fid}"
                action = {"document_id": identifier, "course_code": course.course_code, "document_name": name}
                if file.get("locked_for_user") or file.get("hidden_for_user"):
                    complete = False
                    action["state"] = "inaccessible"
                    if identifier in drafts:
                        drafts[identifier]["remote_state"] = "inaccessible"
                    warnings.append(f"{course.course_code}: document {identifier} is inaccessible; latest version unverified.")
                    actions.append(action)
                    continue
                # The authenticated course/file listing is the authority event for a
                # legacy Canvas row that still carries obsolete approval metadata.
                if doc and doc.source_authority != "canvas_api" and identifier == f"canvas-{course.course_id}-{fid}":
                    doc = self.preparation.promote_canvas_api(doc)
                    trusted_by_id[identifier] = doc
                # Missing version metadata always requires a content hash check.
                version = {k: file.get(k) for k in ("updated_at", "modified_at", "size", "uuid")}
                previous = index.get(identifier, {})
                changed_metadata = bool(previous.get("version")) and previous.get("version") != version and bool(version["updated_at"] or version["modified_at"])
                try:
                    artifact = self.ingestion.repository.load(doc) if doc else None
                    if artifact and (artifact[0].parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default
                            or any(v.get("rule_version") != CandidateValidation.__dataclass_fields__["rule_version"].default for _, v in artifact[1])):
                        artifact = None  # Re-ingest after a validation/parser rule upgrade.
                    cached_path = root / previous.get("path", "")
                    metadata_same = bool(version["updated_at"] or version["modified_at"]) and previous.get("version") == version
                    approved_same = doc and previous.get("sha256") == doc.sha256 and artifact is not None and not doc.refresh_blocked
                    pending_same = (not doc or (doc.refresh_blocked and not doc.auto_refresh)) and identifier in drafts and drafts[identifier].get("sha256") == previous.get("sha256")
                    pending_artifact = self.scanner.repository.load(self.scanner.draft(drafts[identifier])) if pending_same else None
                    if pending_artifact and (pending_artifact[0].parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default
                            or any(v.get("rule_version") != CandidateValidation.__dataclass_fields__["rule_version"].default for _, v in pending_artifact[1])):
                        pending_artifact = None
                    if metadata_same and approved_same:
                        action["state"] = "unchanged"
                    elif metadata_same and pending_same and cached_path.is_file():
                        row = dict(drafts.pop(identifier))
                        row.update(path=previous["path"], sha256=previous["sha256"])
                        doc = self.preparation.register_canvas_api(row)
                        action.update(state="ingested", source_authority="canvas_api",
                                      ingestion=self.ingestion.ingest(identifier, course))
                    else:
                        if hasattr(self.client, "download_document"):
                            content = self.client.download_document(file.get("url", ""), format_name=format_name,
                                                                    allowed_hosts=self.preparation.allowed_hosts)
                        elif format_name == "pdf":
                            content = self.client.download_pdf(file.get("url", ""), allowed_hosts=self.preparation.allowed_hosts)
                        else:
                            raise ApplicationError("DOCUMENT_FORMAT_UNSUPPORTED", "The course document transport does not support this format.")
                        digest = hashlib.sha256(content).hexdigest()
                        relative = f"downloaded/canvas-{course.course_id}-{fid}-{digest}{canonical_extension(format_name)}"
                        destination = root / relative
                        destination.parent.mkdir(exist_ok=True)
                        if destination.exists():
                            if destination.read_bytes() != content:
                                raise ValueError
                        else:
                            with destination.open("xb") as out:
                                out.write(content)
                        index[identifier] = {"version": version, "sha256": digest, "path": relative}
                        if doc:
                            if digest != doc.sha256 or doc.refresh_blocked or not doc.path.exists():
                                self._update_trusted(doc, digest=digest, path=relative, blocked=False)
                            if digest != doc.sha256 or artifact is None or doc.refresh_blocked:
                                result = self.ingestion.ingest(identifier, course)
                                action.update(state="updated" if digest != doc.sha256 else "ingested", ingestion=result)
                            else:
                                action["state"] = "unchanged"
                        else:
                            row = {"document_id": identifier, "course_id": course.course_id,
                                "course_code": course.course_code, "course_name": course.course_name,
                                "document_name": name, "document_kind": kind,
                                "source_url": f"{self.client.base_url}/courses/{course.course_id}/files/{fid}",
                                "path": relative, "sha256": digest}
                            doc = self.preparation.register_canvas_api(row)
                            drafts.pop(identifier, None)
                            action.update(state="ingested", source_authority="canvas_api",
                                          ingestion=self.ingestion.ingest(identifier, course))
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    complete = False
                    if doc and changed_metadata:
                        self._update_trusted(doc, blocked=True)
                    action.update(state="failed", error_code=error.code)
                    warnings.append(f"{course.course_code}: document update failed ({error.code}); evidence may be incomplete.")
                except (OSError, ValueError, TypeError):
                    complete = False
                    if doc and changed_metadata:
                        self._update_trusted(doc, blocked=True)
                    action.update(state="failed", error_code="DOCUMENT_STORE_UNAVAILABLE")
                    warnings.append(f"{course.course_code}: document update failed; evidence may be incomplete.")
                actions.append(action)
            for (cid, fid), doc in known.items():
                if cid == course.course_id and fid not in seen and inventory_complete:
                    self._update_trusted(doc, blocked=True)
                    complete = False
                    actions.append({"document_id": doc.document_id, "course_code": course.course_code, "state": "missing"})
                    warnings.append(f"{course.course_code}: trusted document {doc.document_id} disappeared from the file library; previous deadlines withheld.")
            for identifier, row in drafts.items():
                match = re.fullmatch(r"/courses/(\d+)/files/(\d+)", urlsplit(row.get("source_url", "")).path)
                if str(row.get("course_id")) == course.course_id and match and match[2] not in seen:
                    row["remote_state"] = "missing" if inventory_complete else "unverified"
            coverage.append({"course_id": course.course_id, "course_code": course.course_code,
                "state": "available" if inventory_complete else "module_fallback_partial",
                "listed_document_count": len(files), "processed_document_count": course_processed,
                "unprocessed_document_count": len(files) - course_processed,
                "listed_pdf_count": len(files), "processed_pdf_count": course_processed,
                "unprocessed_pdf_count": len(files) - course_processed})
            try:
                content_items, content_warnings = self.content_inventory.list(course.course_id)
                warnings.extend(content_warnings)
                content_complete = not content_warnings
                complete = complete and content_complete
                content_actions = self._refresh_canvas_content(course, content_items, trusted_by_id,
                                                               drafts, index, root)
                actions.extend(content_actions)
                seen_content = {item["document_id"] for item in content_items}
                if content_complete:
                    for document in trusted:
                        if (document.course_id == course.course_id
                                and document.document_kind in ("canvas_syllabus", "canvas_page")
                                and document.document_id not in seen_content):
                            self._update_trusted(document, blocked=True)
                            complete = False
                            actions.append({"document_id": document.document_id, "course_code": course.course_code,
                                            "document_name": document.document_name, "state": "missing"})
                            warnings.append(f"{course.course_code}: trusted Canvas course content {document.document_id} disappeared; previous deadlines withheld.")
                    for identifier, row in drafts.items():
                        if (str(row.get("course_id")) == course.course_id
                                and row.get("document_kind") in ("canvas_syllabus", "canvas_page")
                                and identifier not in seen_content):
                            row["remote_state"] = "missing"
                coverage.append({"course_id": course.course_id, "course_code": course.course_code,
                    "state": "available" if content_complete else "partial", "source_type": "canvas_course_content",
                    "listed_document_count": len(content_items), "processed_document_count": len(content_items),
                    "unprocessed_document_count": 0})
            except ApplicationError as error:
                if error.code == "CANVAS_AUTH_FAILED":
                    raise
                complete = False
                warnings.append(f"{course.course_code}: Canvas Syllabus/Page refresh failed ({error.code}).")
                coverage.append({"course_id": course.course_id, "course_code": course.course_code,
                    "state": "unavailable", "source_type": "canvas_course_content", "error_code": error.code,
                    "listed_document_count": None, "processed_document_count": 0})
        DocumentPreparationService._write(index_path, index)
        DocumentPreparationService._write(pending_path, {"documents": list(drafts.values())})
        return {"state": "checked", "checked_at": self.clock().isoformat(), "complete": complete,
                "scope": "all_course_documents", "course_ids": [c.course_id for c in courses], "coverage": coverage,
                "actions": actions, "warnings": warnings}

    def _refresh_canvas_content(self, course, items, trusted_by_id, drafts, index, root):
        actions = []
        for item in items:
            identifier, digest = item["document_id"], item["sha256"]
            document = trusted_by_id.get(identifier)
            relative = f"downloaded/{identifier}-{digest}.html"
            destination = root / relative
            destination.parent.mkdir(exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != item["content"]:
                    raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "A cached Canvas course-content version is inconsistent.")
            else:
                with destination.open("xb") as stream:
                    stream.write(item["content"])
            previous = index.get(identifier, {})
            index[identifier] = {"version": item["version"], "sha256": digest, "path": relative}
            artifact = self.ingestion.repository.load(document) if document else None
            if artifact and (artifact[0].parser_version != ParsedDocument.__dataclass_fields__["parser_version"].default
                    or any(v.get("rule_version") != CandidateValidation.__dataclass_fields__["rule_version"].default
                           for _, v in artifact[1])):
                artifact = None
            action = {"document_id": identifier, "course_code": course.course_code,
                      "document_name": item["document_name"]}
            if document and digest == document.sha256 and artifact is not None and not document.refresh_blocked:
                action["state"] = "unchanged"
            elif document:
                self._update_trusted(document, digest=digest, path=relative, blocked=False)
                action.update(state="updated", ingestion=self.ingestion.ingest(identifier, course))
            else:
                row = {"document_id": identifier, "course_id": course.course_id,
                    "course_code": course.course_code, "course_name": course.course_name,
                    "document_name": item["document_name"], "document_kind": item["document_kind"],
                    "source_url": item["source_url"], "path": relative, "sha256": digest,
                    }
                document = self.preparation.register_canvas_api(row)
                drafts.pop(identifier, None)
                action.update(state="ingested", source_authority="canvas_api",
                              ingestion=self.ingestion.ingest(identifier, course))
            actions.append(action)
        return actions

    def _update_trusted(self, document, *, digest=None, path=None, blocked):
        data = json.loads(self.registry.path.read_text(encoding="utf-8-sig"))
        row = next(r for r in data["documents"] if r["document_id"] == document.document_id)
        if digest and digest != row["sha256"]:
            row.setdefault("version_history", []).append({"sha256": row["sha256"], "path": row["path"],
                                                         "replaced_at": self.clock().isoformat()})
            row["sha256"] = digest
            row["version_basis"] = ("canvas_api_course_scope" if row.get("source_authority") == "canvas_api"
                                    else "same_canvas_file_refresh")
        if path:
            row["path"] = path
        row["refresh_blocked"] = blocked
        DocumentPreparationService._write(self.registry.path, data)
