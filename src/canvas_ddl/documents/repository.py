"""Durable ingestion artifacts; query paths never open the original document."""
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from canvas_ddl.canvas.errors import ApplicationError
from .models import ParsedDocument, DocumentPage, DeadlineCandidate


class DocumentRepository:
    def __init__(self, path):
        self.path = Path(path).resolve()

    def save(self, parsed, candidates, validations, course_id):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, course_id TEXT NOT NULL, parsed_json TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS candidates (candidate_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE, candidate_json TEXT NOT NULL, validation_json TEXT NOT NULL)")
                # Replace one approved document atomically, preserving other courses.
                db.execute("DELETE FROM documents WHERE document_id=?", (parsed.document_id,))
                db.execute("INSERT INTO documents VALUES (?,?,?,?)", (parsed.document_id, parsed.sha256, course_id,
                            json.dumps(asdict(parsed), default=lambda v: v.isoformat(), ensure_ascii=False)))
                for c, v in zip(candidates, validations, strict=True):
                    db.execute("INSERT INTO candidates VALUES (?,?,?,?)", (c.candidate_id, parsed.document_id,
                                json.dumps(asdict(c), ensure_ascii=False), json.dumps(asdict(v), default=lambda x: x.isoformat(), ensure_ascii=False)))
        except (sqlite3.Error, ValueError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Document ingestion could not be persisted atomically.") from None

    def load(self, document):
        if not self.path.exists():
            return None
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
                row = db.execute("SELECT sha256, course_id, parsed_json FROM documents WHERE document_id=?", (document.document_id,)).fetchone()
                if row is None or row[0] != document.sha256 or row[1] != document.course_id:
                    return None
                p = json.loads(row[2])
                parsed = ParsedDocument(p["document_id"], p["sha256"], tuple(DocumentPage(**page) for page in p["pages"]),
                                        datetime.fromisoformat(p["parsed_at"]), p["parser_version"])
                if parsed.document_id != document.document_id or parsed.sha256 != document.sha256:
                    raise ValueError
                rows = db.execute("SELECT candidate_id, candidate_json, validation_json FROM candidates WHERE document_id=? ORDER BY candidate_id", (document.document_id,)).fetchall()
                result = []
                for key, candidate_json, validation_json in rows:
                    candidate = DeadlineCandidate(**json.loads(candidate_json))
                    if candidate.candidate_id != key or candidate.document_id != document.document_id:
                        raise ValueError
                    result.append((candidate, json.loads(validation_json)))
        except (sqlite3.Error, KeyError, ValueError, TypeError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Persisted document evidence is invalid or inaccessible.") from None
        return parsed, tuple(result)
