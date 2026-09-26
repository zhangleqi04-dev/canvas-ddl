from pathlib import Path
import re
from urllib.parse import unquote

import yaml


ROOT = Path(__file__).resolve().parents[2]


def documentation_files():
    return (
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "AGENTS.md",
        *sorted((ROOT / "docs").glob("*.md")),
        *sorted((ROOT / "skills").glob("**/*.md")),
    )


def test_local_markdown_links_resolve():
    failures = []
    for document in documentation_files():
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", line):
                target = raw_target.strip().strip("<>")
                if target.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                relative = unquote(target.split("#", 1)[0])
                if relative and not (document.parent / relative).resolve().exists():
                    failures.append(f"{document.relative_to(ROOT)}:{line_number}: {target}")
    assert not failures, "Broken local documentation links:\n" + "\n".join(failures)


def test_issue_forms_are_valid_and_do_not_request_secrets():
    template_dir = ROOT / ".github" / "ISSUE_TEMPLATE"
    forms = [yaml.safe_load(path.read_text(encoding="utf-8")) for path in sorted(template_dir.glob("*.yml"))
             if path.name != "config.yml"]

    assert {form["name"] for form in forms} == {"Bug report", "Feature request"}
    for form in forms:
        ids = [item.get("id") for item in form["body"] if item.get("id")]
        assert len(ids) == len(set(ids))
        text = str(form).lower()
        assert "token" in text and "private course" in text

    config = yaml.safe_load((template_dir / "config.yml").read_text(encoding="utf-8"))
    assert config["blank_issues_enabled"] is False
    assert any("security/advisories/new" in link["url"] for link in config["contact_links"])
