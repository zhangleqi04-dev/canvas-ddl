"""Locate the one engine; no network or deadline business logic here."""
import json
import os
import subprocess
from pathlib import Path
import sys


def run(command):
    skill = Path(__file__).resolve().parent.parent
    configured = os.environ.get("CANVAS_DDL_HOME")
    pointer = skill / "scripts" / "engine-home.txt"
    root = Path(configured).resolve() if configured else None
    if root is None:
        root = next((p for p in skill.parents if (p / "pyproject.toml").is_file() and (p / "src" / "canvas_ddl").is_dir()), None)
    if root is None and pointer.is_file():
        root = Path(pointer.read_text(encoding="utf-8-sig").strip()).resolve()
    if root is None or not (root / "src" / "canvas_ddl").is_dir():
        print(json.dumps({"status": "error", "error": {"code": "INVALID_CONFIG", "message": "Set CANVAS_DDL_HOME to the Canvas DDL project directory."}}))
        sys.exit(2)
    interpreter = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if interpreter.is_file() and Path(sys.executable).resolve() != interpreter.resolve():
        # Windows execv may detach from the calling tool's output pipes. Wait
        # explicitly and forward only the application's sanitized JSON stdout.
        try:
            result = subprocess.run([str(interpreter), "-X", "utf8", str(Path(sys.argv[0]).resolve()), *sys.argv[1:]],
                                    capture_output=True, text=True, encoding="utf-8", timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            print(json.dumps({"status": "error", "error": {"code": "CANVAS_UNAVAILABLE", "message": "The local deadline query could not complete."}}))
            sys.exit(2)
        if result.stdout:
            sys.stdout.write(result.stdout)
        else:
            print(json.dumps({"status": "error", "error": {"code": "INTERNAL_ERROR", "message": "The local engine returned no structured result."}}))
        sys.exit(result.returncode if result.stdout else 2)
    sys.path.insert(0, str(root / "src"))
    try:
        from canvas_ddl.cli.main import main
    except ImportError:
        print(json.dumps({"status": "error", "error": {"code": "INVALID_CONFIG", "message": "Install this project's Python dependencies before querying."}}))
        sys.exit(2)
    sys.exit(main(["--env-file", str(root / ".env"), command, *sys.argv[1:]]))
