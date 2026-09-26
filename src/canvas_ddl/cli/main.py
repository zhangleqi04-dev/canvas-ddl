"""Argument parsing and JSON only; all operations go through DeadlineService."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from canvas_ddl import __version__
from canvas_ddl.bootstrap import build_deadline_service
from canvas_ddl.canvas.errors import ApplicationError
from canvas_ddl.deadlines.query import DeadlineQuery
from canvas_ddl.deadlines.time_intent import TimeIntent, invalid_intent
from canvas_ddl.serialization import encode, error_result, redact


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Do not echo invalid user input, which could contain credentials.
        raise ApplicationError("INVALID_QUERY", "Invalid command arguments; use --help for the supported interface.")


def timestamp(text):
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ApplicationError("INVALID_TIME_RANGE", "Use timezone-aware ISO 8601 timestamps.") from None


def parser():
    p = Parser(prog="canvas-ddl")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--env-file", default=str(Path.cwd() / ".env"))
    commands = p.add_subparsers(dest="command", required=True, parser_class=Parser)
    courses = commands.add_parser("courses")
    courses.add_argument("--course")
    for name in ("deadlines", "upcoming"):
        c = commands.add_parser(name)
        c.add_argument("--course")
        c.add_argument("--type", action="append", dest="types")
        c.add_argument("--submission-status", action="append", dest="statuses")
        c.add_argument("--limit", type=int)
        c.add_argument("--document-mode", choices=("auto", "existing", "refresh"), default="auto")
        if name == "deadlines":
            c.add_argument("--time-intent", help="LLM-normalized structured time intent JSON; exclusive with --start/--end")
            c.add_argument("--time-intent-file", help="UTF-8 JSON intent file (bounded); alternative to --time-intent")
            c.add_argument("--start")
            c.add_argument("--end")
        else:
            c.add_argument("--days", type=int, default=7)
    detail = commands.add_parser("deadline")
    detail.add_argument("--id", required=True, dest="deadline_id")
    commands.add_parser("documents")
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--document", required=True)
    review_requests = commands.add_parser("semantic-review-requests")
    review_requests.add_argument("--document", required=True)
    review_requests.add_argument("--offset", type=int, default=0)
    review_requests.add_argument("--limit", type=int, default=20)
    semantic_ingest = commands.add_parser("semantic-ingest")
    semantic_ingest.add_argument("--document", required=True)
    semantic_ingest.add_argument("--review-file", required=True)
    prepare = commands.add_parser("prepare-documents")
    prepare.add_argument("--course", action="append", required=True)
    prepare.add_argument("--limit", type=int, default=20)
    approve = commands.add_parser("approve-document")
    approve.add_argument("--document", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--valid-from", required=True)
    approve.add_argument("--valid-until", required=True)
    approve.add_argument("--confirm-official", action="store_true", required=True)
    return p


def main(argv=None, *, factory=build_deadline_service, stdout=None):
    out = stdout or sys.stdout
    service = config = None
    code = 0
    try:
        args = parser().parse_args(argv)
        intent = None
        if args.command == "deadlines" and (args.time_intent is not None or args.time_intent_file is not None):
            if args.start is not None or args.end is not None:
                raise invalid_intent()
            if args.time_intent is not None and args.time_intent_file is not None:
                raise invalid_intent()
            text = args.time_intent
            if args.time_intent_file is not None:
                try:
                    with Path(args.time_intent_file).open("rb") as stream:
                        payload = stream.read(32001)
                    if len(payload) > 32000:
                        raise invalid_intent()
                    text = payload.decode("utf-8-sig")
                except (OSError, UnicodeError, ValueError):
                    raise invalid_intent() from None
            intent = TimeIntent.from_json(text)
        service, config = factory(args.env_file)
        if args.command == "courses":
            courses = service.list_courses(args.course)
            result = {"status": "ok", "courses": encode(courses), "data_freshness": "live", "generated_at": encode(service.clock())}
        elif args.command == "documents":
            result = {"status": "ok", "documents": encode(service.list_documents())}
        elif args.command == "ingest":
            result = {"status": "ok", "ingestion": service.ingest_document(args.document)}
        elif args.command == "semantic-review-requests":
            result = {"status": "ok", "semantic_review": service.semantic_review_requests(
                args.document, offset=args.offset, limit=args.limit)}
        elif args.command == "semantic-ingest":
            try:
                with Path(args.review_file).open("rb") as stream:
                    content = stream.read(1_000_001)
                if len(content) > 1_000_000:
                    raise ValueError
                payload = content.decode("utf-8-sig")
            except (OSError, UnicodeError, ValueError):
                raise ApplicationError("INVALID_SEMANTIC_REVIEW", "Semantic review file must be bounded UTF-8 JSON.") from None
            result = {"status": "ok", "semantic_ingestion": service.apply_semantic_review(args.document, payload)}
        elif args.command == "prepare-documents":
            result = service.prepare_documents(args.course, limit=args.limit)
        elif args.command == "approve-document":
            result = {"status": "ok", "ingestion": service.approve_document(args.document,
                      approved_by=args.approved_by, valid_from=args.valid_from, valid_until=args.valid_until)}
        elif args.command == "deadline":
            deadline = service.get_deadline(args.deadline_id)
            freshness = "ingested_document" if deadline.source_type == "official_document" else "live_with_ingested_documents" if any(
                s.source_type == "official_document" for s in deadline.sources) else "live"
            result = {"status": "ok", "deadline": encode(deadline),
                      "data_freshness": freshness, "generated_at": encode(service.clock())}
        else:
            options = {"course_reference": args.course, "types": tuple(args.types) if args.types is not None else None,
                       "submission_statuses": tuple(args.statuses) if args.statuses is not None else None, "limit": args.limit,
                       "document_mode": args.document_mode}
            if args.command == "upcoming":
                data = service.upcoming(days=args.days, **options)
            else:
                data = service.query(DeadlineQuery(start=timestamp(args.start), end=timestamp(args.end),
                                                   time_intent=intent, **options))
            result = encode(data)
    except ApplicationError as error:
        result, code = error_result(error), 2
    except (OSError, ValueError, TypeError, KeyError):
        result, code = error_result(ApplicationError("INTERNAL_ERROR", "The deadline query could not be completed.")), 2
    except Exception:
        # Never print unexpected exception text or traceback containing request data.
        result, code = error_result(ApplicationError("INTERNAL_ERROR", "The deadline query could not be completed.")), 2
    finally:
        if service is not None:
            try:
                service.client.close()
            except Exception:
                pass  # Cleanup must not leak transport exception text.
    result = redact(result, config.token if config else "")
    print(json.dumps(result, ensure_ascii=False), file=out)
    return code


def entrypoint():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())


if __name__ == "__main__":
    entrypoint()
