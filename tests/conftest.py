"""Shared test configuration.

``STEMFORGE_HOME`` is redirected to a session temporary directory before the first
``stemforge`` import, so no test can write to a real user profile.
"""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest

_SESSION_TMP = tempfile.TemporaryDirectory(prefix="stemforge-tests-")
SESSION_HOME = Path(_SESSION_TMP.name)
os.environ["STEMFORGE_HOME"] = str(SESSION_HOME)

from stemforge.platform.paths import AppPaths  # noqa: E402

LAYER_MARKERS = frozenset({"unit", "integration", "ui"})


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    """A per-test ``AppPaths`` rooted inside ``tmp_path``."""
    return AppPaths.for_home(tmp_path / "home").ensure()


@pytest.fixture(autouse=True)
def _block_network_in_unit_tests(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any unit test that opens a network connection."""
    if request.node.get_closest_marker("unit") is None:
        return

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is forbidden in unit tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Every collected test must carry exactly one layer marker."""
    problems: list[str] = []
    for item in items:
        layers = LAYER_MARKERS.intersection(marker.name for marker in item.iter_markers())
        if len(layers) != 1:
            label = ", ".join(sorted(layers)) if layers else "none"
            problems.append(f"{item.nodeid} [{label}]")
    if problems:
        raise pytest.UsageError(
            "every test needs exactly one of the unit, integration, or ui markers; "
            f"offending tests: {'; '.join(problems)}"
        )
