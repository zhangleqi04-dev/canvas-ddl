import json
from pathlib import Path
import tomllib

import pytest

from canvas_ddl import __version__
from canvas_ddl.cli.main import parser


ROOT = Path(__file__).resolve().parents[2]


def test_release_version_sources_are_synchronized():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "canvas_ddl.__version__"
    assert plugin["version"] == __version__
    for relative in ("README.md", "docs/PRD_DDL_ONLY.md", "docs/ARCHITECTURE.md", "docs/AGENTS.md"):
        assert f"v{__version__}" in (ROOT / relative).read_text(encoding="utf-8")


def test_cli_reports_release_version(capsys):
    with pytest.raises(SystemExit) as exited:
        parser().parse_args(["--version"])

    assert exited.value.code == 0
    assert capsys.readouterr().out == f"canvas-ddl {__version__}\n"
