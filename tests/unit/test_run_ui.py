"""The launch seam: the window opens in native mode with the right shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge import __main__
from stemforge.integration.window import MIN_WINDOW_SIZE, configure_window
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
    assert len(startup) == 1
    assert len(shutdown) == 1
