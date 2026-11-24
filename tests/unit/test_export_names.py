"""Pure export naming, collision, and destination-space helpers."""

from __future__ import annotations

from math import ceil
from pathlib import Path

import pytest

from stemforge.pipeline.export_names import (
    MAX_BASE_CHARS,
    MAX_FILENAME_BYTES,
    destination_base_name,
    export_filename,
    required_export_space,
    sanitize_base_name,
    suggest_base_name,
    unique_export_path,
)

pytestmark = pytest.mark.unit

_FORBIDDEN = frozenset('<>:"/\\|?*')
_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _has_forbidden(name: str) -> bool:
    return any(character in _FORBIDDEN or ord(character) < 32 for character in name)


def test_illegal_windows_characters_are_replaced() -> None:
    assert sanitize_base_name('A/B\\C:D*E?F"G<H>I|J') == "A_B_C_D_E_F_G_H_I_J"


def test_control_characters_are_replaced() -> None:
    assert sanitize_base_name("A\x00B\nC\tD") == "A_B_C_D"


def test_trailing_dots_and_spaces_are_removed() -> None:
    assert sanitize_base_name("  Song name. . ") == "Song name"


def test_an_all_dots_name_falls_back_to_a_safe_stem() -> None:
    assert sanitize_base_name("...") == "Export"
    assert sanitize_base_name("   ") == "Export"


def test_reserved_device_names_are_prefixed() -> None:
    for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"):
        assert sanitize_base_name(name) == f"_{name}"


def test_reserved_names_with_an_extension_are_still_reserved() -> None:
    assert sanitize_base_name("nul.flac") == "_nul.flac"
    assert sanitize_base_name("CONSOLE") == "CONSOLE"


def test_the_suggested_base_name_is_capped() -> None:
    assert len(sanitize_base_name("a" * 300)) == MAX_BASE_CHARS


def test_a_multibyte_name_never_exceeds_the_byte_cap() -> None:
    name = export_filename("😀" * 300, ".mp3")

    assert len(name.encode("utf-8")) <= MAX_FILENAME_BYTES
    assert name.endswith(".mp3")


def test_suggested_names_follow_the_pinned_pattern() -> None:
    assert suggest_base_name("Song") == "Song"
    assert suggest_base_name("Song", role="vocals") == "Song - Vocals"
    assert suggest_base_name("Song", role="instrumental") == "Song - Instrumental"
    assert suggest_base_name("Song", semitones=3) == "Song +3st"
    assert suggest_base_name("Song", role="vocals", semitones=3) == "Song - Vocals +3st"
    assert suggest_base_name("Song", semitones=-2) == "Song -2st"


def test_a_long_title_keeps_the_role_suffix_and_the_base_cap() -> None:
    base = suggest_base_name("a" * 300, role="instrumental", semitones=12)

    assert len(base) == MAX_BASE_CHARS
    assert base.endswith(" - Instrumental +12st")


def test_a_method_qualifier_is_part_of_the_role_suffix() -> None:
    assert suggest_base_name("Song", role="vocals", method_label="Karaoke") == (
        "Song - Vocals (Karaoke)"
    )
    assert (
        suggest_base_name("Song", role="instrumental", semitones=3, method_label="Demucs")
        == "Song - Instrumental (Demucs) +3st"
    )


def test_a_long_title_keeps_the_method_qualifier_and_the_base_cap() -> None:
    base = suggest_base_name("a" * 300, role="vocals", semitones=-2, method_label="Karaoke")

    assert len(base) == MAX_BASE_CHARS
    assert base.endswith(" - Vocals (Karaoke) -2st")


def test_export_filename_adds_the_extension_and_collision_suffix() -> None:
    assert export_filename("Song", ".wav") == "Song.wav"
    assert export_filename("Song", ".flac", collision=2) == "Song (2).flac"
    assert export_filename("Song", "mp3", collision=3) == "Song (3).mp3"


def test_export_filename_sanitizes_its_base() -> None:
    name = export_filename("CON: bad?.", ".wav")

    assert name.endswith(".wav")
    assert not _has_forbidden(name)
    assert Path(name).stem.upper() not in _RESERVED
    assert not Path(name).stem.endswith((" ", "."))


def test_a_collision_suffix_stays_within_the_byte_cap() -> None:
    name = export_filename("a" * 300, ".flac", collision=999)

    assert len(name.encode("utf-8")) <= MAX_FILENAME_BYTES
    assert name.endswith(" (999).flac")


def test_unique_path_walks_the_deterministic_collision_sequence(tmp_path: Path) -> None:
    taken = {tmp_path / "Song.wav", tmp_path / "Song (2).wav"}

    result = unique_export_path(
        tmp_path,
        "Song",
        ".wav",
        exists=lambda candidate: candidate in taken,
    )

    assert result == tmp_path / "Song (3).wav"


def test_unique_path_uses_the_first_free_name_when_nothing_exists(tmp_path: Path) -> None:
    assert unique_export_path(tmp_path, "Song", ".mp3") == tmp_path / "Song.mp3"


def test_space_estimate_is_the_wav_24_worst_case() -> None:
    minutes = 10
    expected = ceil(15_900_000 * minutes * 1.2 + 64 * 1024 * 1024)

    assert required_export_space(minutes * 60) == expected


def test_space_estimate_rejects_negative_durations() -> None:
    with pytest.raises(ValueError, match="duration"):
        required_export_space(-1)


@pytest.mark.parametrize(
    ("typed", "suffix", "expected"),
    [
        ("Song - Vocals.wav", ".wav", "Song - Vocals"),
        ("Song - Vocals.WAV", ".wav", "Song - Vocals"),
        ("Song - Vocals.flac", ".wav", "Song - Vocals.flac"),
        ("CON.wav", ".wav", "_CON"),
        ("track: <bad> / name? * .wav", ".wav", "track_ _bad_ _ name_ _"),
        (".wav", ".wav", "Export"),
        ("   ", ".wav", "Export"),
    ],
)
def test_a_dialog_supplied_name_becomes_a_safe_base(typed: str, suffix: str, expected: str) -> None:
    assert destination_base_name(typed, suffix) == expected
