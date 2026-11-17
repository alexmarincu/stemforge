"""The launch seam: the window opens in native mode with the right shape."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from stemforge import __main__
from stemforge.integration.window import MIN_WINDOW_SIZE, WEBKIT_SHM_ENV, configure_window
from stemforge.platform.paths import AppPaths
from stemforge.platform.preflight import PreflightReport

pytestmark = pytest.mark.unit


def test_window_configuration_sets_the_minimum_and_no_downloads(tmp_path: Path) -> None:
    from nicegui import app

    paths = AppPaths.for_home(tmp_path / "home").ensure()

    configure_window(paths)

    assert MIN_WINDOW_SIZE == (1024, 700)
    assert app.native.window_args["min_size"] == MIN_WINDOW_SIZE
    assert app.native.window_args["resizable"] is True
    assert app.native.start_args["private_mode"] is False
    assert app.native.start_args["storage_path"] == str(paths.webview)
    assert app.native.settings["ALLOW_DOWNLOADS"] is False


@pytest.mark.parametrize(("existing", "expected"), [(None, "1"), ("0", "0")])
def test_linux_window_forces_the_webkit_shared_memory_renderer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing: str | None,
    expected: str,
) -> None:
    """WebKitGTK 2.52+ paints a white view when GBM allocation fails (NVIDIA/X11)."""
    import sys

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    monkeypatch.setattr(sys, "platform", "linux")
    if existing is None:
        monkeypatch.delenv(WEBKIT_SHM_ENV, raising=False)
    else:
        monkeypatch.setenv(WEBKIT_SHM_ENV, existing)

    configure_window(paths)

    assert os.environ[WEBKIT_SHM_ENV] == expected


def test_windows_window_does_not_set_the_linux_webkit_workaround(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(WEBKIT_SHM_ENV, raising=False)

    configure_window(paths)

    assert WEBKIT_SHM_ENV not in os.environ


def test_windows_window_asserts_the_edge_chromium_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from nicegui import app

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(app.native.start_args, "gui", "unset")

    configure_window(paths)

    assert app.native.start_args["gui"] == "edgechromium"


def test_run_ui_opens_a_native_window_without_reload_or_close_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nicegui import app, ui

    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    captured: dict[str, object] = {}
    startup: list[object] = []
    shutdown: list[object] = []

    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", startup.append)
    monkeypatch.setattr(app, "on_shutdown", shutdown.append)
    monkeypatch.setattr(page, "configure", lambda context: captured.setdefault("context", context))

    report = PreflightReport(checks=())
    assert __main__._run_ui(paths, report) == 0

    assert captured["native"] is True
    assert captured["reload"] is False
    assert captured["show"] is False
    assert captured["window_size"] == (1280, 820)
    assert captured["title"] == page.TITLE
    context = captured["context"]
    assert isinstance(context, page.UiContext)
    assert context.show_recovery is False
    assert context.preflight is report
    assert context.diagnostics is not None
    assert context.clipboard is not None
    assert len(startup) == 1
    assert len(shutdown) == 1


def test_run_ui_enforces_the_cache_cap_after_the_sweep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nicegui import app, ui

    from stemforge.application import WorkspaceService
    from stemforge.artifacts import EvictionReport, SweepReport
    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    calls: list[str] = []

    monkeypatch.setattr(ui, "run", lambda **kwargs: None)
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)
    monkeypatch.setattr(
        WorkspaceService,
        "sweep_stale",
        lambda self: calls.append("sweep") or SweepReport(),
    )
    monkeypatch.setattr(
        WorkspaceService,
        "enforce_cache_cap",
        lambda self: calls.append("cap") or EvictionReport((), 0, 0, 0),
    )

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0
    assert calls == ["sweep", "cap"]


def test_run_ui_survives_a_cache_cap_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nicegui import app, ui

    from stemforge import errors
    from stemforge.application import WorkspaceService
    from stemforge.errors import StemForgeError
    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()

    def fail(self: WorkspaceService) -> None:
        raise StemForgeError(errors.STORAGE_CACHE_FULL, "cache is full")

    monkeypatch.setattr(ui, "run", lambda **kwargs: None)
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)
    monkeypatch.setattr(WorkspaceService, "enforce_cache_cap", fail)

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0


def test_run_ui_browser_mode_opens_the_web_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nicegui import app, ui

    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    captured: dict[str, object] = {}

    monkeypatch.setenv("STEMFORGE_BROWSER", "1")
    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)

    report = PreflightReport(checks=())
    assert __main__._run_ui(paths, report) == 0

    assert captured["native"] is False
    assert captured["show"] is True
    assert captured["host"] == "127.0.0.1"
    assert captured["reload"] is False
    assert "window_size" not in captured
