"""User-facing copy resolves through the English catalog.

The page shell renders catalog keys with ``i18n.t``; the English catalog is the
copy source, so a page-shell prose literal or a key missing from ``en.json`` is a
defect. Components keep the older constant rule until their own migration, and
the catalog's values are scanned for the glossary's avoided Import verbs.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "src" / "stemforge" / "ui"
PAGE_SHELL = (UI / "page.py", UI / "theme.py")
UI_SOURCES = tuple(sorted(UI.rglob("*.py")))
COMPONENT_SOURCES = tuple(sorted((UI / "components").glob("*.py")))
ENGLISH_CATALOG = REPO_ROOT / "src" / "stemforge" / "i18n" / "catalogs" / "en.json"

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
    "ui.page_title": frozenset({"title"}),
    "ui.switch": frozenset({"text", "label"}),
    "ui.expansion": frozenset({"text"}),
    "ui.image": frozenset({"alt"}),
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

ARIA_LABEL_IN_PROPS = re.compile(r"\baria-label=([^,;]+)")
IMAGE_ALT = re.compile(r'\balt="([^"]*)"')


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


def _props_literals(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Visible ``aria-label`` values embedded in a plain ``props`` string."""
    violations: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "props":
            continue
        for argument in node.args:
            value = _literal(argument)
            if value is None:
                continue
            for match in ARIA_LABEL_IN_PROPS.finditer(value):
                label = match.group(1).strip()
                if label and not label.startswith("{"):
                    violations.append((node.lineno, "props aria-label", label))
    return violations


def _alt_literals(tree: ast.AST) -> list[tuple[int, str, str]]:
    """A literal ``alt`` attribute in HTML built by the page shell."""
    violations: list[tuple[int, str, str]] = []
    for line, text in _copy_strings(tree):
        for match in IMAGE_ALT.finditer(text):
            value = match.group(1)
            if value and not value.startswith("{"):
                violations.append((line, "alt", value))
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


def _i18n_t(node: ast.Call) -> bool:
    function = node.func
    if isinstance(function, ast.Attribute) and function.attr == "t":
        return isinstance(function.value, ast.Name) and function.value.id == "i18n"
    return isinstance(function, ast.Name) and function.id == "t"


def _catalog_keys(tree: ast.AST) -> list[tuple[int, str]]:
    keys: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _i18n_t(node):
            continue
        if not node.args:
            continue
        key = _literal(node.args[0])
        if key is not None:
            keys.append((node.lineno, key))
    return keys


def _catalog_values(payload: Mapping[str, object]) -> Iterator[tuple[str, str]]:
    for key, value in payload.items():
        if isinstance(value, str):
            yield key, value
        elif isinstance(value, Mapping):
            for form, text in value.items():
                if isinstance(text, str):
                    yield f"{key}.{form}", text


def _missing_catalog_keys(tree: ast.AST, catalog: Mapping[str, object]) -> list[tuple[int, str]]:
    return [(line, key) for line, key in _catalog_keys(tree) if key not in catalog]


def _catalog_synonyms(catalog: Mapping[str, object]) -> list[tuple[str, str]]:
    violations: list[tuple[str, str]] = []
    for key, text in _catalog_values(catalog):
        if CODE_IDENTIFIER.fullmatch(text):
            continue
        for match in AVOIDED_SYNONYMS.finditer(text):
            violations.append((key, match.group(0)))
    return violations


