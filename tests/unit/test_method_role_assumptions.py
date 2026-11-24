"""No method-aware path treats one Demucs role set as every Separation's shape.

A Separation set is built from its method's descriptor. The Demucs vocabulary
stays available for legacy manifests and the Demucs block path, so those
modules are named explicitly here; a new global use anywhere else fails the
audit rather than silently reintroducing four Stems or five outputs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMUCS_ONLY_NAMES = frozenset(
    {
        "NON_VOCAL_ROLES",
        "OUTPUT_ROLES",
        "SEPARATION_OUTPUT_ROLES",
        "STEM_NAMES",
        "STEM_ROLES",
        "WORKER_OUTPUT_NAMES",
        "WORKER_OUTPUTS",
    }
)

# Each entry is a module where the Demucs vocabulary is the subject, not a
# global contract: the registry itself, the legacy wire copy, the Demucs block
# writer, legacy export keys, and the contract test that pins the old names.
DEMUCS_SCOPED_PATHS = {
    "src/stemforge/domain/roles.py",
    "src/stemforge/pipeline/export_job.py",
    "src/stemforge/worker_protocol.py",
    "src/stemforge/workers/separation.py",
    "tests/unit/test_domain.py",
}


def _python_files() -> list[Path]:
    paths = []
    for root in (REPO_ROOT / "src" / "stemforge", REPO_ROOT / "tests"):
        paths.extend(sorted(root.rglob("*.py")))
    return paths


def _demucs_names(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DEMUCS_ONLY_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in DEMUCS_ONLY_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in DEMUCS_ONLY_NAMES:
                    found.add(alias.name)
    return found


def test_no_method_aware_module_reuses_the_demucs_role_vocabulary() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in DEMUCS_SCOPED_PATHS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _demucs_names(tree)
        if names:
            offenders[relative] = sorted(names)

    assert offenders == {}, (
        "use the method descriptor's output_roles/stems instead of a global Demucs role tuple"
    )


def test_every_demucs_scoped_path_still_exists() -> None:
    missing = sorted(
        relative for relative in DEMUCS_SCOPED_PATHS if not (REPO_ROOT / relative).is_file()
    )
    assert missing == []
