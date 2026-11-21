"""``AppPaths``: per-OS roots, the ``STEMFORGE_HOME`` override, and ``ensure()``."""

from __future__ import annotations

import dataclasses
import os
import stat
import sys
from pathlib import Path

import pytest

from stemforge.platform.paths import AppPaths


@pytest.mark.unit
@pytest.mark.platform_linux
@pytest.mark.skipif(sys.platform != "linux", reason="XDG roots are a Linux contract")
def test_linux_roots_follow_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STEMFORGE_HOME", raising=False)
    data_home = tmp_path / "xdg-data"
    config_home = tmp_path / "xdg-config"
    cache_home = tmp_path / "xdg-cache"
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    paths = AppPaths.from_environment(platform="linux")

    assert paths.data == data_home / "stemforge"
    assert paths.config == config_home / "stemforge"
    assert paths.cache == cache_home / "stemforge"
    assert paths.logs == state_home / "stemforge" / "log"
    assert paths.models == paths.data / "models"
    assert paths.webview == paths.data / "webview"


@pytest.mark.unit
def test_windows_roots_use_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEMFORGE_HOME", raising=False)
    base = Path("/win/LocalAppData")
    calls: list[tuple[str, bool | str | None]] = []

    def fake(appname: str, appauthor: bool | str | None = None) -> str:
        calls.append((appname, appauthor))
        return str(base / appname)

    def fake_cache(appname: str, appauthor: bool | str | None = None) -> str:
        calls.append((appname, appauthor))
        return str(base / appname / "Cache")

    def fake_logs(appname: str, appauthor: bool | str | None = None) -> str:
        calls.append((appname, appauthor))
        return str(base / appname / "Logs")

    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_data_dir", fake)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_config_dir", fake)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_cache_dir", fake_cache)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_log_dir", fake_logs)

    paths = AppPaths.from_environment(platform="win32")

    assert paths.data == base / "StemForge"
    assert paths.config == paths.data
    assert paths.cache == paths.data / "Cache"
    assert paths.logs == paths.data / "Logs"
    assert paths.models == paths.data / "models"
    assert paths.webview == paths.data / "webview"
    assert calls
    assert {appname for appname, _ in calls} == {"StemForge"}
    assert all(appauthor is False for _, appauthor in calls)


@pytest.mark.unit
def test_stemforge_home_keeps_every_root_under_the_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "portable"
    monkeypatch.setenv("STEMFORGE_HOME", str(home))

    def unexpected(*args: object, **kwargs: object) -> str:
        raise AssertionError("platformdirs must not be consulted when STEMFORGE_HOME is set")

    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_data_dir", unexpected)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_config_dir", unexpected)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_cache_dir", unexpected)
    monkeypatch.setattr("stemforge.platform.paths.platformdirs.user_log_dir", unexpected)

    paths = AppPaths.from_environment()

    assert paths.data == home / "data"
    assert paths.config == home / "config"
    assert paths.cache == home / "cache"
    assert paths.logs == home / "logs"
    assert paths.models == home / "data" / "models"
    assert paths.webview == home / "data" / "webview"
    for root in paths.roots:
        assert root.is_relative_to(home)


@pytest.mark.unit
def test_app_paths_do_not_create_directories_on_construction(tmp_path: Path) -> None:
    home = tmp_path / "not-yet"

    AppPaths.for_home(home)

    assert not home.exists()


@pytest.mark.unit
def test_ensure_creates_every_root(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home")

    ensured = paths.ensure()

    assert ensured.roots == paths.roots
    for directory in ensured.roots:
        assert directory.is_dir()
    assert ensured.models.is_dir()
    assert ensured.webview.is_dir()


@pytest.mark.unit
@pytest.mark.platform_linux
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX directory modes")
def test_ensure_creates_private_directories(tmp_path: Path) -> None:
    ensured = AppPaths.for_home(tmp_path / "home").ensure()

    for directory in ensured.roots:
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700


@pytest.mark.unit
@pytest.mark.platform_linux
@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs extra privileges")
def test_ensure_resolves_a_symlinked_home_after_creating_it(tmp_path: Path) -> None:
    real_home = tmp_path / "real"
    real_home.mkdir()
    linked_home = tmp_path / "linked"
    try:
        linked_home.symlink_to(real_home, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not available")

    ensured = AppPaths.for_home(linked_home).ensure()

    assert ensured.data == (real_home / "data").resolve()
    assert ensured.config == (real_home / "config").resolve()
    assert ensured.cache == (real_home / "cache").resolve()
    assert ensured.logs == (real_home / "logs").resolve()


@pytest.mark.unit
def test_app_paths_are_immutable(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home")

    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.data = tmp_path / "elsewhere"  # type: ignore[misc]


@pytest.mark.unit
def test_home_override_accepts_a_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("STEMFORGE_HOME", "~/portable")

    paths = AppPaths.from_environment()

    assert paths.data == tmp_path / "portable" / "data"
    assert os.path.isabs(paths.data)


@pytest.mark.unit
def test_a_relative_home_override_becomes_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STEMFORGE_HOME", "relative-portable")

    paths = AppPaths.from_environment()

    assert paths.data == tmp_path / "relative-portable" / "data"
    assert paths.data.is_absolute()


@pytest.mark.unit
def test_storage_secret_is_32_random_bytes_of_hex_and_stays_put(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()

    first = paths.storage_secret()
    second = paths.storage_secret()

    assert len(first) == 64
    assert int(first, 16) >= 0
    assert second == first
    secret_file = paths.config / "storage_secret"
    assert secret_file.read_text(encoding="utf-8").strip() == first
    if os.name == "posix":
        assert secret_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_an_unreadable_storage_secret_is_regenerated(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    secret_file = paths.config / "storage_secret"
    secret_file.write_text("not a hex secret\n", encoding="utf-8")

    regenerated = paths.storage_secret()

    assert len(regenerated) == 64
    assert int(regenerated, 16) >= 0
    assert secret_file.read_text(encoding="utf-8").strip() == regenerated
