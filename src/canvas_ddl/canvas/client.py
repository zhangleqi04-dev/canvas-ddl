"""GET-only Canvas transport. Refuse redirects and foreign pagination links."""
import re
import time
from urllib.parse import urlsplit, urljoin
from collections.abc import Callable
from typing import Any
import httpx
from .auth import bearer_headers
from .errors import ApplicationError


class CanvasClient:
    def __init__(self, base_url: str, token: str, *, transport=None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 15):
        self.base_url = base_url.rstrip("/")
        self._origin = self._url_origin(self.base_url)
        if urlsplit(self.base_url).scheme != "https":
            raise ApplicationError("INVALID_CONFIG", "Canvas must use HTTPS.")
        self._headers = bearer_headers(token)
        self._sleep = sleep
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False,
                                 limits=httpx.Limits(max_connections=8, max_keepalive_connections=8))

    @staticmethod
    def _url_origin(url: str):
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    def _safe_url(self, path: str) -> str:
        try:
            url = urljoin(self.base_url + "/", path)
            parts = urlsplit(url)
            if self._url_origin(url) != self._origin or parts.username or parts.password or parts.fragment or not parts.path.startswith("/api/v1/"):
                raise ValueError
        except ValueError:
            raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned an unsafe API or pagination URL.") from None
        return url

    def _request(self, path: str, params=None) -> tuple[Any, str | None]:
        url = self._safe_url(path)
        for attempt in range(3):
            try:
                response = self._http.get(url, params=params, headers=self._headers)
            except (httpx.HTTPError, ValueError):
                if attempt < 2:
                    self._sleep(0.25 * (attempt + 1))
                    continue
                raise ApplicationError("CANVAS_UNAVAILABLE", "Current Canvas data could not be verified.") from None
            status = response.status_code
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                retry = response.headers.get("Retry-After", "0.5")
                try:
                    delay = min(2.0, max(0.0, float(retry)))
                except ValueError:
                    delay = 0.5
                self._sleep(delay)
                continue
            if status == 401:
                raise ApplicationError("CANVAS_AUTH_FAILED", "Canvas authentication failed.")
            if status == 403:
                raise ApplicationError("PERMISSION_DENIED", "The Canvas source is inaccessible.")
            if status == 404:
                raise ApplicationError("SOURCE_UNAVAILABLE", "This Canvas source is not available.")
            if not 200 <= status < 300:
                raise ApplicationError("CANVAS_UNAVAILABLE", "Current Canvas data could not be verified.")
            if len(response.content) > 8 * 1024 * 1024:
                raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas response exceeded the safety limit.")
            try:
                data = response.json()
            except ValueError:
                raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned invalid JSON.") from None
            # Do not rebuild server-issued pagination queries or expose links in errors.
            links = response.headers.get("Link", "")
            matches = re.findall(r'<([^>]+)>\s*;\s*rel="?next"?', links)
            return data, matches[0] if matches else None
        raise ApplicationError("CANVAS_UNAVAILABLE", "Current Canvas data could not be verified.")

    def get(self, path: str, *, params=None) -> dict | list:
        data, _ = self._request(path, params)
        if not isinstance(data, (dict, list)):
            raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned an unexpected response shape.")
        return data

    def get_paginated(self, path: str, *, params=None) -> list[dict]:
        query = dict(params or {})
        query.setdefault("per_page", 100)
        rows, visited = [], set()
        current = path
        for _ in range(1000):
            url = self._safe_url(current)
            if url in visited:
                raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas pagination did not complete.")
            visited.add(url)
            data, following = self._request(current, query)
            if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
                raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas returned an unexpected list shape.")
            rows.extend(data)
            if len(rows) > 100000:
                break
            if not following:
                return rows
            current, query = following, None
        raise ApplicationError("CANVAS_UNAVAILABLE", "Canvas pagination exceeded the safety limit.")

    def close(self):
        self._http.close()

    def download_pdf(self, url: str, *, allowed_hosts=()) -> bytes:
        return self.download_document(url, format_name="pdf", allowed_hosts=allowed_hosts)

    def download_document(self, url: str, *, format_name: str, allowed_hosts=()) -> bytes:
        """Bounded maintenance download; never forward Canvas auth off-origin."""
        if format_name not in {"pdf", "docx", "pptx", "xlsx", "csv", "text", "markdown", "rtf", "html", "image"}:
            raise ApplicationError("DOCUMENT_FORMAT_UNSUPPORTED", "The course document format is not supported.")
        allowed = {urlsplit(self.base_url).hostname, *allowed_hosts}
        try:
            for _ in range(6):
                parts = urlsplit(url)
                # Published Canvas file-storage hosts are transport destinations,
                # not evidence authority. No bearer token is sent to them.
                host = parts.hostname or ""
                canvas_storage = bool(re.fullmatch(r"(?:a\d+-\d+\.)?cluster\d+\.canvas-user-content\.com", host))
                canvas_storage = canvas_storage or host.endswith(".inscloudgate.net") or host in {
                    f"instructure-uploads{region}.s3.amazonaws.com" for region in
                    ("", "-2", "-eu", "-apse1", "-apse2", "-fra", "-pdx", "-yul")}
                if (parts.scheme != "https" or not (host in allowed or canvas_storage) or parts.username
                        or parts.password or parts.fragment or parts.port not in (None, 443)):
                    raise ApplicationError("DOCUMENT_DOWNLOAD_HOST_UNAPPROVED", "The PDF download host requires explicit configuration approval.",
                                           ({"host": parts.hostname, "scheme": parts.scheme},))
                headers = self._headers if self._url_origin(url) == self._origin else {}
                with self._http.stream("GET", url, headers=headers) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError
                        url = urljoin(url, location)
                        continue
                    if response.status_code == 401 and headers:
                        raise ApplicationError("CANVAS_AUTH_FAILED", "Canvas authentication failed.")
                    if response.status_code != 200:
                        raise ValueError
                    if int(response.headers.get("Content-Length", "0")) > 30 * 1024 * 1024:
                        raise ValueError
                    chunks, size = [], 0
                    for chunk in response.iter_bytes(chunk_size=65536):
                        size += len(chunk)
                        if size > 30 * 1024 * 1024:
                            raise ValueError
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not self._valid_document_bytes(content, format_name):
                        raise ValueError
                    return content
        except ApplicationError:
            raise
        except (httpx.HTTPError, ValueError):
            pass
        raise ApplicationError("DOCUMENT_DOWNLOAD_FAILED", "The course document could not be downloaded safely.")

    @staticmethod
    def _valid_document_bytes(content: bytes, format_name: str) -> bool:
        if not content:
            return False
        if format_name == "pdf":
            return content.startswith(b"%PDF-")
        if format_name in ("docx", "pptx", "xlsx"):
            import zipfile
            from io import BytesIO
            required = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml",
                        "xlsx": "xl/workbook.xml"}[format_name]
            try:
                with zipfile.ZipFile(BytesIO(content)) as archive:
                    return required in archive.namelist() and len(archive.namelist()) <= 10000
            except (zipfile.BadZipFile, OSError):
                return False
        if format_name == "image":
            try:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(content)) as image:
                    image.verify()
                return True
            except Exception:
                return False
        if b"\x00" in content[:4096]:
            return False
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return format_name == "rtf" and content.lstrip().startswith(b"{\\rtf")
        if format_name == "html":
            return "<" in text and ">" in text
        if format_name == "rtf":
            return text.lstrip().startswith("{\\rtf")
        return True

    def download_calendar_feed(self, url: str) -> bytes:
        """Read a same-origin Canvas course ICS feed without exposing its secret URL."""
        try:
            parts = urlsplit(url)
            if (self._url_origin(url) != self._origin or parts.username or parts.password
                    or parts.fragment or not re.fullmatch(r"/feeds/calendars/course_[A-Za-z0-9_-]+\.ics", parts.path)):
                raise ValueError
            with self._http.stream("GET", url, headers={}) as response:
                if response.status_code != 200 or int(response.headers.get("Content-Length", "0")) > 2 * 1024 * 1024:
                    raise ValueError
                chunks, size = [], 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise ValueError
                    chunks.append(chunk)
                content = b"".join(chunks)
            if b"BEGIN:VCALENDAR" not in content[:4096]:
                raise ValueError
            return content
        except (httpx.HTTPError, ValueError):
            raise ApplicationError("CANVAS_CALENDAR_FEED_UNAVAILABLE", "The Canvas course calendar feed could not be verified.") from None
