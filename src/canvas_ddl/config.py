from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from collections.abc import Mapping
import os
import re
from dotenv import dotenv_values
from .canvas.errors import ApplicationError


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str = field(repr=False)
    timezone: str = "Asia/Singapore"
    course_ids: tuple[str, ...] = ()
    exam_keywords: tuple[str, ...] = ()
    document_registry: Path | None = None
    document_store: Path | None = None
    document_hosts: tuple[str, ...] = ()
    ocr_enabled: bool = True
    ocr_device: str = "cpu"
    ocr_dpi: int = 150
    ocr_min_confidence: float = 0.90


def _boolean(value, default):
    if value is None or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ApplicationError("INVALID_CONFIG", "CANVAS_OCR_ENABLED must be true or false.")


def load_config(env_file: str | Path | None = None, *, environ: Mapping[str, str] | None = None) -> Config:
    env = dict(dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")) if env_file else {}
    env.update(os.environ if environ is None else environ)
    base = (env.get("CANVAS_BASE_URL") or "").strip().rstrip("/")
    token = (env.get("CANVAS_TOKEN") or env.get("CANVAS_API_TOKEN") or "").strip()
    try:
        url = urlsplit(base)
        valid = url.scheme == "https" and url.hostname and url.path == "" and not any((url.username, url.password, url.query, url.fragment))
        _ = url.port
    except ValueError:
        valid = False
    if not valid or not token or any(c in token for c in "\r\n"):
        raise ApplicationError("INVALID_CONFIG", "Configure an HTTPS Canvas origin and a local Canvas token in .env.")
    course_ids = tuple(v.strip() for v in (env.get("CANVAS_COURSE_IDS") or "").split(",") if v.strip())
    if any(not value.isdecimal() for value in course_ids):
        raise ApplicationError("INVALID_CONFIG", "CANVAS_COURSE_IDS must contain numeric Canvas course IDs.")
    root = Path(env_file).resolve().parent if env_file else Path.cwd()
    try:
        ocr_enabled = _boolean(env.get("CANVAS_OCR_ENABLED"), True)
        ocr_device = (env.get("CANVAS_OCR_DEVICE") or "cpu").strip().casefold()
        if not (ocr_device == "cpu" or re.fullmatch(r"gpu:\d+", ocr_device)):
            raise ValueError
        ocr_dpi = int(env.get("CANVAS_OCR_DPI") or 150)
        ocr_min_confidence = float(env.get("CANVAS_OCR_MIN_CONFIDENCE") or 0.90)
        if not 96 <= ocr_dpi <= 300 or not 0.5 <= ocr_min_confidence <= 1:
            raise ValueError
    except ValueError:
        raise ApplicationError("INVALID_CONFIG", "OCR settings are invalid; use cpu/gpu:N, DPI 96-300 and confidence 0.5-1.0.") from None
    return Config(base, token, (env.get("CANVAS_TIMEZONE") or "Asia/Singapore").strip(), course_ids,
                  tuple(v.strip() for v in (env.get("CANVAS_EXAM_KEYWORDS") or "").split(",") if v.strip()),
                  (root / (env.get("CANVAS_DOCUMENT_REGISTRY") or "documents/registry.json")).resolve(),
                  (root / (env.get("CANVAS_DOCUMENT_STORE") or "data/documents.sqlite3")).resolve(),
                  tuple(v.strip().casefold() for v in (env.get("CANVAS_DOCUMENT_HOSTS") or "").split(",") if v.strip()),
                  ocr_enabled, ocr_device, ocr_dpi, ocr_min_confidence)
