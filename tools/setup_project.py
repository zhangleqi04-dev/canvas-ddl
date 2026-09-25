#!/usr/bin/env python3
"""Cross-platform local installer for the Canvas DDL engine and Codex skill."""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
SKILL_SOURCE = ROOT / "skills" / "canvas-ddl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Canvas DDL on Windows, macOS, or Linux.",
    )
    parser.add_argument("--ocr", action="store_true", help="Install local OCR support (large download).")
    parser.add_argument("--dev", action="store_true", help="Install development and test dependencies.")
    parser.add_argument(
        "--no-skill",
        action="store_true",
        help="Install only the Python engine; do not copy the Codex skill.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Do not prompt for Canvas settings; create .env from the template if needed.",
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    display = " ".join(command)
    print(f"\n> {display}")
    subprocess.run(command, cwd=ROOT, check=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.rstrip("\r\n"))
        if match:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[match.group(1)] = value
    return values


def env_value(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:+@-]*", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_env(lines: list[str], key: str, value: str) -> list[str]:
    replacement = f"{key}={env_value(value)}\n"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            return lines
    lines.append(replacement)
    return lines


def valid_canvas_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value.rstrip("/"))
        _ = parsed.port
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname != "your-institution.instructure.com"
            and parsed.path in ("", "/")
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:
        return False


def configure_env(non_interactive: bool) -> bool:
    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        print(f"Created {ENV_FILE.name} from {ENV_EXAMPLE.name}.")

    lines = ENV_FILE.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    values = parse_env(lines)
    token = values.get("CANVAS_TOKEN", "") or values.get("CANVAS_API_TOKEN", "")
    configured = valid_canvas_origin(values.get("CANVAS_BASE_URL", "")) and bool(token)
    if configured or non_interactive or not sys.stdin.isatty():
        return configured

    print("\nConfigure Canvas. The token is entered locally and is not displayed.")
    current_url = values.get("CANVAS_BASE_URL", "")
    while not valid_canvas_origin(current_url):
        current_url = input("Canvas HTTPS origin (for example https://canvas.example.edu): ").strip().rstrip("/")
        if not valid_canvas_origin(current_url):
            print("Enter an HTTPS origin without a path, query, or embedded credentials.")

    token = values.get("CANVAS_TOKEN", "") or values.get("CANVAS_API_TOKEN", "")
    while not token or any(character in token for character in "\r\n"):
        token = getpass.getpass("Canvas personal access token: ").strip()
        if not token:
            print("A Canvas token is required.")

    timezone = values.get("CANVAS_TIMEZONE", "Asia/Singapore") or "Asia/Singapore"
    while True:
        entered = input(f"IANA timezone [{timezone}]: ").strip()
        timezone = entered or timezone
        try:
            ZoneInfo(timezone)
            break
        except (ValueError, ZoneInfoNotFoundError):
            print("Enter a valid IANA timezone such as Asia/Singapore or Europe/London.")

    set_env(lines, "CANVAS_BASE_URL", current_url)
    set_env(lines, "CANVAS_TOKEN", token)
    set_env(lines, "CANVAS_TIMEZONE", timezone)
    ENV_FILE.write_text("".join(lines), encoding="utf-8")
    print("Saved local Canvas settings to .env (excluded from Git).")
    return True


def install_skill() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    destination = codex_home / "skills" / "canvas-ddl"
    pointer = destination / "scripts" / "engine-home.txt"
    if destination.exists():
        existing_root = None
        if pointer.is_file():
            try:
                existing_root = Path(pointer.read_text(encoding="utf-8-sig").strip()).resolve()
            except (OSError, ValueError):
                pass
        if existing_root != ROOT:
            raise RuntimeError(
                f"Refusing to overwrite an unrelated skill at {destination}. "
                "Remove or rename it, then run setup again."
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_SOURCE, destination, dirs_exist_ok=True)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(ROOT), encoding="utf-8")
    return destination


def main() -> int:
    args = parse_args()
    if sys.version_info < (3, 11):
        print("Canvas DDL requires Python 3.11 or newer.", file=sys.stderr)
        return 2

    if not VENV.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    python = venv_python()
    if not python.is_file():
        print(f"Virtual environment is incomplete: {python} was not found.", file=sys.stderr)
        return 2

    extras = [name for name, enabled in (("ocr", args.ocr), ("dev", args.dev)) if enabled]
    project = "." if not extras else f".[{','.join(extras)}]"
    run([str(python), "-m", "pip", "install", "-e", project])
    configured = configure_env(args.non_interactive)

    skill_destination = None
    if not args.no_skill:
        skill_destination = install_skill()

    run([str(python), "-c", "import canvas_ddl; print('Canvas DDL engine import: OK')"])
    print("\nSetup complete.")
    if skill_destination:
        print(f"Codex skill: {skill_destination}")
    if configured:
        print("Canvas configuration: ready. Restart Codex, then invoke $canvas-ddl.")
    else:
        print(f"Next step: edit {ENV_FILE} and set CANVAS_BASE_URL and CANVAS_TOKEN.")
    if not args.ocr:
        print("OCR was not installed. Re-run with --ocr if scanned documents must be parsed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
