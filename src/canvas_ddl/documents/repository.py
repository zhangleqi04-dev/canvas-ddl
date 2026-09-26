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
                db.execute("CREATE TABLE IF NOT EXISTS semantic_reviews (request_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE, review_json TEXT NOT NULL)")
                # Replace one approved document atomically, preserving other courses.
                db.execute("DELETE FROM documents WHERE document_id=?", (parsed.document_id,))
                db.execute("INSERT INTO documents VALUES (?,?,?,?)", (parsed.document_id, parsed.sha256, course_id,
                            json.dumps(asdict(parsed), default=lambda v: v.isoformat(), ensure_ascii=False)))
                for c, v in zip(candidates, validations, strict=True):
                    db.execute("INSERT INTO candidates VALUES (?,?,?,?)", (c.candidate_id, parsed.document_id,
                                json.dumps(asdict(c), ensure_ascii=False), json.dumps(asdict(v), default=lambda x: x.isoformat(), ensure_ascii=False)))
        except (sqlite3.Error, ValueError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Document ingestion could not be persisted atomically.") from None

    def save_semantic(self, parsed, candidates, validations, reviews, course_id):
        """Replace semantic proposals/reviews for one exact parsed document atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.path) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("CREATE TABLE IF NOT EXISTS documents (document_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, course_id TEXT NOT NULL, parsed_json TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS candidates (candidate_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE, candidate_json TEXT NOT NULL, validation_json TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS semantic_reviews (request_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE, review_json TEXT NOT NULL)")
                row = db.execute("SELECT sha256, course_id FROM documents WHERE document_id=?", (parsed.document_id,)).fetchone()
                if row is not None and row != (parsed.sha256, course_id):
                    raise ValueError
                encoded = json.dumps(asdict(parsed), default=lambda v: v.isoformat(), ensure_ascii=False)
                db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?)", (parsed.document_id, parsed.sha256, course_id, encoded))
                db.execute("DELETE FROM candidates WHERE document_id=?", (parsed.document_id,))
                db.execute("DELETE FROM semantic_reviews WHERE document_id=?", (parsed.document_id,))
                for request_id, review in reviews.items():
                    db.execute("INSERT INTO semantic_reviews VALUES (?,?,?)", (
                        request_id, parsed.document_id, json.dumps(review, ensure_ascii=False),
                    ))
                for candidate, validation in zip(candidates, validations, strict=True):
                    db.execute("INSERT INTO candidates VALUES (?,?,?,?)", (
                        candidate.candidate_id, parsed.document_id,
                        json.dumps(asdict(candidate), ensure_ascii=False),
                        json.dumps(asdict(validation), default=lambda value: value.isoformat(), ensure_ascii=False),
                    ))
        except (sqlite3.Error, ValueError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Semantic document review could not be persisted atomically.") from None

    def load_semantic_reviews(self, document):
        if not self.path.exists():
            return {}
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "semantic_reviews" not in tables:
                    return {}
                rows = db.execute(
                    "SELECT request_id, review_json FROM semantic_reviews WHERE document_id=? ORDER BY request_id",
                    (document.document_id,),
                ).fetchall()
                result = {}
                for request_id, review_json in rows:
                    review = json.loads(review_json)
                    if review.get("request_id") != request_id:
                        raise ValueError
                    result[request_id] = review
                return result
        except (sqlite3.Error, ValueError, TypeError):
            raise ApplicationError("DOCUMENT_STORE_UNAVAILABLE", "Persisted semantic review is invalid or inaccessible.") from None

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
