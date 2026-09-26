"""Real subprocess entrypoints: UTF-8 JSON, exit status, venv handoff."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "canvas-ddl" / "scripts"


@pytest.mark.parametrize("script,args", [
    ("deadlines.py", ["--bad-option"]), ("upcoming.py", ["--bad-option"]),
    ("courses.py", ["--bad-option"]), ("deadline_details.py", []),
    ("semantic_review_requests.py", ["--bad-option"]),
    ("semantic_ingest.py", ["--bad-option"]),
])
def test_scripts_json_error_no_network(script, args, tmp_path):
    result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
                            capture_output=True, text=True, encoding="utf8", cwd=tmp_path)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_QUERY"


def test_missing_engine_returns_json_and_invalid_home_not_echoed(tmp_path):
    env = {**os.environ, "CANVAS_DDL_HOME": str(tmp_path / "fixture-secret")}
    result = subprocess.run([sys.executable, str(SCRIPTS / "courses.py")], env=env,
                            capture_output=True, text=True, encoding="utf8")
    assert result.returncode == 2 and "fixture-secret" not in result.stdout and not result.stderr
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_CONFIG"


def test_handoff_waits_for_json_output_without_using_canvas():
    # A different interpreter prefix exercises the auto-venv subprocess branch.
    base = getattr(sys, "_base_executable", None)
    if not base or Path(base).resolve() == Path(sys.executable).resolve():
        pytest.skip("A separate base interpreter is required for handoff.")
    result = subprocess.run([base, "-X", "utf8", str(SCRIPTS / "deadlines.py"), "--bad-option"],
                            capture_output=True, text=True, encoding="utf8")
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_QUERY"


def test_structured_time_script_rejects_bad_intent_without_network(tmp_path):
    path = tmp_path / "time.json"
    path.write_text('{"kind":"academic_week","offset":7,"original_text":"第7周"}', encoding="utf8")
    result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "deadlines.py"),
                             "--time-intent-file", str(path)], capture_output=True, text=True, encoding="utf8", cwd=tmp_path)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_TIME_INTENT"
