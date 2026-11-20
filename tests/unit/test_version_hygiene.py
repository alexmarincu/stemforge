"""The version lives in exactly one file.

Packaging metadata, docs, workflows, and bundle specs must derive the version
from ``src/stemforge/__init__.py``. A literal copy elsewhere will drift on the
next bump, so this guard names every offending line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import stemforge

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("src", "packaging", "scripts", ".github", "pyproject.toml")
VERSION_SOURCE = "src/stemforge/__init__.py"
SKIPPED_DIRS = frozenset({"__pycache__", ".git", ".venv", "node_modules"})


def _candidate_files() -> list[Path]:
    files: list[Path] = []
    for entry in SCANNED:
        path = REPO_ROOT / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(path.rglob("*"))
    return files


def test_the_version_lives_only_in_the_package() -> None:
    version = stemforge.__version__
    pattern = re.compile(rf"(?<![\w.])v?{re.escape(version)}(?![\w.])")
    offenders: list[str] = []
    for path in sorted(_candidate_files()):
        if not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == VERSION_SOURCE:
            continue
        if SKIPPED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{relative}:{number}")

    assert offenders == [], (
        "the version must only live in src/stemforge/__init__.py; "
        f"also found at: {', '.join(offenders)}"
    )
