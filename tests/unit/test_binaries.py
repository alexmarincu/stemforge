"""``platform.binaries``: ffmpeg/ffprobe resolution order."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge.platform import binaries

pytestmark = pytest.mark.unit


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake Bundle root with no vendored binaries yet."""
    monkeypatch.setattr(binaries, "bundle_root", lambda: tmp_path)
    monkeypatch.delenv(binaries.FFMPEG_ENV, raising=False)
    monkeypatch.delenv(binaries.FFPROBE_ENV, raising=False)
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    return tmp_path


def _fake(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def test_vendored_binary_wins(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vendored = _fake(root / "vendor" / "ffmpeg" / binaries.executable_name("ffmpeg"))
    monkeypatch.setenv(binaries.FFMPEG_ENV, str(_fake(root / "elsewhere" / "ffmpeg")))

    assert binaries.ffmpeg() == vendored


def test_environment_override_is_used_when_not_vendored(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = _fake(root / "custom" / "ffmpeg")
    monkeypatch.setenv(binaries.FFMPEG_ENV, str(override))

    assert binaries.ffmpeg() == override


def test_path_is_the_last_resort(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    on_path = _fake(root / "bin" / "ffprobe")
    monkeypatch.setattr(
        binaries.shutil, "which", lambda name: str(on_path) if name == "ffprobe" else None
    )

    assert binaries.ffprobe() == on_path


def test_missing_binary_raises_file_not_found(root: Path) -> None:
    with pytest.raises(FileNotFoundError) as error:
        binaries.ffmpeg()

    assert "ffmpeg" in str(error.value)


def test_broken_environment_override_does_not_fall_through(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(binaries.FFMPEG_ENV, str(root / "missing" / "ffmpeg"))
    monkeypatch.setattr(binaries.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    with pytest.raises(FileNotFoundError):
        binaries.ffmpeg()
