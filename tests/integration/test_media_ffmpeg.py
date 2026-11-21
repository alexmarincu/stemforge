"""Integration: the FFmpeg media adapter against fixtures generated at test time.

Every audio file in this module is produced by the real ``ffmpeg`` in a session
temporary directory; the repository commits no binary audio (ticket 11 §2).
Assertions are structural (codec, rate, channels, duration, peak, tags) rather
than byte hashes, so FFmpeg build differences stay out of the contract.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import (
    CanonicalMetadata,
    ExportFormat,
    FfmpegTool,
    MediaInfo,
)
from stemforge.platform import binaries, process

pytestmark = pytest.mark.integration

DURATION_TOLERANCE_S = 0.050


# --- Fixture factory -------------------------------------------------------


@dataclass(frozen=True)
class Fixtures:
    """Paths to the audio fixtures, all generated once per session."""

    root: Path
    stereo_44100_1s: Path
    stereo_44100_3s: Path
    stereo_48000_3s: Path
    mono_44100_3s: Path
    three_channel_44100_3s: Path
    silence_44100_1s: Path
    clipped_f32_44100_1s: Path
    stem_a: Path
    stem_b: Path
    stem_c: Path
    long_8000_60s: Path
    too_long_8000_1801s: Path
    mp3_native: Path
    flac_native: Path
    cover_png: Path
    video_only_mp4: Path
    truncated_wav: Path
    truncated_flac: Path
    unknown_codec_wav: Path
    random_bytes: Path


@pytest.fixture(scope="session")
def ffmpeg() -> Path:
    try:
        return binaries.ffmpeg()
    except FileNotFoundError as error:
        pytest.fail(f"ffmpeg is required for this layer and was not found: {error}")


@pytest.fixture(scope="session")
def ffprobe() -> Path:
    try:
        return binaries.ffprobe()
    except FileNotFoundError as error:
        pytest.fail(f"ffprobe is required for this layer and was not found: {error}")


@pytest.fixture(scope="session")
def tool(ffmpeg: Path, ffprobe: Path) -> FfmpegTool:
    return FfmpegTool(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory, ffmpeg: Path) -> Fixtures:
    root = tmp_path_factory.mktemp("media-fixtures")
    stereo = "sin(2*PI*440*t)|sin(2*PI*440*t)"

    def wav(
        name: str,
        *,
        expression: str,
        rate: int,
        channels: str,
        duration: float,
        codec: str = "pcm_s16le",
    ) -> Path:
        target = root / name
        _run(
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc={expression}:s={rate}:d={duration}:c={channels}",
            "-c:a",
            codec,
            target,
        )
        return target

    stereo_44100_3s = wav(
        "stereo-44100-3s.wav",
        expression=stereo,
        rate=44_100,
        channels="stereo",
        duration=3,
    )
    cover = root / "cover.png"
    _run(
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=64x64:d=1",
        "-frames:v",
        "1",
        cover,
    )

    mp3 = root / "native-tagged.mp3"
    _run(
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        stereo_44100_3s,
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        "-id3v2_version",
        "3",
        *_tags(_full_tags()),
        mp3,
    )

    flac = root / "native-tagged.flac"
    _run(
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        stereo_44100_3s,
        "-i",
        cover,
        "-map",
        "0:a:0",
        "-map",
        "1:v:0",
        "-c:a",
        "flac",
        "-sample_fmt",
        "s32",
        "-bits_per_raw_sample",
        "24",
        "-c:v",
        "copy",
        "-disposition:v",
        "attached_pic",
        *_tags(_full_tags()),
        flac,
    )

    video = root / "video-only.mp4"
    _run(
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=64x64:d=1",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        video,
    )

    truncated = root / "truncated.wav"
    truncated.write_bytes(stereo_44100_3s.read_bytes()[:36])

    complete_flac = root / "complete.flac"
    _run(ffmpeg, "-v", "error", "-y", "-i", stereo_44100_3s, "-c:a", "flac", complete_flac)
    truncated_flac = root / "truncated.flac"
    payload = complete_flac.read_bytes()
    truncated_flac.write_bytes(payload[: len(payload) // 2])

    unknown_codec = root / "unknown-codec.wav"
    payload = bytearray(stereo_44100_3s.read_bytes())
    struct.pack_into("<H", payload, 20, 0)  # the WAVE format tag becomes unknown
    unknown_codec.write_bytes(payload)

    random_bytes = root / "random.wav"
    random_bytes.write_bytes(bytes(range(256)) * 32)

    return Fixtures(
        root=root,
        stereo_44100_1s=wav(
            "stereo-44100-1s.wav",
            expression=stereo,
            rate=44_100,
            channels="stereo",
            duration=1,
        ),
        stereo_44100_3s=stereo_44100_3s,
        stereo_48000_3s=wav(
            "stereo-48000-3s.wav",
            expression=stereo,
            rate=48_000,
            channels="stereo",
            duration=3,
        ),
        mono_44100_3s=wav(
            "mono-44100-3s.wav",
            expression="sin(2*PI*440*t)",
            rate=44_100,
            channels="mono",
            duration=3,
        ),
        three_channel_44100_3s=wav(
            "three-channel-44100-3s.wav",
            expression="0.3*sin(2*PI*440*t)|0.2*sin(2*PI*550*t)|0.1*sin(2*PI*660*t)",
            rate=44_100,
            channels="3.0",
            duration=3,
        ),
        silence_44100_1s=wav(
            "silence-44100-1s.wav",
            expression="0",
            rate=44_100,
            channels="stereo",
            duration=1,
        ),
        clipped_f32_44100_1s=wav(
            "clipped-44100-1s.wav",
            expression="2*sin(2*PI*440*t)|2*sin(2*PI*440*t)",
            rate=44_100,
            channels="stereo",
            duration=1,
            codec="pcm_f32le",
        ),
        stem_a=wav(
            "stem-a.wav",
            expression="0.4*sin(2*PI*440*t)|0.4*sin(2*PI*440*t)",
            rate=44_100,
            channels="stereo",
            duration=1,
            codec="pcm_f32le",
        ),
        stem_b=wav(
            "stem-b.wav",
            expression="0.3*sin(2*PI*550*t)|0.3*sin(2*PI*550*t)",
            rate=44_100,
            channels="stereo",
            duration=1,
            codec="pcm_f32le",
        ),
        stem_c=wav(
            "stem-c.wav",
            expression="0.2*sin(2*PI*660*t)|0.2*sin(2*PI*660*t)",
            rate=44_100,
            channels="stereo",
            duration=1,
            codec="pcm_f32le",
        ),
        long_8000_60s=wav(
            "long-8000-60s.wav",
            expression="sin(2*PI*440*t)",
            rate=8_000,
            channels="mono",
            duration=60,
        ),
        too_long_8000_1801s=wav(
            "too-long-8000-1801s.wav",
            expression="0",
            rate=8_000,
            channels="mono",
            duration=1801,
        ),
        mp3_native=mp3,
        flac_native=flac,
        cover_png=cover,
        video_only_mp4=video,
        truncated_wav=truncated,
        truncated_flac=truncated_flac,
        unknown_codec_wav=unknown_codec,
        random_bytes=random_bytes,
    )


def _full_tags() -> dict[str, str]:
    return {
        "title": "Fixture Title",
        "artist": "Fixture Artist",
        "album": "Fixture Album",
        "album_artist": "Fixture Album Artist",
        "track": "3",
        "disc": "1",
        "date": "2024",
        "genre": "Test",
        "comment": "Fixture comment",
        "composer": "Fixture Composer",
        "copyright": "Fixture Copyright",
    }


def _tags(tags: dict[str, str]) -> Iterator[str]:
    for key, value in tags.items():
        yield "-metadata"
        yield f"{key}={value}"


def _run(executable: Path, *arguments: object) -> None:
    proc = subprocess.run(
        [str(executable), *(str(argument) for argument in arguments)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def _probe_json(ffprobe: Path, path: Path) -> dict[str, object]:
    proc = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    return payload


def _audio_stream(payload: dict[str, object]) -> dict[str, object]:
    streams = payload["streams"]
    assert isinstance(streams, list)
    for stream in streams:
        assert isinstance(stream, dict)
        if stream.get("codec_type") == "audio":
            return stream
    raise AssertionError("no audio stream")


def _has_attached_picture(payload: dict[str, object]) -> bool:
    streams = payload["streams"]
    assert isinstance(streams, list)
    return any(
        isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        and isinstance(stream.get("disposition"), dict)
        and stream["disposition"].get("attached_pic") == 1
        for stream in streams
    )


def _read_samples(ffmpeg: Path, path: Path) -> tuple[float, ...]:
    proc = subprocess.run(
        [str(ffmpeg), "-v", "error", "-i", str(path), "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    count = len(proc.stdout) // 4
    return struct.unpack(f"<{count}f", proc.stdout[: count * 4])


def _peak_dbfs(ffmpeg: Path, path: Path) -> float:
    proc = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "astats",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    values = re.findall(r"Peak level dB:\s*(-inf|-?\d+(?:\.\d+)?)", proc.stderr)
    assert values, proc.stderr
    return max(float("-inf") if value == "-inf" else float(value) for value in values)


def _assert_duration(actual: float, expected: float) -> None:
    assert abs(actual - expected) <= DURATION_TOLERANCE_S, (actual, expected)


def _assert_empty(directory: Path) -> None:
    assert list(directory.iterdir()) == []


def _assert_coded(error: pytest.ExceptionInfo[errors.StemForgeError], code: str) -> None:
    assert error.value.code == code


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "out"
    directory.mkdir()
    return directory


# --- probe -----------------------------------------------------------------


def test_probe_reports_the_audio_stream_and_container(tool: FfmpegTool, fixtures: Fixtures) -> None:
    info = tool.probe(fixtures.stereo_48000_3s)

    assert isinstance(info, MediaInfo)
    assert info.codec == "pcm_s16le"
    assert info.sample_rate == 48_000
    assert info.channels == 2
    assert info.format == "wav"
    _assert_duration(info.duration_s, 3.0)


def test_probe_returns_container_tags(tool: FfmpegTool, fixtures: Fixtures) -> None:
    info = tool.probe(fixtures.mp3_native)

    assert info.codec == "mp3"
    assert info.tags["title"] == "Fixture Title"
    assert info.tags["artist"] == "Fixture Artist"


def test_probe_rejects_a_video_without_audio(tool: FfmpegTool, fixtures: Fixtures) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.probe(fixtures.video_only_mp4)

    _assert_coded(error, errors.IMPORT_NO_AUDIO_STREAM)


def test_probe_rejects_random_bytes(tool: FfmpegTool, fixtures: Fixtures) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.probe(fixtures.random_bytes)

    _assert_coded(error, errors.MEDIA_PROBE_FAILED)


def test_decode_uses_the_canonical_format(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    result = tool.decode_canonical(
        fixtures.stereo_44100_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    info = tool.probe(result.path)
    assert info.codec == "pcm_f32le"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert info.format == "wav"
    _assert_duration(info.duration_s, 3.0)
    _assert_duration(result.duration_s, 3.0)
    assert result.downmixed is False
    # WAV writers never carry the source's comment tag as an ICMT chunk.
    assert b"ICMT" not in result.path.read_bytes()
    assert not (out_dir / "canonical.partial.wav").exists()


def test_decode_resamples_48000_to_44100(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    result = tool.decode_canonical(
        fixtures.stereo_48000_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    info = tool.probe(result.path)
    assert info.sample_rate == 44_100
    assert info.channels == 2
    _assert_duration(info.duration_s, 3.0)
    _assert_duration(result.duration_s, 3.0)


def test_decode_duplicates_mono_to_dual_mono(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    result = tool.decode_canonical(
        fixtures.mono_44100_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    assert tool.probe(result.path).channels == 2
    samples = _read_samples(_ffmpeg_of(tool), result.path)
    left = samples[0::2]
    right = samples[1::2]
    assert max(abs(a - b) for a, b in zip(left, right, strict=False)) <= 1e-6


def test_decode_downmixes_more_than_two_channels(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    result = tool.decode_canonical(
        fixtures.three_channel_44100_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    assert result.downmixed is True
    info = tool.probe(result.path)
    assert info.channels == 2
    assert info.sample_rate == 44_100


# --- decode policy: cap, errors, cancellation, command shape ---------------


def test_decode_rejects_sources_over_the_thirty_minute_cap(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.decode_canonical(
            fixtures.too_long_8000_1801s,
            out_dir / "canonical.wav",
            cancel=CancelToken(),
            log=_logger(),
        )

    _assert_coded(error, errors.IMPORT_TOO_LONG)
    assert error.value.detail is not None
    assert "1801" in error.value.detail
    _assert_empty(out_dir)


def test_a_truncated_wav_fails_the_probe_and_leaves_no_artifact(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.decode_canonical(
            fixtures.truncated_wav,
            out_dir / "canonical.wav",
            cancel=CancelToken(),
            log=_logger(),
        )

    _assert_coded(error, errors.MEDIA_PROBE_FAILED)
    _assert_empty(out_dir)


def test_a_truncated_flac_fails_validation_and_leaves_no_artifact(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.decode_canonical(
            fixtures.truncated_flac,
            out_dir / "canonical.wav",
            cancel=CancelToken(),
            log=_logger(),
        )

    _assert_coded(error, errors.MEDIA_VALIDATION_FAILED)
    _assert_empty(out_dir)


def test_a_corrupt_wav_fails_the_decode_and_leaves_no_artifact(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.decode_canonical(
            fixtures.unknown_codec_wav,
            out_dir / "canonical.wav",
            cancel=CancelToken(),
            log=_logger(),
        )

    _assert_coded(error, errors.DECODE_FAILED)
    _assert_empty(out_dir)


def test_a_cancelled_decode_leaves_neither_partial_nor_output(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    token = _CancelAfter(checks=3)

    with pytest.raises(errors.JobCancelled):
        tool.decode_canonical(
            fixtures.long_8000_60s,
            out_dir / "canonical.wav",
            cancel=token,
            log=_logger(),
        )

    _assert_empty(out_dir)


def test_a_pre_cancelled_decode_never_starts_ffmpeg(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    token = CancelToken()
    token.cancel()

    with pytest.raises(errors.JobCancelled):
        tool.decode_canonical(
            fixtures.stereo_44100_3s,
            out_dir / "canonical.wav",
            cancel=token,
            log=_logger(),
        )

    _assert_empty(out_dir)


def test_decode_omits_the_normalizers_for_a_canonical_source(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_ffmpeg_calls(monkeypatch, tool)

    tool.decode_canonical(
        fixtures.stereo_44100_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    argv = calls[-1]
    for flag in ("-af", "-ac", "-ar"):
        assert flag not in argv


def test_decode_uses_soxr_exactly_when_the_build_provides_it(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _record_ffmpeg_calls(monkeypatch, tool)

    tool.decode_canonical(
        fixtures.stereo_48000_3s,
        out_dir / "canonical.wav",
        cancel=CancelToken(),
        log=_logger(),
    )

    argv = calls[-1]
    assert "-ac" in argv
    assert argv[argv.index("-ac") + 1] == "2"
    assert "-ar" in argv
    assert argv[argv.index("-ar") + 1] == "44100"
    if tool.supports_soxr():
        assert "-af" in argv
        assert "resampler=soxr" in argv[argv.index("-af") + 1]
    else:
        assert "-af" not in argv


# --- metadata and cover ----------------------------------------------------


def test_extract_metadata_maps_every_record_field(tool: FfmpegTool, fixtures: Fixtures) -> None:
    metadata = tool.extract_metadata(fixtures.flac_native)

    assert metadata.title == "Fixture Title"
    assert metadata.artist == "Fixture Artist"
    assert metadata.album == "Fixture Album"
    assert metadata.album_artist == "Fixture Album Artist"
    assert metadata.track == "3"
    assert metadata.disc == "1"
    assert metadata.date == "2024"
    assert metadata.genre == "Test"
    assert metadata.comment == "Fixture comment"
    assert metadata.composer == "Fixture Composer"
    assert metadata.copyright == "Fixture Copyright"
    assert metadata.cover_path is None
    _assert_duration(metadata.duration_s, 3.0)
    assert metadata.sample_rate == 44_100
    assert metadata.channels == 2


def test_extract_metadata_defaults_to_empty_tags_for_an_untagged_source(
    tool: FfmpegTool, fixtures: Fixtures
) -> None:
    metadata = tool.extract_metadata(fixtures.stereo_44100_3s)

    assert metadata.title == ""
    assert metadata.artist == ""
    assert metadata.tags() == {}
    _assert_duration(metadata.duration_s, 3.0)


def test_extract_cover_writes_the_embedded_picture(
    tool: FfmpegTool, fixtures: Fixtures, tmp_path: Path
) -> None:
    target_dir = tmp_path / "source"

    cover = tool.extract_cover(fixtures.flac_native, target_dir)

    assert cover is not None
    assert cover == target_dir / "cover.png"
    assert cover.is_file()
    assert cover.stat().st_size > 0
    assert _probe_json(_ffprobe_of(tool), cover)["format"] is not None


def test_extract_cover_returns_none_without_a_picture(
    tool: FfmpegTool, fixtures: Fixtures, tmp_path: Path
) -> None:
    target_dir = tmp_path / "source"

    assert tool.extract_cover(fixtures.mp3_native, target_dir) is None
    assert tool.extract_cover(fixtures.stereo_44100_3s, target_dir) is None
    assert not target_dir.exists() or list(target_dir.iterdir()) == []


# --- preview ---------------------------------------------------------------


def test_preview_is_mp3_192_with_title_and_artist_only(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "original.mp3"

    tool.encode_preview(
        fixtures.flac_native,
        target,
        gain_db=0.0,
        title="Preview Title",
        artist="Preview Artist",
        cancel=CancelToken(),
        log=_logger(),
    )

    info = tool.probe(target)
    assert info.codec == "mp3"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert info.bit_rate == 192_000
    _assert_duration(info.duration_s, 3.0)
    assert info.tags["title"] == "Preview Title"
    assert info.tags["artist"] == "Preview Artist"
    assert "album" not in info.tags
    assert not _has_attached_picture(_probe_json(_ffprobe_of(tool), target))
    assert not (out_dir / "original.partial.mp3").exists()


def test_preview_applies_the_presentation_gain_without_clipping(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    unity = out_dir / "unity.mp3"
    attenuated = out_dir / "attenuated.mp3"
    common = {"title": "", "artist": "", "log": _logger()}

    tool.encode_preview(
        fixtures.stereo_44100_1s, unity, gain_db=0.0, cancel=CancelToken(), **common
    )
    tool.encode_preview(
        fixtures.stereo_44100_1s, attenuated, gain_db=-6.0, cancel=CancelToken(), **common
    )

    unity_peak = _peak_dbfs(_ffmpeg_of(tool), unity)
    attenuated_peak = _peak_dbfs(_ffmpeg_of(tool), attenuated)
    assert attenuated_peak <= 0.0
    assert abs((unity_peak - attenuated_peak) - 6.0) <= 0.1
    assert max(abs(sample) for sample in _read_samples(_ffmpeg_of(tool), attenuated)) <= 1.0


def test_a_cancelled_preview_leaves_no_partial(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    token = _CancelAfter(checks=3)

    with pytest.raises(errors.JobCancelled):
        tool.encode_preview(
            fixtures.long_8000_60s,
            out_dir / "original.mp3",
            gain_db=0.0,
            title="",
            artist="",
            cancel=token,
            log=_logger(),
        )

    _assert_empty(out_dir)


# --- export ----------------------------------------------------------------


def _export_metadata(fixtures: Fixtures) -> CanonicalMetadata:
    return CanonicalMetadata(
        title="Fixture Title",
        artist="Fixture Artist",
        album="Fixture Album",
        album_artist="Fixture Album Artist",
        track="3",
        disc="1",
        date="2024",
        genre="Test",
        comment="Fixture comment",
        composer="Fixture Composer",
        copyright="Fixture Copyright",
        duration_s=3.0,
        sample_rate=44_100,
        channels=2,
    )


def test_export_wav24_carries_info_tags_and_no_cover(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "export.wav"

    tool.encode_export(
        fixtures.stereo_44100_3s,
        target,
        fmt=ExportFormat.WAV24,
        metadata=_export_metadata(fixtures),
        gain_db=0.0,
        cover=fixtures.cover_png,
        cancel=CancelToken(),
        log=_logger(),
    )

    document = _probe_json(_ffprobe_of(tool), target)
    info = tool.probe(target)
    assert info.codec == "pcm_s24le"
    assert info.bit_depth == 24
    assert info.format == "wav"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    _assert_duration(info.duration_s, 3.0)
    assert not _has_attached_picture(document)
    for key in ("title", "artist", "album", "track", "date", "genre", "copyright"):
        assert info.tags[key] == _export_metadata(fixtures).tags()[key]
    # Ticket 05 §9: WAV comment tags are not written, so no ICMT INFO chunk.
    assert "comment" not in info.tags
    assert b"ICMT" not in target.read_bytes()
    assert not (out_dir / "export.partial.wav").exists()


def test_export_flac24_carries_all_tags_and_the_cover(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "export.flac"

    tool.encode_export(
        fixtures.stereo_44100_3s,
        target,
        fmt=ExportFormat.FLAC24,
        metadata=_export_metadata(fixtures),
        gain_db=0.0,
        cover=fixtures.cover_png,
        cancel=CancelToken(),
        log=_logger(),
    )

    document = _probe_json(_ffprobe_of(tool), target)
    info = tool.probe(target)
    assert info.codec == "flac"
    assert info.bit_depth == 24
    assert info.format == "flac"
    _assert_duration(info.duration_s, 3.0)
    assert _has_attached_picture(document)
    for key, value in _export_metadata(fixtures).tags().items():
        assert info.tags[key] == value


def test_export_mp3_320_carries_all_tags_and_the_cover(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "export.mp3"

    tool.encode_export(
        fixtures.stereo_44100_3s,
        target,
        fmt=ExportFormat.MP3_320,
        metadata=_export_metadata(fixtures),
        gain_db=0.0,
        cover=fixtures.cover_png,
        cancel=CancelToken(),
        log=_logger(),
    )

    document = _probe_json(_ffprobe_of(tool), target)
    info = tool.probe(target)
    assert info.codec == "mp3"
    assert info.bit_rate == 320_000
    assert info.format == "mp3"
    _assert_duration(info.duration_s, 3.0)
    assert _has_attached_picture(document)
    for key, value in _export_metadata(fixtures).tags().items():
        assert info.tags[key] == value


def test_export_with_a_missing_cover_fails_without_an_artifact(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(errors.StemForgeError) as error:
        tool.encode_export(
            fixtures.stereo_44100_3s,
            out_dir / "export.flac",
            fmt=ExportFormat.FLAC24,
            metadata=CanonicalMetadata(title="Export"),
            gain_db=0.0,
            cover=tmp_path / "missing.png",
            cancel=CancelToken(),
            log=_logger(),
        )

    _assert_coded(error, errors.MEDIA_ENCODE_FAILED)
    _assert_empty(out_dir)


def test_export_applies_gain_so_no_sample_exceeds_full_scale(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    source_peak = tool.peak_dbfs(fixtures.clipped_f32_44100_1s, cancel=CancelToken(), log=_logger())
    target = out_dir / "export.wav"

    tool.encode_export(
        fixtures.clipped_f32_44100_1s,
        target,
        fmt=ExportFormat.WAV24,
        metadata=CanonicalMetadata(title="Clipped"),
        gain_db=-7.0,
        cover=None,
        cancel=CancelToken(),
        log=_logger(),
    )

    peak = _peak_dbfs(_ffmpeg_of(tool), target)
    assert source_peak > 0.0
    assert peak <= 0.0
    assert abs(peak - (source_peak - 7.0)) <= 0.1
    assert max(abs(sample) for sample in _read_samples(_ffmpeg_of(tool), target)) <= 1.0


def test_export_encodes_without_normalization_at_zero_gain(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "export.flac"

    tool.encode_export(
        fixtures.stereo_44100_1s,
        target,
        fmt=ExportFormat.FLAC24,
        metadata=CanonicalMetadata(),
        gain_db=0.0,
        cover=None,
        cancel=CancelToken(),
        log=_logger(),
    )

    source_peak = _peak_dbfs(_ffmpeg_of(tool), fixtures.stereo_44100_1s)
    assert abs(_peak_dbfs(_ffmpeg_of(tool), target) - source_peak) <= 0.1


# --- sum and peak -----------------------------------------------------------


def test_sum_wavs_adds_the_inputs_without_normalizing(
    tool: FfmpegTool, fixtures: Fixtures, out_dir: Path
) -> None:
    target = out_dir / "instrumental.wav"

    tool.sum_wavs(
        [fixtures.stem_a, fixtures.stem_b, fixtures.stem_c],
        target,
        cancel=CancelToken(),
        log=_logger(),
    )

    info = tool.probe(target)
    assert info.codec == "pcm_f32le"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    _assert_duration(info.duration_s, 1.0)

    left, middle, right, total = (
        _read_samples(_ffmpeg_of(tool), path)
        for path in (fixtures.stem_a, fixtures.stem_b, fixtures.stem_c, target)
    )
    assert len(left) == len(total)
    error = max(
        abs(a + b + c - summed) for a, b, c, summed in zip(left, middle, right, total, strict=False)
    )
    assert error <= 1e-3
    assert max(abs(sample) for sample in total) <= 1.0
    assert b"ICMT" not in target.read_bytes()
    assert not (out_dir / "instrumental.partial.wav").exists()


def test_peak_dbfs_reads_full_scale_silence_and_overdriven_sources(
    tool: FfmpegTool, fixtures: Fixtures
) -> None:
    full_scale = tool.peak_dbfs(fixtures.stereo_44100_1s, cancel=CancelToken(), log=_logger())
    clipped = tool.peak_dbfs(fixtures.clipped_f32_44100_1s, cancel=CancelToken(), log=_logger())
    silent = tool.peak_dbfs(fixtures.silence_44100_1s, cancel=CancelToken(), log=_logger())

    assert abs(full_scale) <= 0.1
    assert abs(clipped - 6.02) <= 0.1
    assert silent == float("-inf")


def test_peak_dbfs_is_deterministic(tool: FfmpegTool, fixtures: Fixtures) -> None:
    first = tool.peak_dbfs(fixtures.stereo_44100_3s, cancel=CancelToken(), log=_logger())
    second = tool.peak_dbfs(fixtures.stereo_44100_3s, cancel=CancelToken(), log=_logger())

    assert first == second


# --- helpers ---------------------------------------------------------------


class _CancelAfter:
    """A token that raises ``JobCancelled`` on its Nth checkpoint."""

    def __init__(self, *, checks: int) -> None:
        self._remaining = checks

    def check(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            raise errors.JobCancelled()


def _record_ffmpeg_calls(monkeypatch: pytest.MonkeyPatch, tool: FfmpegTool) -> list[list[str]]:
    calls: list[list[str]] = []
    real = process.run_capture

    def spy(argv: object, **kwargs: object) -> process.CapturedRun:
        rendered = [str(argument) for argument in argv]  # type: ignore[union-attr]
        if rendered[0] == str(tool.ffmpeg):
            calls.append(rendered)
        return real(rendered, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(process, "run_capture", spy)
    return calls


def _logger() -> logging.Logger:
    return logging.getLogger("stemforge.tests.media")


def _ffmpeg_of(tool: FfmpegTool) -> Path:
    return tool.ffmpeg


def _ffprobe_of(tool: FfmpegTool) -> Path:
    return tool.ffprobe
