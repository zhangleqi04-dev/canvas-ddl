"""Supported course-document formats and deterministic metadata classification."""
from pathlib import PurePath


FORMAT_EXTENSIONS = {
    "pdf": ".pdf",
    "docx": ".docx",
    "pptx": ".pptx",
    "xlsx": ".xlsx",
    "csv": ".csv",
    "text": ".txt",
    "markdown": ".md",
    "rtf": ".rtf",
    "html": ".html",
    "image": ".png",
}

EXTENSION_FORMATS = {
    ".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".xlsx": "xlsx",
    ".csv": "csv", ".txt": "text", ".text": "text", ".md": "markdown",
    ".markdown": "markdown", ".rtf": "rtf", ".html": "html", ".htm": "html",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".tif": "image", ".tiff": "image", ".bmp": "image",
}

MIME_FORMATS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv", "application/csv": "csv",
    "text/plain": "text", "text/markdown": "markdown", "text/rtf": "rtf",
    "application/rtf": "rtf", "text/html": "html", "application/xhtml+xml": "html",
    "image/png": "image", "image/jpeg": "image", "image/webp": "image",
    "image/tiff": "image", "image/bmp": "image",
}


def document_format(file_or_name, content_type=None):
    """Return a supported normalized format from Canvas metadata or a filename."""
    if isinstance(file_or_name, dict):
        file = file_or_name
        name = str(file.get("display_name") or file.get("filename") or "")
        mime = str(file.get("content-type") or file.get("content_type") or "")
    else:
        name, mime = str(file_or_name or ""), str(content_type or "")
    mime = mime.split(";", 1)[0].strip().casefold()
    extension_format = EXTENSION_FORMATS.get(PurePath(name).suffix.casefold())
    if extension_format is not None:
        return extension_format
    if mime in MIME_FORMATS:
        return MIME_FORMATS[mime]
    return None


def canonical_extension(format_name):
    return FORMAT_EXTENSIONS[format_name]
