"""The package dependency rule, enforced against the source tree.

`lint-imports` is the CI gate; these tests keep the two edges that drifted
(workers on the top layer, UI reaching into platform) from silently returning.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "src" / "stemforge"

# ADR 0013: the UI keeps importing the facade and view types from
# ``stemforge.application``; the package's internals stay private.
APPLICATION_INTERNALS = (
    "stemforge.application.collaborators",
    "stemforge.application.identity",
    "stemforge.application.renders",
    "stemforge.application.service",
    "stemforge.application.state",
    "stemforge.application.views",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _violations(directory: Path, forbidden: tuple[str, ...]) -> list[str]:
    problems: list[str] = []
    for path in sorted(directory.rglob("*.py")):
        for module in _imports(path):
            if any(module == item or module.startswith(f"{item}.") for item in forbidden):
                problems.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    return problems


def test_ui_imports_only_the_ui_layer_or_below() -> None:
    problems = _violations(
        PACKAGE / "ui",
        (
            "stemforge.platform",
            "stemforge.engines",
            "stemforge.workers",
            "stemforge.pipeline",
            "stemforge.jobs",
        ),
    )
    assert problems == []


def test_ui_imports_only_the_application_facade() -> None:
    assert _violations(PACKAGE / "ui", APPLICATION_INTERNALS) == []


def test_workers_import_only_their_own_lower_layer() -> None:
    problems = _violations(
        PACKAGE / "workers",
        (
            "stemforge.application",
            "stemforge.pipeline",
            "stemforge.engines",
            "stemforge.jobs",
            "stemforge.artifacts",
            "stemforge.domain",
            "stemforge.models",
            "stemforge.settings",
            "stemforge.integration",
            "stemforge.ui",
        ),
    )
    assert problems == []


def test_the_import_contract_declares_the_same_edges() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]
    layers = next(item["layers"] for item in contracts if item["type"] == "layers")
    assert layers[0] == "stemforge.ui"
    assert "stemforge.domain | stemforge.workers" in layers

    forbidden = {item["name"]: item for item in contracts if item["type"] == "forbidden"}
    ui = forbidden["UI never imports engines, workers, pipeline, or jobs"]
    assert "stemforge.platform" in ui["forbidden_modules"]
    facade = forbidden["UI talks to the application facade only"]
    assert set(facade["forbidden_modules"]) == set(APPLICATION_INTERNALS)
    workers = forbidden["Workers stay below the app"]
    assert "stemforge.pipeline" in workers["forbidden_modules"]
    assert "stemforge.application" in workers["forbidden_modules"]
