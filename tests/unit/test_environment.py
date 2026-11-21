"""``platform.environment.configure()``: paths, env vars, and directory creation."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from stemforge.platform.environment import configure
from stemforge.platform.paths import AppPaths

ENV_KEYS = (
    "NICEGUI_STORAGE_PATH",
    "HF_HOME",
    "HF_HUB_DISABLE_SYMLINKS_WARNING",
    "HF_HUB_DISABLE_TELEMETRY",
    "DENO_DIR",
    "TMPDIR",
    "TEMP",
    "TMP",
    "GST_PLUGIN_PATH",
    "GST_PLUGIN_SYSTEM_PATH",
    "GST_REGISTRY",
)


@pytest.fixture(autouse=True)
def _restore_environment() -> Iterator[None]:
    before = {key: os.environ.get(key) for key in ENV_KEYS}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.mark.unit
def test_configure_creates_paths_and_sets_the_pre_import_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STEMFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NICEGUI_STORAGE_PATH", "stale-value")

    paths = configure()

    expected = AppPaths.for_home(tmp_path / "home").ensure()
    assert paths == expected
    for directory in paths.roots:
        assert directory.is_dir()
    assert os.environ["NICEGUI_STORAGE_PATH"] == str(paths.config / "nicegui")
    assert os.environ["HF_HOME"] == str(paths.models)
    assert os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["DENO_DIR"] == str(paths.cache / "deno")
    assert os.environ["TMPDIR"] == str(paths.cache / "tmp")
    assert os.environ["TEMP"] == str(paths.cache / "tmp")
    assert os.environ["TMP"] == str(paths.cache / "tmp")
    assert (paths.config / "nicegui").is_dir()
    assert (paths.cache / "tmp").is_dir()
    assert (paths.cache / "deno").is_dir()
    assert (paths.cache / "yt-dlp").is_dir()


@pytest.mark.unit
def test_configure_restores_the_host_gstreamer_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = tmp_path / "_internal"
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("STEMFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "GST_PLUGIN_PATH", os.pathsep.join([str(bundle), str(bundle / "gst_plugins")])
    )
    monkeypatch.setenv("GST_PLUGIN_SYSTEM_PATH", "")
    monkeypatch.setenv("GST_REGISTRY", str(bundle / "registry.bin"))

    configure()

    assert "GST_PLUGIN_PATH" not in os.environ
    assert "GST_PLUGIN_SYSTEM_PATH" not in os.environ
    assert "GST_REGISTRY" not in os.environ


@pytest.mark.unit
def test_configure_keeps_a_host_gstreamer_plugin_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setenv("STEMFORGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GST_PLUGIN_PATH", "/opt/stemforge-plugins")

    configure()

    assert os.environ["GST_PLUGIN_PATH"] == "/opt/stemforge-plugins"


@pytest.mark.unit
def test_configure_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STEMFORGE_HOME", str(tmp_path / "home"))

    first = configure()
    second = configure()

    assert second == first
