"""Conformance and configuration checks for the shared UI ``MediaTool`` fake."""

from __future__ import annotations

import inspect

import pytest
from media_fake import FakeMedia, MediaProfile

from stemforge.engines.media import MediaTool

pytestmark = pytest.mark.ui

_PROTOCOL_METHODS = {
    name: member
    for name, member in inspect.getmembers(MediaTool)
    if inspect.isfunction(member) and not name.startswith("_")
}


def test_the_shared_fake_matches_every_media_tool_signature() -> None:
    """A protocol change must fail here instead of drifting in nine suites."""
    assert _PROTOCOL_METHODS, "MediaTool exposes no methods to compare"
    for name, protocol_method in _PROTOCOL_METHODS.items():
        fake_method = getattr(FakeMedia, name, None)
        assert fake_method is not None, f"FakeMedia does not implement MediaTool.{name}"
        assert inspect.signature(fake_method) == inspect.signature(protocol_method), (
            f"FakeMedia.{name} has drifted from MediaTool.{name}"
        )


def test_probe_reports_the_default_source_as_cd_quality_wav(tmp_path) -> None:
    info = FakeMedia().probe(tmp_path / "Song.wav")

    assert info.format == "wav"
    assert info.codec == "pcm_f32le"
    assert (info.sample_rate, info.channels) == (44_100, 2)


def test_probe_reports_a_configured_non_wav_source_with_depth_and_rate(tmp_path) -> None:
    profile = MediaProfile(
        codec="flac",
        format="flac",
        sample_rate=96_000,
        channels=2,
        bit_depth=24,
        bit_rate=2_304_000,
    )
    info = FakeMedia(formats={".flac": profile}).probe(tmp_path / "Lossless.flac")

    assert (info.format, info.codec) == ("flac", "flac")
    assert (info.sample_rate, info.channels) == (96_000, 2)
    assert (info.bit_depth, info.bit_rate) == (24, 2_304_000)


def test_an_empty_metadata_title_stays_empty_for_the_import_fallback(tmp_path) -> None:
    fake = FakeMedia(title="")
    source = tmp_path / "Untitled.wav"
    source.write_bytes(b"source")

    assert fake.extract_metadata(source).title == ""


def test_extract_cover_returns_a_real_staged_cover(tmp_path) -> None:
    artwork = tmp_path / "art.png"
    artwork.write_bytes(b"\x89PNG fake cover")
    fake = FakeMedia(cover=artwork)
    staging = tmp_path / "staging"

    cover = fake.extract_cover(tmp_path / "Song.wav", staging)

    assert cover == staging / "cover.png"
    assert cover.read_bytes() == artwork.read_bytes()


def test_extract_cover_returns_none_without_configured_art(tmp_path) -> None:
    assert FakeMedia().extract_cover(tmp_path / "Song.wav", tmp_path / "staging") is None
