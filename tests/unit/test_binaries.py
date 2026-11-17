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
    monkeypatch.delenv(binaries.RUBBERBAND_ENV, raising=False)
    monkeypatch.delenv(binaries.DENO_ENV, raising=False)
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    return tmp_path


@pytest.fixture
def frozen_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake frozen Bundle root; every vendor file is mandatory."""
    monkeypatch.setattr(binaries, "bundle_root", lambda: tmp_path)
    monkeypatch.setattr(binaries.resources, "is_frozen", lambda: True)
    for env in (
        binaries.FFMPEG_ENV,
        binaries.FFPROBE_ENV,
        binaries.RUBBERBAND_ENV,
        binaries.DENO_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(binaries.shutil, "which", lambda name: "/usr/bin/ignored")
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
    name = binaries.executable_name("ffprobe")
    on_path = _fake(root / "bin" / name)
    monkeypatch.setattr(
        binaries.shutil, "which", lambda query: str(on_path) if query == name else None
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


def _install(root: Path, *relative: str) -> list[Path]:
    return [_fake(root / "vendor" / item) for item in relative]


def test_vendor_path_is_the_ticket_10_layout_for_both_platforms() -> None:
    assert binaries.vendor_path("ffmpeg", system="linux") == Path("ffmpeg/ffmpeg")
    assert binaries.vendor_path("ffprobe", system="linux") == Path("ffmpeg/ffprobe")
    assert binaries.vendor_path("rubberband", system="linux") == Path("rubberband/rubberband")
    assert binaries.vendor_path("deno", system="linux") == Path("deno/deno")
    assert binaries.vendor_path("ffmpeg", system="win32") == Path("ffmpeg/ffmpeg.exe")
    assert binaries.vendor_path("deno", system="win32") == Path("deno/deno.exe")


def test_shared_library_name_is_the_co_located_sndfile() -> None:
    assert binaries.shared_library_name("rubberband", system="linux") == "libsndfile.so.1"
    assert binaries.shared_library_name("rubberband", system="win32") == "sndfile.dll"
    assert binaries.shared_library_name("ffmpeg", system="linux") is None


def test_frozen_resolves_all_four_tools(frozen_root: Path) -> None:
    installed = _install(
        frozen_root,
        "ffmpeg/ffmpeg",
        "ffmpeg/ffprobe",
        "rubberband/rubberband",
        "rubberband/libsndfile.so.1",
        "deno/deno",
    )

    assert binaries.ffmpeg() == installed[0]
    assert binaries.ffprobe() == installed[1]
    assert binaries.rubberband() == installed[2]
    assert binaries.deno() == installed[4]


def test_frozen_missing_tools_raise_bundle_damaged(frozen_root: Path) -> None:
    for resolver in (binaries.ffmpeg, binaries.ffprobe, binaries.rubberband, binaries.deno):
        with pytest.raises(binaries.BundleDamagedError) as raised:
            resolver()

        assert raised.value.code == "preflight.bundle_damaged"
        assert "vendor" in str(raised.value)


def test_frozen_rubberband_requires_its_shared_library(frozen_root: Path) -> None:
    _install(frozen_root, "rubberband/rubberband")

    with pytest.raises(binaries.BundleDamagedError) as raised:
        binaries.rubberband()

    assert "libsndfile.so.1" in str(raised.value)
    assert raised.value.code == "preflight.bundle_damaged"


def test_frozen_shared_libraries_are_resolved(frozen_root: Path) -> None:
    installed = _install(frozen_root, "rubberband/libsndfile.so.1")

    assert binaries.shared_libraries("rubberband") == (installed[0],)
    assert binaries.shared_libraries("ffmpeg") == ()


def test_development_shared_libraries_are_optional(root: Path) -> None:
    assert binaries.shared_libraries("rubberband") == ()
    library = _install(root, "rubberband/libsndfile.so.1")[0]

    assert binaries.shared_libraries("rubberband") == (library,)


def test_tool_environment_puts_the_vendor_directory_first_on_linux() -> None:
    executable = Path("/bundle/vendor/rubberband/rubberband")
    base = {"LD_LIBRARY_PATH": "/usr/lib", "PATH": "/usr/bin"}

    environment = binaries.tool_environment(executable, base=base, system="linux")

    assert environment["LD_LIBRARY_PATH"] == "/bundle/vendor/rubberband:/usr/lib"
    assert environment["PATH"] == "/usr/bin"


def test_tool_environment_does_not_touch_windows_loader_search() -> None:
    executable = Path(r"C:\bundle\vendor\rubberband\rubberband.exe")
    base = {"PATH": r"C:\Windows"}

    environment = binaries.tool_environment(executable, base=base, system="win32")

    assert environment == base


def test_tool_environment_leaves_an_empty_ld_library_path_absent() -> None:
    executable = Path("/bundle/vendor/rubberband/rubberband")

    environment = binaries.tool_environment(executable, base={}, system="linux")

    assert environment["LD_LIBRARY_PATH"] == "/bundle/vendor/rubberband"
