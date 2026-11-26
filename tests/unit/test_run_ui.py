"""The launch seam: the window opens in native mode with the right shape."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
import webview

from stemforge import __main__, i18n
from stemforge.integration.window import MIN_WINDOW_SIZE, WEBKIT_DMABUF_ENV, configure_window
from stemforge.platform.paths import AppPaths
from stemforge.platform.preflight import PreflightReport
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit


def test_window_configuration_sets_the_minimum_and_no_downloads(tmp_path: Path) -> None:
    from nicegui import app

    paths = AppPaths.for_home(tmp_path / "home").ensure()

    configure_window(paths)

    assert MIN_WINDOW_SIZE == (1024, 700)
    assert app.native.window_args["min_size"] == MIN_WINDOW_SIZE
    assert app.native.window_args["resizable"] is True
    expected_icon = "stemforge.ico" if sys.platform.startswith("win") else "stemforge-512.png"
    assert Path(app.native.start_args["icon"]).name == expected_icon
    assert "icon" not in app.native.window_args  # pywebview 6 takes it on start()
    start_parameters = inspect.signature(webview.start).parameters
    assert set(app.native.start_args) <= set(start_parameters)
    assert app.native.start_args["private_mode"] is False
    assert app.native.start_args["storage_path"] == str(paths.webview)
    assert app.native.settings["ALLOW_DOWNLOADS"] is False


@pytest.mark.parametrize(("existing", "expected"), [(None, "1"), ("0", "0")])
def test_linux_window_disables_the_webkit_dmabuf_renderer(
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
        monkeypatch.delenv(WEBKIT_DMABUF_ENV, raising=False)
    else:
        monkeypatch.setenv(WEBKIT_DMABUF_ENV, existing)

    configure_window(paths)

    assert os.environ[WEBKIT_DMABUF_ENV] == expected


def test_linux_window_does_not_set_the_old_shared_memory_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The app sets one guard; a leftover FORCE_SHM export belongs to the user."""
    import sys

    legacy = "WEBKIT_DMABUF_RENDERER_FORCE_SHM"
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv(WEBKIT_DMABUF_ENV, raising=False)
    monkeypatch.delenv(legacy, raising=False)

    configure_window(paths)

    assert legacy not in os.environ


def test_windows_window_does_not_set_the_linux_webkit_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(WEBKIT_DMABUF_ENV, raising=False)

    configure_window(paths)

    assert WEBKIT_DMABUF_ENV not in os.environ


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
    assert captured["language"] == "en-GB"
    assert app.config.language == "en-GB"
    assert captured["storage_secret"] == paths.storage_secret()
    assert len(str(captured["storage_secret"])) == 64
    context = captured["context"]
    assert isinstance(context, page.UiContext)
    assert context.show_recovery is False
    assert context.preflight is report
    assert context.diagnostics is not None
    assert context.clipboard is not None
    assert len(startup) == 1
    assert len(shutdown) == 1


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"LC_ALL": "ro_RO.UTF-8"}, "ro"),
        ({"LC_ALL": "en_US.UTF-8"}, "en"),
    ],
)
def test_startup_resolves_the_system_preference_from_the_host_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected: str,
) -> None:
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(name, environment.get(name, ""))
    settings = SettingsStore(AppPaths.for_home(tmp_path / "home").ensure())

    __main__._initialize_language(settings)

    assert i18n.preference() == "system"
    assert i18n.language() == expected


def test_run_ui_uses_the_persisted_language_and_passes_it_to_nicegui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nicegui import app, ui

    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    settings.set_language("ro")
    captured: dict[str, object] = {}
    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)

    assert __main__._run_ui(paths, PreflightReport(checks=()), settings=settings) == 0

    assert i18n.language() == "ro"
    assert app.config.language == "ro"
    assert captured["language"] == "ro"


def test_the_page_language_attribute_follows_the_framework_language() -> None:
    from nicegui import Client, app

    from stemforge.ui import page

    i18n.set_language("ro", system_locale="ro_RO.UTF-8")
    app.config.language = i18n.framework_language(i18n.language())

    assert Client.page_routes.pages[page.index].resolve_language() == "ro"


def test_native_window_uses_the_ico_icon_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NiceGUI's Windows icon path calls LoadImageW, which needs the .ico."""
    from nicegui import app, ui

    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    captured: dict[str, object] = {}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0
    favicon = captured["favicon"]
    assert isinstance(favicon, Path)
    assert favicon.name == "stemforge.ico"


def test_native_window_uses_the_png_icon_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pywebview's GTK/QT window icon takes the raster mark, not the ICO."""
    from nicegui import app, ui

    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    captured: dict[str, object] = {}
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: None)

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0
    favicon = captured["favicon"]
    assert isinstance(favicon, Path)
    assert favicon.name == "stemforge-512.png"


def test_run_ui_connects_the_separation_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production service must not report ``internal.unexpected``."""
    from nicegui import app, ui

    from stemforge import errors
    from stemforge.domain.models import OriginKind, Source, SourceMetadata, Track
    from stemforge.engines.separation import WorkerSeparationEngine
    from stemforge.errors import StemForgeError
    from stemforge.ui import page

    paths = AppPaths.for_home(tmp_path / "home").ensure()
    captured: dict[str, object] = {}
    monkeypatch.setattr(ui, "run", lambda **kwargs: captured.update(kwargs))
    monkeypatch.setattr(app, "on_startup", lambda callback: None)
    monkeypatch.setattr(app, "on_shutdown", lambda callback: None)
    monkeypatch.setattr(page, "configure", lambda context: captured.setdefault("context", context))

    assert __main__._run_ui(paths, PreflightReport(checks=())) == 0

    context = captured["context"]
    assert isinstance(context, page.UiContext)
    assert isinstance(context.service.engines.separation, WorkerSeparationEngine)

    track = Track(
        track_id="track",
        source=Source(
            source_id="source",
            origin="/music/song.wav",
            origin_kind=OriginKind.FILE,
            metadata=SourceMetadata(title="Song"),
        ),
        title="Song",
    )
    with pytest.raises(StemForgeError) as caught:
        context.service.separate(track)
    # Without the engine this is internal.unexpected; the model gate is the
    # first legitimate refusal once separation is wired.
    assert caught.value.code == errors.SEPARATION_MODEL_MISSING


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
    assert captured["storage_secret"] == paths.storage_secret()
    assert "window_size" not in captured
