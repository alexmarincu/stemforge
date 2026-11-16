"""``--self-test`` writes only under ``STEMFORGE_HOME``."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _isolated_environment(home: Path, *, fake_home: Path, xdg: Path, temp: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("XDG_"):
            env.pop(key)
    env.pop("STEMFORGE_LOG_LEVEL", None)
    env.update(
        {
            "STEMFORGE_HOME": str(home),
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "APPDATA": str(fake_home / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(fake_home / "AppData" / "Local"),
            "XDG_DATA_HOME": str(xdg / "data"),
            "XDG_CONFIG_HOME": str(xdg / "config"),
            "XDG_CACHE_HOME": str(xdg / "cache"),
            "XDG_STATE_HOME": str(xdg / "state"),
            "TMPDIR": str(temp),
            "TEMP": str(temp),
            "TMP": str(temp),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return env


@pytest.mark.unit
def test_self_test_creates_nothing_outside_stemforge_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_home = tmp_path / "fake-home"
    xdg = tmp_path / "xdg"
    temp = tmp_path / "canary-temp"
    work = tmp_path / "canary-cwd"
    for directory in (fake_home, xdg, temp, work):
        directory.mkdir()

    before = {path for path in tmp_path.rglob("*")}

    result = subprocess.run(
        [sys.executable, "-m", "stemforge", "--self-test"],
        cwd=work,
        env=_isolated_environment(home, fake_home=fake_home, xdg=xdg, temp=temp),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    after = {path for path in tmp_path.rglob("*")}
    created = after - before
    assert created
    assert all(path.is_relative_to(home) for path in created), sorted(
        str(path) for path in created if not path.is_relative_to(home)
    )

    assert list(work.iterdir()) == []
    assert list(fake_home.iterdir()) == []
    assert list(xdg.iterdir()) == []
    assert list(temp.iterdir()) == []

    assert (home / "config" / "settings.json").is_file()
    assert (home / "logs" / "stemforge.log").is_file()
    for directory in (
        home / "data",
        home / "cache",
        home / "logs",
        home / "data" / "models",
        home / "config",
        home / "data" / "webview",
    ):
        assert directory.is_dir()
