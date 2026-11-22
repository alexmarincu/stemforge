"""User-facing copy has one home and the glossary's Import verb.

An inline sentence in a page or component drifts from ``ui/formatting.py`` and
from the glossary. This scan fails on a visible literal passed to a copy sink
and on an avoided Import synonym in the copy table.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "stemforge" / "ui"
FORMATTING = UI / "formatting.py"
COPY_SOURCES = (UI / "page.py", *sorted((UI / "components").glob("*.py")))

# The parameter carrying visible text for each copy sink. The first positional
# argument is the same parameter, including ``ui.input``'s label.
COPY_ARGUMENTS: dict[str, frozenset[str]] = {
    "ui.label": frozenset({"text"}),
    "ui.button": frozenset({"text"}),
    "ui.notify": frozenset({"message"}),
    "toast": frozenset({"message"}),
    "tooltip": frozenset({"text"}),
    "ui.tooltip": frozenset({"text"}),
    "ui.input": frozenset({"label", "placeholder"}),
}

# Technical strings that name no copy: an empty label is a slot a later
# ``set_text`` fills, and a bare separator joins fragments.
TECHNICAL_LITERALS = frozenset({"", " · "})

# Lowercase identifiers are enum values, mapping keys, and reason tokens, not
# prose; a visible label is capitalised or contains whitespace.
CODE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

# The glossary's avoided Import verbs, including inflections. Word boundaries
# keep Download and Upload out of the scan.
AVOIDED_SYNONYMS = re.compile(
    r"\b(?:load|loads|loaded|loading|add|adds|added|adding|ingest|ingests"
    r"|ingested|ingesting)\b",
    re.IGNORECASE,
)


def _copy_sink(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if not isinstance(func, ast.Attribute):
        return None
    if isinstance(func.value, ast.Name) and func.value.id == "ui":
        return f"ui.{func.attr}"
    if func.attr == "tooltip":
        return "tooltip"
    return None


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_copy(tree: ast.AST) -> list[tuple[int, str, str]]:
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        sink = _copy_sink(node)
        if sink is None or sink not in COPY_ARGUMENTS:
            continue
        keywords = COPY_ARGUMENTS[sink]
        if node.args:
            value = _literal(node.args[0])
            if value is not None and value not in TECHNICAL_LITERALS:
                violations.append((node.lineno, sink, value))
        for keyword in node.keywords:
            if keyword.arg not in keywords:
                continue
            value = _literal(keyword.value)
            if value is not None and value not in TECHNICAL_LITERALS:
                violations.append((node.lineno, f"{sink} {keyword.arg}", value))
    return violations


def _docstrings(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def _copy_strings(tree: ast.AST) -> list[tuple[int, str]]:
    docstrings = _docstrings(tree)
    strings: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append((node.lineno, node.value))
    return strings


def _synonyms(tree: ast.AST) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for line, text in _copy_strings(tree):
        if CODE_IDENTIFIER.fullmatch(text):
            continue
        for match in AVOIDED_SYNONYMS.finditer(text):
            violations.append((line, match.group(0)))
    return violations


def test_pages_and_components_render_copy_constants() -> None:
    violations: list[str] = []
    for path in COPY_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, sink, text in _literal_copy(tree):
            relative = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
    assert not violations, (
        "Visible copy belongs in ui/formatting.py; render the constant instead "
        "of an inline literal.\n" + "\n".join(violations)
    )


def test_the_scan_rejects_an_inline_label() -> None:
    tree = ast.parse('ui.label("Add audio")\n')

    assert _literal_copy(tree) == [(1, "ui.label", "Add audio")]


def test_the_scan_rejects_keywords_and_a_direct_tooltip() -> None:
    tree = ast.parse(
        'ui.button(text="Save")\n'
        'ui.tooltip("Remove me")\n'
        'ui.input("Name")\n'
        'toast(message="Load done")\n'
    )

    assert _literal_copy(tree) == [
        (1, "ui.button text", "Save"),
        (2, "ui.tooltip", "Remove me"),
        (3, "ui.input", "Name"),
        (4, "toast message", "Load done"),
    ]


def test_the_scan_accepts_constants_fstrings_and_technical_literals() -> None:
    tree = ast.parse(
        "ui.label(formatting.IMPORT_AUDIO_LABEL)\n"
        'ui.label(f"Tracks {count}")\n'
        'ui.label("")\n'
        'ui.label(" · ")\n'
        "ui.button(text=formatting.EXPORT_CONFIRM)\n"
        "ui.input(placeholder=formatting.YOUTUBE_URL_PLACEHOLDER)\n"
    )

    assert _literal_copy(tree) == []


def test_copy_uses_the_glossary_import_verb() -> None:
    tree = ast.parse(FORMATTING.read_text(encoding="utf-8"), filename=str(FORMATTING))
    violations = _synonyms(tree)
    assert not violations, (
        "The glossary's Import verb avoids Load, Add, and Ingest; use Import.\n"
        + "\n".join(f"{FORMATTING.name}:{line}: {word!r}" for line, word in violations)
    )


def test_the_synonym_scan_rejects_load() -> None:
    tree = ast.parse('LABEL = "Load audio"\n')

    assert _synonyms(tree) == [(1, "Load")]


def test_the_synonym_scan_ignores_technical_identifiers() -> None:
    tree = ast.parse('KEY = "model_load"\n')

    assert _synonyms(tree) == []
