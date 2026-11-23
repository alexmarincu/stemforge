"""The design document stays a mirror of the visual root.

``DESIGN.md`` is the agent-facing account of the shell's visual language, so its
token table must quote :mod:`stemforge.ui.theme` exactly. A stale table would
teach the next change the wrong palette, and the failure names the token that
drifted instead of leaving the mismatch to review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from stemforge.ui import theme

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN = REPO_ROOT / "DESIGN.md"

TOKEN_ROW = re.compile(r"^\|\s*`(--[a-z0-9-]+)`\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
TOKEN_DECLARATION = re.compile(r"(--[a-z0-9-]+):\s*([^;]+);")
HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def _design_text() -> str:
    """The document text; a missing file fails the token-table tests here."""
    if not DESIGN.is_file():
        pytest.fail("DESIGN.md is the agent-facing visual contract; restore it at the root")
    return DESIGN.read_text(encoding="utf-8")


def _documented_tokens() -> list[tuple[str, str]]:
    return TOKEN_ROW.findall(_design_text())


def _declared_tokens() -> dict[str, str]:
    return {name: value.strip() for name, value in TOKEN_DECLARATION.findall(theme.TOKENS_CSS)}


def test_design_document_exists() -> None:
    assert DESIGN.is_file(), "DESIGN.md is the agent-facing visual contract; restore it at the root"


def test_token_table_matches_the_theme_block() -> None:
    declared = _declared_tokens()
    documented = _documented_tokens()
    names = [name for name, _ in documented]

    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"DESIGN.md documents these tokens more than once: {duplicates}"

    missing = sorted(declared.keys() - set(names))
    assert not missing, f"DESIGN.md is missing tokens declared in theme.TOKENS_CSS: {missing}"

    stale = sorted(set(names) - declared.keys())
    assert not stale, f"DESIGN.md documents tokens theme.TOKENS_CSS no longer declares: {stale}"

    values = dict(documented)
    mismatched = [
        f"{name}: DESIGN.md {values[name]!r} != theme {declared[name]!r}"
        for name in sorted(declared)
        if values.get(name) != declared[name]
    ]
    assert not mismatched, (
        "DESIGN.md values must mirror theme.TOKENS_CSS; move the table in the same "
        "change as the token block:\n" + "\n".join(mismatched)
    )


def test_hex_literals_outside_the_table_are_tokens() -> None:
    prose = TOKEN_ROW.sub("", _design_text())
    allowed = {value for value in _declared_tokens().values() if HEX_LITERAL.fullmatch(value)}

    offenders = sorted(set(HEX_LITERAL.findall(prose)) - allowed)
    assert not offenders, (
        "DESIGN.md may only quote hex values declared as tokens in theme.TOKENS_CSS; "
        f"add the colour to the token block first: {offenders}"
    )
