"""The local gate: ``scripts/check.sh``, the hooks, and their install.

A local gate only pays for itself if it runs the checks CI runs and if a fresh
clone can install it with one command. These tests pin the marker split, the CI
checks the script must run, each hook's depth, and the documented install.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "scripts" / "check.sh"
PRECOMMIT = REPO_ROOT / ".githooks" / "pre-commit"
PREPUSH = REPO_ROOT / ".githooks" / "pre-push"
README = REPO_ROOT / "README.md"
AGENTS = REPO_ROOT / "AGENTS.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_gate_runs_the_checks_ci_makes() -> None:
    text = _text(CHECK)
    for command in ("uv lock --check", "shellcheck -x", "check_powershell.py"):
        assert command in text, command
    # Both external tools are optional locally; the gate must say it skipped
    # them instead of failing on a machine without them.
    for tool in ("shellcheck", "pwsh"):
        assert f"command -v {tool}" in text, tool
    # Presence is not enough: a half-installed pwsh must not turn every push
    # into its own opaque runtime error.
    assert "pwsh -NoProfile -NonInteractive -Command 'exit 0'" in text


def test_the_gate_keeps_the_ci_marker_split() -> None:
    commands = [
        line.strip()
        for line in _text(CHECK).splitlines()
        if line.strip().startswith(("uv run pytest", "pytest"))
    ]
    for marker in ("unit", "ui", "licensing"):
        assert any(re.search(rf"-m\s+[\"']?{marker}[\"']?\s*$", command) for command in commands), (
            marker,
            commands,
        )
    assert not any("unit or ui or licensing" in command for command in commands)


def test_fast_mode_skips_the_lock_check_and_the_tests() -> None:
    text = _text(CHECK)
    guard_start = text.index("if (( ! fast ))")
    guard_end = text.index("fi", guard_start)
    assert guard_start < text.index("uv lock --check") < guard_end
    assert guard_end < text.index("uv sync --frozen --inexact")

    fast_exit = text.index("if (( fast ))")
    for fast_check in (
        "ruff format --check",
        "ruff check .",
        "mypy src/stemforge",
        "lint-imports",
    ):
        assert text.index(fast_check) < fast_exit, fast_check
    for full_check in ("pytest -m unit", "pytest -m ui", "pytest -m licensing"):
        assert text.index(full_check) > fast_exit, full_check


@pytest.mark.parametrize(
    ("hook", "fast"),
    [(PRECOMMIT, True), (PREPUSH, False)],
    ids=["pre-commit", "pre-push"],
)
def test_each_hook_runs_the_gate_at_its_own_depth(hook: Path, fast: bool) -> None:
    assert hook.is_file()
    text = _text(hook)
    assert "scripts/check.sh" in text
    assert ("--fast" in text) is fast


# Git for Windows has no executable bit to check; the Linux job verifies the
# committed mode.
@pytest.mark.skipif(os.name == "nt", reason="Windows cannot represent the executable bit")
@pytest.mark.parametrize("hook", [PRECOMMIT, PREPUSH], ids=["pre-commit", "pre-push"])
def test_each_hook_is_executable(hook: Path) -> None:
    assert hook.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize("path", [README, AGENTS], ids=["readme", "agents"])
def test_a_fresh_clone_can_install_the_hooks(path: Path) -> None:
    assert "git config core.hooksPath .githooks" in _text(path), path


def test_agents_point_at_the_local_gate_before_a_push() -> None:
    assert "Before pushing, run `scripts/check.sh`" in _text(AGENTS)
