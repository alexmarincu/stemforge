"""The native open-dialog wrapper: filters, normalization, and path trust.

The backend is injected so these tests never import pywebview's window stack and
never open a dialog. The filter grammar and the selection rules are owned
elsewhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import (
    AUDIO_EXTENSIONS,
    AUDIO_FILE_TYPES,
    AUDIO_FILTER,
    DropSelection,
    NativeOpenDialog,
    filter_drop_paths,
    is_native_path,
)

pytestmark = pytest.mark.unit


class FakeBackend:
    """Records the dialog request and returns a scripted selection."""

    def __init__(self, result: Sequence[str] | None) -> None:
        self.result = result
        self.calls: list[tuple[bool, tuple[str, ...]]] = []

    async def choose_files(
        self, *, allow_multiple: bool, file_types: tuple[str, ...]
    ) -> Sequence[str] | None:
        self.calls.append((allow_multiple, file_types))
        return self.result


def test_the_audio_filter_uses_pywebviews_strict_grammar() -> None:
    assert AUDIO_FILTER == "Audio (*.wav;*.mp3;*.flac;*.m4a)"
    assert AUDIO_FILE_TYPES == (AUDIO_FILTER,)
    assert AUDIO_EXTENSIONS == (".wav", ".mp3", ".flac", ".m4a")


async def test_open_requests_multiple_files_with_the_audio_filter(tmp_path: Path) -> None:
    first = tmp_path / "one.wav"
    second = tmp_path / "two.mp3"
    backend = FakeBackend([str(first), str(second)])
    dialog = NativeOpenDialog(backend=backend)

    selected = await dialog.choose_audio_files()

    assert backend.calls == [(True, AUDIO_FILE_TYPES)]
    assert selected == (first, second)


async def test_cancel_returns_no_paths() -> None:
    dialog = NativeOpenDialog(backend=FakeBackend(None))

    assert await dialog.choose_audio_files() == ()


async def test_extension_matching_ignores_case_and_surrounding_whitespace(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "LOUD.WAV"
    mixed = tmp_path / "quiet.FlAc"
    backend = FakeBackend([f"  {upper}  ", f"{mixed}\n"])
    dialog = NativeOpenDialog(backend=backend)

    selected = await dialog.choose_audio_files()

    assert selected == (upper, mixed)


async def test_unsupported_extension_is_a_coded_selection_error(tmp_path: Path) -> None:
    backend = FakeBackend([str(tmp_path / "notes.ogg")])
    dialog = NativeOpenDialog(backend=backend)

    with pytest.raises(StemForgeError) as caught:
        await dialog.choose_audio_files()

    assert caught.value.code == errors.IMPORT_UNSUPPORTED_SOURCE
    assert caught.value.detail == "extension"


@pytest.mark.parametrize(
    "fake_path",
    [
        r"C:\fakepath\song.wav",
        "file:///home/me/song.wav",
        "https://example.test/song.wav",
        "",
        "   ",
    ],
)
async def test_browser_fake_paths_are_never_accepted(fake_path: str) -> None:
    dialog = NativeOpenDialog(backend=FakeBackend([fake_path]))

    assert await dialog.choose_audio_files() == ()


async def test_a_mixed_selection_keeps_only_trusted_native_paths(tmp_path: Path) -> None:
    real = tmp_path / "real.wav"
    backend = FakeBackend([r"C:\fakepath\fake.wav", str(real), "file:///tmp/x.mp3"])
    dialog = NativeOpenDialog(backend=backend)

    assert await dialog.choose_audio_files() == (real,)


def test_drop_selection_requires_real_supported_paths(tmp_path: Path) -> None:
    real = tmp_path / "drop.FLAC"

    selection = filter_drop_paths(
        [
            str(real),
            r"C:\fakepath\dropped.wav",
            "file:///tmp/dropped.wav",
            str(tmp_path / "not-audio.txt"),
            "",
        ]
    )

    assert selection == DropSelection(accepted=(real,), ignored=4)
    assert selection.accepted[0].suffix.lower() == ".flac"


def test_native_path_trust_rules(tmp_path: Path) -> None:
    assert is_native_path(str(tmp_path / "song.wav")) is True
    assert is_native_path(r"C:\fakepath\song.wav") is False
    assert is_native_path("file:///tmp/song.wav") is False
    assert is_native_path("relative/song.wav") is False
