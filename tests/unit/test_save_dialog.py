"""The native save-dialog wrapper: filters, extension repair, and path trust.

The backend is injected so these tests never import pywebview's window stack and
never open a dialog. pywebview owns the strict filter grammar; the filename and
extension rules are owned elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from stemforge.integration.dialogs import (
    SAVE_AUDIO_EXTENSIONS,
    SAVE_FILTERS,
    NativeSaveDialog,
    SaveSelection,
    normalize_save_name,
    normalize_save_path,
    save_filter_for,
)

pytestmark = pytest.mark.unit

# pywebview 6.2.1 ``util.parse_file_type``, copied verbatim.
_FILTER_GRAMMAR = re.compile(r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$")


class FakeSaveBackend:
    """Records the save request and returns a scripted selection."""

    def __init__(self, result: Sequence[str] | None) -> None:
        self.result = result
        self.calls: list[tuple[str, str | None, tuple[str, ...]]] = []

    async def save_file(
        self,
        *,
        default_name: str,
        directory: str | None,
        file_types: tuple[str, ...],
    ) -> Sequence[str] | None:
        self.calls.append((default_name, directory, file_types))
        return self.result


def test_each_format_has_one_strict_filter() -> None:
    assert SAVE_AUDIO_EXTENSIONS == (".wav", ".flac", ".mp3")
    assert SAVE_FILTERS == {
        ".wav": "WAV 24 bit (*.wav)",
        ".flac": "FLAC 24 bit (*.flac)",
        ".mp3": "MP3 320 kbps (*.mp3)",
    }
    for suffix, value in SAVE_FILTERS.items():
        match = _FILTER_GRAMMAR.fullmatch(value)
        assert match is not None, value
        assert match.group(2) == f"*{suffix}"


def test_filter_lookup_accepts_an_extension_with_or_without_a_dot() -> None:
    assert save_filter_for("wav") == SAVE_FILTERS[".wav"]
    assert save_filter_for(".FLAC") == SAVE_FILTERS[".flac"]
    with pytest.raises(ValueError, match="unsupported export suffix"):
        save_filter_for(".ogg")


@pytest.mark.parametrize("suffix", SAVE_AUDIO_EXTENSIONS)
async def test_save_passes_the_default_name_directory_and_matching_filter(
    tmp_path: Path, suffix: str
) -> None:
    chosen = tmp_path / f"Song - Vocals{suffix}"
    backend = FakeSaveBackend([str(chosen)])
    dialog = NativeSaveDialog(backend=backend)

    selection = await dialog.save_audio_file(
        default_name=f"Song - Vocals{suffix}",
        suffix=suffix,
        directory=tmp_path,
    )

    assert selection == SaveSelection(path=chosen, corrected=False, typed_name=chosen.name)
    assert backend.calls == [(f"Song - Vocals{suffix}", str(tmp_path), (SAVE_FILTERS[suffix],))]


async def test_a_missing_directory_is_not_sent_to_pywebview(tmp_path: Path) -> None:
    backend = FakeSaveBackend([str(tmp_path / "Song.wav")])
    dialog = NativeSaveDialog(backend=backend)

    await dialog.save_audio_file(
        default_name="Song.wav",
        suffix=".wav",
        directory=tmp_path / "gone",
    )

    assert backend.calls[0][1] is None


async def test_cancel_returns_none() -> None:
    dialog = NativeSaveDialog(backend=FakeSaveBackend(None))

    assert await dialog.save_audio_file(default_name="Song.wav", suffix=".wav") is None


@pytest.mark.parametrize(
    "fake_path",
    [
        r"C:\fakepath\song.wav",
        "file:///home/me/song.wav",
        "https://example.test/song.wav",
        "relative/song.wav",
        "",
        "   ",
    ],
)
async def test_browser_fake_paths_are_never_accepted(fake_path: str) -> None:
    dialog = NativeSaveDialog(backend=FakeSaveBackend([fake_path]))

    assert await dialog.save_audio_file(default_name="Song.wav", suffix=".wav") is None


async def test_a_typed_known_audio_extension_is_replaced_not_used_to_switch_format(
    tmp_path: Path,
) -> None:
    chosen = tmp_path / "Song - Vocals.flac"
    dialog = NativeSaveDialog(backend=FakeSaveBackend([str(chosen)]))

    selection = await dialog.save_audio_file(
        default_name="Song - Vocals.wav",
        suffix=".wav",
    )

    assert selection is not None
    assert selection.path == tmp_path / "Song - Vocals.wav"
    assert selection.corrected is True
    assert selection.typed_name == "Song - Vocals.flac"


async def test_a_typed_extension_is_case_insensitive_when_it_matches(
    tmp_path: Path,
) -> None:
    chosen = tmp_path / "Song.FLAC"
    dialog = NativeSaveDialog(backend=FakeSaveBackend([str(chosen)]))

    selection = await dialog.save_audio_file(default_name="Song.flac", suffix=".flac")

    assert selection is not None
    assert selection.path == tmp_path / "Song.flac"
    assert selection.corrected is False


async def test_a_missing_extension_is_appended(tmp_path: Path) -> None:
    chosen = tmp_path / "Song"
    dialog = NativeSaveDialog(backend=FakeSaveBackend([str(chosen)]))

    selection = await dialog.save_audio_file(default_name="Song.mp3", suffix=".mp3")

    assert selection is not None
    assert selection.path == tmp_path / "Song.mp3"
    assert selection.corrected is False


@pytest.mark.parametrize(
    ("typed", "suffix", "expected", "corrected"),
    [
        ("Song", ".wav", "Song.wav", False),
        ("Song.flac", ".wav", "Song.wav", True),
        ("Song.m4a", ".mp3", "Song.mp3", True),
        ("Song.FLAC", ".flac", "Song.flac", False),
        ("song.tar.gz", ".wav", "song.tar.gz.wav", False),
        ("CON.wav", ".wav", "_CON.wav", False),
        ("track: <bad> / name? * .wav", ".wav", "track_ _bad_ _ name_ _.wav", False),
        ("...", ".wav", "Export.wav", False),
        ("   ", ".mp3", "Export.mp3", False),
    ],
)
def test_default_names_are_windows_safe_and_extension_normalized(
    typed: str, suffix: str, expected: str, corrected: bool
) -> None:
    result = normalize_save_name(typed, suffix)

    assert result.name == expected
    assert result.corrected is corrected


def test_a_name_is_capped_below_the_windows_filename_limit() -> None:
    result = normalize_save_name(f"{'a' * 300}.flac", ".wav")

    assert result.name.endswith(".wav")
    assert len(result.name.encode("utf-8")) <= 255
    assert result.corrected is True


def test_normalize_save_path_trusts_only_absolute_native_paths(tmp_path: Path) -> None:
    real = tmp_path / "Song.wav"

    assert normalize_save_path(str(real), ".wav") == SaveSelection(real, False, "Song.wav")
    assert normalize_save_path(r"C:\fakepath\Song.wav", ".wav") is None
    assert normalize_save_path("file:///tmp/Song.wav", ".wav") is None
    assert normalize_save_path("Song.wav", ".wav") is None
