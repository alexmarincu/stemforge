"""User-facing copy resolves through the locale catalogues.

The page shell, components, preflight chrome, and fatal copy render catalogue keys
with ``i18n.t``; the English catalogue is the only copy source and the Romanian
catalogue mirrors every key. A prose literal at a copy sink, a catalogue key missing
from either locale, a glossary synonym, a cedilla, or a term-table violation is a
defect.
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
SOURCE_ROOT = REPO_ROOT / "src" / "stemforge"
PAGE_SHELL = (UI / "page.py", UI / "theme.py")
COMPONENT_SOURCES = tuple(sorted((UI / "components").glob("*.py")))
ENTRY = SOURCE_ROOT / "__main__.py"
RUNTIME_SOURCES = tuple(sorted(SOURCE_ROOT.rglob("*.py")))
ENGLISH_CATALOG = SOURCE_ROOT / "i18n" / "catalogs" / "en.json"
ROMANIAN_CATALOG = SOURCE_ROOT / "i18n" / "catalogs" / "ro.json"
TERMS_TABLE = REPO_ROOT / "docs" / "i18n" / "ro" / "terms.md"

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
    "preflight.NativeChrome": frozenset(
        {
            "fatal_title",
            "details_label",
            "logs_unavailable",
            "copy_details_label",
            "ok_label",
            "already_running_title",
            "already_running_body",
        }
    ),
}

# Technical strings that name no copy: an empty label is a slot a later
# ``set_text`` fills, and a bare separator joins fragments.
TECHNICAL_LITERALS = frozenset({"", " · "})

# Lowercase identifiers are enum values, mapping keys, and reason tokens, not
# prose; a visible label is capitalised or contains whitespace.
CODE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

# A finished Track's status, not an Import action. Only this exact detail value
# is exempt; any copy edit, or the same words under an action key, is scanned.
STATUS_DETAIL_VALUES = frozenset({"Track added"})

# The glossary's avoided wording across every copy-bearing term. Download and
# Upload stay out because the verb list uses word boundaries.
ENGLISH_AVOIDED = re.compile(
    r"\b(?:load|loads|loaded|loading|add|adds|added|adding|ingest|ingests"
    r"|ingested|ingesting)\b"
    r"|\b(?:bulk|mass)\s+export\b"
    r"|\bexport\s+stems\b"
    r"|\bUI\s+language\b"
    r"|\blanguage\s+pack\b"
    r"|\braw\s+file\b"
    r"|\bsource\s+file\b"
    r"|\bpassthrough\s+export\b"
    r"|\bnative\s+export\b"
    r"|\braw\s+export\b",
    re.IGNORECASE,
)

# The renderings documented in ``docs/i18n/ro/terms.md``. ``pistă audio`` stays
# legal because it names an audio stream, not a Track or Stem.
ROMANIAN_AVOIDED = re.compile(
    r"(?:"
    r"\bartefact\w*"
    r"|\bexport\s+masiv\b"
    r"|\bsalveaz[ăa]\s+originalul\b"
    r"|\bjob\b"
    r"|\bfi[șs]ier(?:ul)?\s+(?:brut|surs[ăa])\b"
    r"|(?<!audio )\bpist[ăa]\b(?!\s+audio)"
    r"|\b(?:încarc|încărc|adaug|adăug|ingere|inger)\w*"
    r")",
    re.IGNORECASE,
)

# The avoided forms the term table must keep documenting, so the scan and the
# translation contract cannot drift apart.
DOCUMENTED_AVOIDED = (
    "pistă",
    "încarcă",
    "adaugă",
    "ingerează",
    "artefact",
    "export masiv",
    "Salvează originalul",
    "job",
    "fișier brut",
    "fișier sursă",
)

# ``formatting`` keeps helpers, the error-copy mapping, and this layout cap; it
# must not define or re-export prose.
FORMATTING_NON_COPY_CONSTANTS = frozenset({"MAX_HEADLINE_LENGTH"})

CEDILLA = frozenset("şŞţŢ")

ARIA_LABEL_IN_PROPS = re.compile(r"\baria-label=([^,;]+)")
IMAGE_ALT = re.compile(r'\balt="([^"]*)"')


def _copy_sink(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if not isinstance(func, ast.Attribute):
        return None
    if isinstance(func.value, ast.Name):
        if func.value.id == "ui":
            return f"ui.{func.attr}"
        if func.value.id == "preflight" and func.attr == "NativeChrome":
            return "preflight.NativeChrome"
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
        if key.startswith("job.detail.") and text in STATUS_DETAIL_VALUES:
            continue
        if CODE_IDENTIFIER.fullmatch(text):
            continue
        for match in ENGLISH_AVOIDED.finditer(text):
            violations.append((key, match.group(0)))
    return violations


def _load_catalog(path: Path) -> Mapping[str, object]:
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _formatting_string_constants() -> dict[str, str]:
    """Module-level string assignments in ``ui/formatting.py``, by name."""
    path = UI / "formatting.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants: dict[str, str] = {}
    for node in tree.body:
        value: ast.expr | None = None
        names: list[str] = []
        if isinstance(node, ast.Assign):
            value = node.value
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            names = [node.target.id]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for name in names:
                constants[name] = value.value
    return constants


def _formatting_constant_references(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name) or node.value.id != "formatting":
            continue
        if node.attr.isupper():
            names.add(node.attr)
    return names


def test_components_have_no_prose_literals_at_copy_sinks() -> None:
    violations: list[str] = []
    for path in COMPONENT_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for line, sink, text in _literal_copy(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
        for line, sink, text in _props_literals(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
        for line, sink, text in _alt_literals(tree):
            violations.append(f"{relative}:{line}: {sink} takes {text!r}")
    assert not violations, (
        "Component copy resolves through i18n.t and the English catalogue; "
        "render a catalogue key instead of an inline literal.\n" + "\n".join(violations)
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
        "Page-shell copy resolves through i18n.t and the English catalogue; "
        "render a catalogue key instead of an inline literal.\n" + "\n".join(violations)
    )


def test_preflight_chrome_and_fatal_copy_have_no_prose_literals() -> None:
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"), filename=str(ENTRY))
    assert _literal_copy(tree) == []


def test_every_runtime_catalog_key_exists_in_both_locales() -> None:
    catalogs = {
        "en.json": _load_catalog(ENGLISH_CATALOG),
        "ro.json": _load_catalog(ROMANIAN_CATALOG),
    }
    violations: list[str] = []
    for path in RUNTIME_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for name, catalog in catalogs.items():
            for line, key in _missing_catalog_keys(tree, catalog):
                violations.append(f"{relative}:{line}: {key!r} is missing from {name}")
    assert not violations, (
        "Every i18n.t key used at runtime must exist in both locale catalogues.\n"
        + "\n".join(violations)
    )


def test_the_formatting_module_defines_no_visible_copy() -> None:
    assert _formatting_string_constants() == {}


def test_runtime_sources_reference_no_formatting_copy_constant() -> None:
    violations: list[str] = []
    for path in RUNTIME_SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(REPO_ROOT).as_posix()
        for name in sorted(_formatting_constant_references(tree)):
            if name not in FORMATTING_NON_COPY_CONSTANTS:
                violations.append(f"{relative}: formatting.{name}")
    assert not violations, (
        "User-visible copy lives in the catalogues; formatting.py owns helpers only.\n"
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


def test_the_scan_rejects_a_literal_preflight_chrome_value() -> None:
    tree = ast.parse('preflight.NativeChrome(fatal_title="StemForge can\'t start")\n')

    assert _literal_copy(tree) == [
        (1, "preflight.NativeChrome fatal_title", "StemForge can't start")
    ]


def test_the_scan_accepts_catalog_keys_and_interpolated_accessibility_text() -> None:
    tree = ast.parse(
        "ui.label(i18n.t('page.import_audio'))\n"
        "ui.page_title(i18n.t('app.name'))\n"
        "ui.switch(i18n.t('page.youtube_setting'))\n"
        "ui.expansion(i18n.t('details.label'))\n"
        "ui.element('section').props(f'aria-label={i18n.t(\"page.transport_aria_label\")}')\n"
        "preflight.NativeChrome(fatal_title=i18n.t('preflight.fatal_title'))\n"
    )

    assert _literal_copy(tree) == []
    assert _props_literals(tree) == []
    assert _alt_literals(tree) == []


def test_a_missing_catalog_key_is_rejected() -> None:
    tree = ast.parse("i18n.t('page.no_such_message')\n")

    assert _missing_catalog_keys(tree, {"app.name": "StemForge"}) == [(1, "page.no_such_message")]
    assert _missing_catalog_keys(tree, {"page.no_such_message": "Present"}) == []


def test_the_english_catalog_uses_the_glossary_vocabulary() -> None:
    violations = _catalog_synonyms(_load_catalog(ENGLISH_CATALOG))
    assert not violations, (
        "The English catalogue must use the glossary's terms, not their avoided synonyms.\n"
        + "\n".join(f"en.json {key}: {word!r}" for key, word in violations)
    )


def test_the_romanian_catalog_uses_no_avoided_rendering() -> None:
    violations = [
        (key, match.group(0))
        for key, text in _catalog_values(_load_catalog(ROMANIAN_CATALOG))
        for match in ROMANIAN_AVOIDED.finditer(text)
    ]
    assert not violations, "\n".join(f"ro.json {key}: {word!r}" for key, word in violations)


def test_the_romanian_catalog_has_no_cedilla_diacritics() -> None:
    text = "".join(value for _, value in _catalog_values(_load_catalog(ROMANIAN_CATALOG)))

    assert not (set(text) & CEDILLA)


def test_the_terms_table_documents_every_avoided_rendering() -> None:
    text = TERMS_TABLE.read_text(encoding="utf-8")

    missing = [term for term in DOCUMENTED_AVOIDED if term not in text]
    assert not missing, f"terms.md must document the scanned avoided forms: {missing}"


def test_the_catalog_synonym_scan_rejects_load() -> None:
    assert _catalog_synonyms({"page.audio": "Load audio"}) == [("page.audio", "Load")]


def test_the_status_phrase_exemption_does_not_excuse_action_copy() -> None:
    assert _catalog_synonyms({"job.detail.youtube.track_added": "Track added"}) == []
    assert _catalog_synonyms({"page.action": "Track added"}) == [("page.action", "added")]
    assert _catalog_synonyms({"job.detail.youtube.track_added": "Add track"}) == [
        ("job.detail.youtube.track_added", "Add")
    ]


def test_the_romanian_scan_rejects_a_term_and_allows_an_audio_stream() -> None:
    assert ROMANIAN_AVOIDED.search("adaugă piesa")
    assert ROMANIAN_AVOIDED.search("fișier sursă")
    assert ROMANIAN_AVOIDED.search("Acest fișier nu are pistă audio.") is None
