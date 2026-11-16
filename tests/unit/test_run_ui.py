"""The launch seam: the window opens in native mode with the right shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge import __main__
from stemforge.platform.paths import AppPaths
from stemforge.platform.preflight import PreflightReport

pytestmark = pytest.mark.unit


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

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0

    assert captured["native"] is True
    assert captured["reload"] is False
    assert captured["show"] is False
    assert captured["window_size"] == (1280, 820)
    assert captured["title"] == page.TITLE
    context = captured["context"]
    assert isinstance(context, page.UiContext)
    assert context.show_recovery is False
    assert len(startup) == 1
    assert len(shutdown) == 1