def _load_english_catalog() -> Mapping[str, object]:
    payload: object = json.loads(ENGLISH_CATALOG.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_components_render_copy_constants() -> None:
    violations: list[str] = []
    for path in COMPONENT_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line, sink, text in _literal_copy(tree):
            relative = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
    assert not violations, (
        "Visible component copy belongs in ui/formatting.py until its migration; "
        "render the constant instead of an inline literal.\n" + "\n".join(violations)
    )


def test_page_shell_has_no_prose_literals_at_copy_sinks() -> None:
    violations: list[str] = []
    for path in PAGE_SHELL:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for line, sink, text in _literal_copy(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
        for line, sink, text in _props_literals(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
        for line, sink, text in _alt_literals(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
    assert not violations, (
        "Page-shell copy resolves through i18n.t and the English catalog; "
        "render a catalog key instead of an inline literal.\n" + "\n".join(violations)
    )


def test_ui_catalog_keys_exist_in_english() -> None:
    catalog = _load_english_catalog()
    violations: list[str] = []
    for path in UI_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for line, key in _missing_catalog_keys(tree, catalog):
            violations.append(f"{relative}:{line}: {key!r} is missing from en.json")
    assert not violations, (
        "Every i18n.t key used by the UI must exist in the English catalog.\n"
        + "\n".join(violations)
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


def test_the_scan_rejects_page_titles_switch_labels_and_expansion_labels() -> None:
    tree = ast.parse(
        'ui.page_title("StemForge")\nui.switch("YouTube import")\nui.expansion("Details")\n'
    )

    assert _literal_copy(tree) == [
        (1, "ui.page_title", "StemForge"),
        (2, "ui.switch", "YouTube import"),
        (3, "ui.expansion", "Details"),
    ]


def test_the_scan_rejects_a_props_aria_label_and_an_image_alt() -> None:
    props = ast.parse('ui.element("section").props("aria-label=Transport")\n')
    alt = ast.parse('ui.html(\'<img src="mark.svg" alt="StemForge">\')\n')

    assert _props_literals(props) == [(1, "props aria-label", "Transport")]
    assert _alt_literals(alt) == [(1, "alt", "StemForge")]


def test_the_scan_accepts_catalog_keys_and_interpolated_accessibility_text() -> None:
    tree = ast.parse(
        "ui.label(i18n.t('page.import_audio'))\n"
        "ui.page_title(i18n.t('app.name'))\n"
        "ui.switch(i18n.t('page.youtube_setting'))\n"
        "ui.expansion(i18n.t('details.label'))\n"
        "ui.element('section').props(f'aria-label={i18n.t(\"page.transport_aria_label\")}')\n"
    )

    assert _literal_copy(tree) == []
    assert _props_literals(tree) == []
    assert _alt_literals(tree) == []


def test_a_missing_catalog_key_is_rejected() -> None:
    tree = ast.parse("i18n.t('page.no_such_message')\n")

    assert _missing_catalog_keys(tree, {"app.name": "StemForge"}) == [(1, "page.no_such_message")]
    assert _missing_catalog_keys(tree, {"page.no_such_message": "Present"}) == []


def test_the_page_shell_keys_resolve_in_english() -> None:
    tree = ast.parse("i18n.t('app.name')\n")

    assert _missing_catalog_keys(tree, _load_english_catalog()) == []


def test_the_english_catalog_uses_the_glossary_import_verb() -> None:
    violations = _catalog_synonyms(_load_english_catalog())
    assert not violations, (
        "The glossary's Import verb avoids Load, Add, and Ingest; use Import.\n"
        + "\n".join(f"en.json {key}: {word!r}" for key, word in violations)
    )


def test_the_legacy_copy_module_uses_the_glossary_import_verb() -> None:
    path = UI / "formatting.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = _synonyms(tree)
    assert not violations, (
        "The glossary's Import verb avoids Load, Add, and Ingest; use Import.\n"
        + "\n".join(f"{path.name}:{line}: {word!r}" for line, word in violations)
    )


def test_the_catalog_synonym_scan_rejects_load() -> None:
    assert _catalog_synonyms({"page.audio": "Load audio"}) == [("page.audio", "Load")]


def test_the_synonym_scan_rejects_load() -> None:
    tree = ast.parse('LABEL = "Load audio"\n')

    assert _synonyms(tree) == [(1, "Load")]


def test_the_synonym_scan_ignores_technical_identifiers() -> None:
    tree = ast.parse('KEY = "model_load"\n')

    assert _synonyms(tree) == []
