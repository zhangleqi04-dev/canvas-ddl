"""Read-only inventory of official Canvas Syllabus and course Page HTML."""
import hashlib
from urllib.parse import quote

from canvas_ddl.canvas.errors import ApplicationError


class CanvasCourseContentInventory:
    MAX_ITEMS = 500
    MAX_ITEM_BYTES = 2 * 1024 * 1024
    MAX_TOTAL_BYTES = 20 * 1024 * 1024

    def __init__(self, client):
        self.client = client

    def list(self, course_id):
        # Small injected test/adapter clients may expose only the file-library
        # interface. CanvasClient always exposes both methods.
        if not hasattr(self.client, "get") or not hasattr(self.client, "get_paginated"):
            return [], []
        items, warnings, total = [], [], 0
        try:
            course = self.client.get(f"/api/v1/courses/{course_id}", params={"include[]": "syllabus_body"})
            body = course.get("syllabus_body") if isinstance(course, dict) else None
            if isinstance(body, str) and body.strip():
                payload = body.encode("utf-8")
                if len(payload) > self.MAX_ITEM_BYTES:
                    raise ValueError
                items.append(self._item(course_id, "syllabus", "Canvas Syllabus.html", "canvas_syllabus",
                                        f"{self.client.base_url}/courses/{course_id}/assignments/syllabus",
                                        payload, course.get("updated_at")))
                total += len(payload)
        except ApplicationError as error:
            if error.code == "CANVAS_AUTH_FAILED":
                raise
            warnings.append(f"Course {course_id}: Canvas Syllabus HTML unavailable ({error.code}).")
        except (TypeError, ValueError):
            warnings.append(f"Course {course_id}: Canvas Syllabus HTML exceeded safety limits.")

        try:
            pages = self.client.get_paginated(f"/api/v1/courses/{course_id}/pages")
        except ApplicationError as error:
            if error.code == "CANVAS_AUTH_FAILED":
                raise
            if error.code == "SOURCE_UNAVAILABLE":
                pages = []
            else:
                warnings.append(f"Course {course_id}: Canvas Pages inventory unavailable ({error.code}).")
                pages = []
        if len(pages) > self.MAX_ITEMS:
            warnings.append(f"Course {course_id}: Canvas Pages exceeded the bounded inventory limit.")
            pages = pages[:self.MAX_ITEMS]
        for row in pages:
            slug = str(row.get("url") or "")
            if not slug or row.get("published") is False:
                continue
            try:
                page = self.client.get(f"/api/v1/courses/{course_id}/pages/{quote(slug, safe='')}")
                body = page.get("body") if isinstance(page, dict) else None
                if not isinstance(body, str) or not body.strip():
                    continue
                payload = body.encode("utf-8")
                if len(payload) > self.MAX_ITEM_BYTES or total + len(payload) > self.MAX_TOTAL_BYTES:
                    raise ValueError
                title = str(page.get("title") or row.get("title") or slug)
                safe_name = title[:160].replace("/", "_").replace("\\", "_") + ".html"
                source_url = f"{self.client.base_url}/courses/{course_id}/pages/{quote(slug, safe='')}"
                items.append(self._item(course_id, "page:" + slug, safe_name, "canvas_page",
                                        source_url, payload, page.get("updated_at") or row.get("updated_at")))
                total += len(payload)
            except ApplicationError as error:
                if error.code == "CANVAS_AUTH_FAILED":
                    raise
                warnings.append(f"Course {course_id}: a Canvas Page is inaccessible ({error.code}).")
            except (TypeError, ValueError):
                warnings.append(f"Course {course_id}: a Canvas Page exceeded safety limits.")
        return items, warnings

    @staticmethod
    def _item(course_id, source_key, name, kind, source_url, content, updated_at):
        digest = hashlib.sha256(content).hexdigest()
        opaque = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
        return {"document_id": f"canvas-{course_id}-content-{opaque}", "document_name": name,
                "document_kind": kind, "source_url": source_url, "content": content,
                "sha256": digest, "version": {"updated_at": updated_at, "sha256": digest}}
