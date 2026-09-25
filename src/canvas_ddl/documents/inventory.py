"""Course document inventory, including module files when Files is restricted."""
from canvas_ddl.canvas.errors import ApplicationError
from .formats import document_format


def is_supported_document(file):
    return document_format(file) is not None


def is_pdf(file):
    """Compatibility predicate retained for callers that explicitly need PDFs."""
    return document_format(file) == "pdf"


class CourseDocumentInventory:
    def __init__(self, client):
        self.client = client

    def list(self, course_id):
        original_error = None
        try:
            # Enumerate metadata before local MIME/extension checks: documents
            # uploaded as application/octet-stream still need extension detection.
            files = self.client.get_paginated(f"/api/v1/courses/{course_id}/files")
            return [file for file in files if is_supported_document(file)], []
        except ApplicationError as error:
            if error.code not in ("PERMISSION_DENIED", "SOURCE_UNAVAILABLE"):
                raise
            original_error = error
        warnings = [f"Course {course_id}: Files inventory unavailable; scanned accessible module-linked supported documents only."]
        try:
            modules = self.client.get_paginated(f"/api/v1/courses/{course_id}/modules", params={"include[]": "items"})
        except ApplicationError as error:
            if error.code == "CANVAS_AUTH_FAILED":
                raise
            raise original_error from None
        ids, files = set(), []
        for module in modules:
            mid = str(module.get("id", ""))
            if not mid.isdecimal():
                continue
            items = module.get("items") or []
            if module.get("items_count", len(items)) > len(items):
                items = self.client.get_paginated(f"/api/v1/courses/{course_id}/modules/{mid}/items")
            for item in items:
                fid = str(item.get("content_id", ""))
                if item.get("type") != "File" or not fid.isdecimal() or fid in ids:
                    continue
                ids.add(fid)
                try:
                    file = self.client.get(f"/api/v1/courses/{course_id}/files/{fid}")
                    if not isinstance(file, dict) or str(file.get("id")) != fid:
                        raise ApplicationError("CANVAS_UNAVAILABLE", "Unexpected course file metadata.")
                    if is_supported_document(file):
                        files.append(file)
                except ApplicationError as error:
                    if error.code == "CANVAS_AUTH_FAILED":
                        raise
                    warnings.append(f"Course {course_id}: module-linked file {fid} is inaccessible ({error.code}).")
        return files, warnings


class CoursePdfInventory(CourseDocumentInventory):
    """Compatibility inventory for explicit legacy PDF-only callers."""
    def list(self, course_id):
        files, warnings = super().list(course_id)
        return [file for file in files if is_pdf(file)], warnings
