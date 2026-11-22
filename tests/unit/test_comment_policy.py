"""The comment policy: every comment states its reason in place (ADR 0014).

A pointer to a document is not a reason: it sends the reader away from the line
that needs it. These assertions fail on a work-item citation, on a section sign
that is not part of a licence reference, and on a collaborator docstring that
repeats the facade contract.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
APPLICATION = REPO_ROOT / "src" / "stemforge" / "application"

# A work item is not a location in this repository; nothing here can resolve
# one. The policy is about pointers, not about the words.
FORBIDDEN = (
    (re.compile(r"\btickets?[\s_-]+#?\d", re.IGNORECASE), "ticket citation"),
    (re.compile(r"\bissues?\s+#\d", re.IGNORECASE), "issue citation"),
    (re.compile(r"\bresearch[\s_-]+\d", re.IGNORECASE), "research-note citation"),
    (re.compile(r"\bCONTEXT\.md\b"), "CONTEXT.md pointer"),
)

# A section sign is only legitimate in a licence reference, where it names a
# statute clause (GPL-3.0 §6(b)); anywhere else it is a work-item section.
LICENCE_MARKER = re.compile(r"\b(?:A?GPL|LGPL)\b")

SCANNED_SUFFIXES = frozenset(
    {".py", ".spec", ".sh", ".ps1", ".yml", ".toml", ".json", ".iss", ".md"}
)
SCANNED_DIRECTORIES = ("src", "tests", "scripts", "packaging", ".github", "docs", "LICENSES")
# README and GLOSSARY are the public prose; a work item there is worse than in a
# comment. LICENSE, THIRD_PARTY_NOTICES.md and SOURCE_OFFER.md are generator
# output and are already proven byte-for-byte by `generate_notices.py --check`.
SCANNED_FILES = ("pyproject.toml", ".gitignore", "README.md", "GLOSSARY.md")

# Rendered from packaging/*.lock.json by scripts/generate_notices.py and
# compared against the committed tree in CI.
GENERATED_PREFIX = "LICENSES/"

# Describes the agent skill system, so its vocabulary is its subject matter.
AGENT_DOCS_PREFIX = "docs/agents/"

# Below this, two identical sentences are a naming coincidence rather than a
# contract copied into the wrong layer: "The Track." is a label, not a rule.
MINIMUM_SHARED_CONTRACT = 30

# This file spells out the patterns it forbids, so it is the one file the scan
# must skip.
POLICY_TEST = Path(__file__).resolve()


def _scanned_files() -> list[Path]:
    files = [REPO_ROOT / name for name in SCANNED_FILES]
    for directory in SCANNED_DIRECTORIES:
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if path.resolve() == POLICY_TEST:
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative.startswith(GENERATED_PREFIX):
                continue
            if relative.startswith(AGENT_DOCS_PREFIX):
                continue
            files.append(path)
    return files


def test_scanned_files_exist() -> None:
    missing = [name for name in SCANNED_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, f"the comment policy no longer scans {missing}: restore or drop them"


def test_no_work_item_citations() -> None:
    violations: list[str] = []
    for path in _scanned_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, label in FORBIDDEN:
                for match in pattern.finditer(line):
                    relative = path.relative_to(REPO_ROOT).as_posix()
                    violations.append(f"{relative}:{number}: {label}: {match.group(0)!r}")
    assert not violations, (
        "Work-item citations cannot be resolved from this repository; state the "
        "reason in place instead (ADR 0014).\n" + "\n".join(violations)
    )


def test_section_signs_are_licence_references() -> None:
    violations: list[str] = []
    for path in _scanned_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "§" not in line or LICENCE_MARKER.search(line):
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            violations.append(f"{relative}:{number}: {line.strip()[:120]}")
    assert not violations, (
        "A section sign must name a licence clause (GPL-3.0 §6(b)); a work-item "
        "section is a citation (ADR 0014).\n" + "\n".join(violations)
    )


def test_generated_exemption_is_generator_output() -> None:
    """The exemption may only cover paths the generator actually writes."""
    exempt = [path for path in (REPO_ROOT / GENERATED_PREFIX).rglob("*") if path.is_file()]
    assert exempt, f"nothing lives under {GENERATED_PREFIX!r}; drop the exemption"
    output_roots = _generator_output_roots()
    assert GENERATED_PREFIX.split("/")[0] in output_roots, (
        f"{GENERATED_PREFIX!r} is exempt from the comment policy but "
        f"scripts/generate_notices.py no longer writes under it "
        f"(found {sorted(output_roots)}); drop the exemption."
    )


def _generator_output_roots() -> set[str]:
    tree = ast.parse((REPO_ROOT / "scripts" / "generate_notices.py").read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"write", "write_bytes"} or not node.args:
            continue
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            roots.add(argument.value.split("/")[0])
        elif isinstance(argument, ast.JoinedStr) and argument.values:
            head = argument.values[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                roots.add(head.value.split("/")[0])
    return roots


def test_facade_does_not_repeat_collaborator_contracts() -> None:
    """One text, one home: the UI sees the facade, so the facade owns the contract."""
    facade = _contracts(APPLICATION / "service.py")
    shared: list[str] = []
    for path in sorted((APPLICATION / "collaborators").glob("*.py")):
        for name, text in _contracts(path).items():
            if name in facade and facade[name] == text and len(text) >= MINIMUM_SHARED_CONTRACT:
                shared.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{name}: {text!r}")
    assert not shared, (
        "The facade and a collaborator state the same contract; the copy will "
        "drift. Keep the contract on the facade and leave the collaborator only "
        "what is specific to its mechanism.\n" + "\n".join(shared)
    )


def _contracts(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    contracts: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name.startswith("_"):
            continue
        docstring = ast.get_docstring(node, clean=True)
        if docstring:
            contracts[node.name] = " ".join(docstring.split())
    return contracts
