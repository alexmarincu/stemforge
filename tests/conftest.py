"""Shared test configuration.

``STEMFORGE_HOME`` is redirected to a session temporary directory before the first
``stemforge`` import, so no test can write to a real user profile.
"""

from __future__ import annotations

import os
import socket
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_SESSION_TMP = tempfile.TemporaryDirectory(prefix="stemforge-tests-")
SESSION_HOME = Path(_SESSION_TMP.name)
os.environ["STEMFORGE_HOME"] = str(SESSION_HOME)

from stemforge.platform.paths import AppPaths  # noqa: E402

LAYER_MARKERS = frozenset({"unit", "integration", "ui"})

# Windows emulates ``socket.socketpair`` (asyncio's event-loop self-pipe) by
# connecting to a loopback listener, so the guard has to allow local transports
# while still failing any connection that leaves the machine.
_LOCAL_HOSTS = frozenset({"localhost", "::1", "0.0.0.0", "::"})


def _is_local_address(address: object) -> bool:
    """True for AF_UNIX paths and loopback TCP peers."""
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        return isinstance(host, str) and (host in _LOCAL_HOSTS or host.startswith("127."))
    return False


def _install_network_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any non-local ``connect``/``connect_ex`` on ``socket.socket``."""

    def guard(real: Callable[..., Any]) -> Callable[..., Any]:
        def connect(self: socket.socket, address: object, *args: object, **kwargs: object) -> Any:
            if _is_local_address(address):
                return real(self, address, *args, **kwargs)
            raise RuntimeError("network access is forbidden in tests")

        return connect

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))


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
    _install_network_guard(monkeypatch)


@pytest.fixture
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in guard for non-unit layers (UI tests that must stay offline)."""
    _install_network_guard(monkeypatch)


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
