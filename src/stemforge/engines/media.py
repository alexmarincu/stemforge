"""``MediaTool``: probe, decode, encode, sum, and peak via FFmpeg.

One adapter owns every FFmpeg/ffprobe invocation in the application. It
validates what it produced with ffprobe before returning, writes only
``<name>.partial.<ext>`` files and renames them into place, raises coded
:class:`~stemforge.errors.StemForgeError` values, and never touches manifests or
the Workspace.

Policy: canonical audio, preview, export, the 30-minute cap, and ADR 0001.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, cast

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import StereoLayout
from stemforge.errors import StemForgeError
from stemforge.platform import binaries, process

CANONICAL_SAMPLE_RATE = 44_100
CANONICAL_CHANNELS = 2
CANONICAL_CODEC = "pcm_f32le"
MAX_DURATION_S = 1800.0
DURATION_TOLERANCE_S = 0.050
PREVIEW_BITRATE = "192k"
EXPORT_MP3_BITRATE = "320k"
PROBE_TIMEOUT_S = 60.0
ERROR_TAIL_CHARS = 2_000

# A side signal at or below this peak is digital silence for import purposes.
# The window sits near the 16-bit noise floor: identical channels cancel to
# -inf, while any real stereo image is far louder.
DUAL_MONO_SIDE_PEAK_DBFS = -90.0

_SOXR_LINE = re.compile(r"^\s*soxr\s+\d", re.MULTILINE)
_PEAK_LEVEL = re.compile(r"Peak level dB:\s*(-inf|-?\d+(?:\.\d+)?)")
_MAIN_PEAK_LEVEL = re.compile(r"\[astats@main[^\]]*\]\s*Peak level dB:\s*(-inf|-?\d+(?:\.\d+)?)")
_SIDE_PEAK_LEVEL = re.compile(r"\[astats@side[^\]]*\]\s*Peak level dB:\s*(-inf|-?\d+(?:\.\d+)?)")
_LEVEL_FILTER = (
    "[0:a]asplit=2[main][side];"
    "[main]astats@main=measure_perchannel=0:measure_overall=Peak_level[mainout];"
    "[side]pan=stereo|c0=c0-c1|c1=c0-c1,"
    "astats@side=measure_perchannel=0:measure_overall=Peak_level[sideout]"
)
_IMAGE_SUFFIXES: Mapping[str, str] = {
    "mjpeg": ".jpg",
    "jpeg": ".jpg",
    "png": ".png",
    "bmp": ".bmp",
    "webp": ".webp",
    "gif": ".gif",
    "tiff": ".tiff",
}


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """The audio stream and container facts ``ffprobe`` reported."""

    codec: str
    sample_rate: int
    channels: int
    duration_s: float
    format: str
    tags: Mapping[str, str]
    bit_depth: int | None = None
    bit_rate: int | None = None


@dataclass(frozen=True, slots=True)
class CanonicalMetadata:
    """The record-driven metadata carried by a Source."""

    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track: str = ""
    disc: str = ""
    date: str = ""
    genre: str = ""
    comment: str = ""
    composer: str = ""
    copyright: str = ""
    source_url: str = ""
    source_id: str = ""
    cover_path: Path | None = None
    duration_s: float = 0.0
    sample_rate: int = 0
    channels: int = 0

    def tags(self) -> Mapping[str, str]:
        """The exportable tag fields, in a stable order, omitting empty values."""
        return {key: value for key in EXPORT_TAG_FIELDS if (value := getattr(self, key))}


@dataclass(frozen=True, slots=True)
class DecodeResult:
    """The validated canonical WAV and the normalization that produced it."""

    path: Path
    duration_s: float
    sample_rate: int
    channels: int
    downmixed: bool


@dataclass(frozen=True, slots=True)
class LevelMeasurement:
    """One pass over a file: its peak and the peak of its left-minus-right side."""

    peak_dbfs: float
    side_peak_dbfs: float


def classify_stereo_layout(*, original_channels: int, side_peak_dbfs: float) -> StereoLayout:
    """Classify a Source from its original channels and decoded side peak.

    A one-channel original is mono whatever the canonical file contains. A
    two-or-more-channel original is dual-mono only when its side signal is at or
    below :data:`DUAL_MONO_SIDE_PEAK_DBFS`; anything louder carries a stereo
    image.
    """
    if original_channels == 1:
        return StereoLayout.MONO
    if side_peak_dbfs <= DUAL_MONO_SIDE_PEAK_DBFS:
        return StereoLayout.DUAL_MONO
    return StereoLayout.STEREO


class ExportFormat(StrEnum):
    """The three export formats."""

    WAV24 = "wav24"
    FLAC24 = "flac24"
    MP3_320 = "mp3_320"

    @property
    def suffix(self) -> str:
        match self:
            case ExportFormat.WAV24:
                return ".wav"
            case ExportFormat.FLAC24:
                return ".flac"
            case ExportFormat.MP3_320:
                return ".mp3"


EXPORT_TAG_FIELDS = (
    "title",
    "artist",
    "album",
    "album_artist",
    "track",
    "disc",
    "date",
    "genre",
    "comment",
    "composer",
    "copyright",
)

# WAV carries INFO tags only. The RIFF/INFO mapping has no
# album-artist, disc, or composer field that FFmpeg persists, so the writer and
# the verifier share this exact subset; MP3 and FLAC carry all eleven.
WAV_TAG_FIELDS = (
    "title",
    "artist",
    "album",
    "track",
    "date",
    "genre",
    "copyright",
)

_TAG_ALIASES: Mapping[str, tuple[str, ...]] = {
    "album_artist": ("album_artist", "albumartist", "album artist"),
    "track": ("track", "tracknumber"),
    "disc": ("disc", "discnumber", "disk"),
    "date": ("date", "year"),
}

_EXPORT_CODECS: Mapping[ExportFormat, str] = {
    ExportFormat.WAV24: "pcm_s24le",
    ExportFormat.FLAC24: "flac",
    ExportFormat.MP3_320: "mp3",
}

_EXPORT_FORMATS: Mapping[ExportFormat, str] = {
    ExportFormat.WAV24: "wav",
    ExportFormat.FLAC24: "flac",
    ExportFormat.MP3_320: "mp3",
}

_EXPORT_BIT_DEPTHS: Mapping[ExportFormat, int] = {
    ExportFormat.FLAC24: 24,
    ExportFormat.WAV24: 24,
}


def _export_codec_args(fmt: ExportFormat) -> list[str]:
    match fmt:
        case ExportFormat.WAV24:
            return ["-c:a", "pcm_s24le", "-rf64", "auto"]
        case ExportFormat.FLAC24:
            return [
                "-c:a",
                "flac",
                "-sample_fmt",
                "s32",
                "-bits_per_raw_sample",
                "24",
                "-compression_level",
                "8",
            ]
        case ExportFormat.MP3_320:
            return [
                "-c:a",
                "libmp3lame",
                "-b:a",
                EXPORT_MP3_BITRATE,
                "-id3v2_version",
                "3",
            ]


def _tag_args(tags: Mapping[str, str], fields: Sequence[str] = EXPORT_TAG_FIELDS) -> list[str]:
    arguments: list[str] = []
    for key in fields:
        value = tags.get(key)
        if value:
            arguments.extend(["-metadata", f"{key}={value}"])
    return arguments


class MediaTool(Protocol):
    """The single FFmpeg/ffprobe adapter."""

    def probe(self, path: Path) -> MediaInfo: ...

    def decode_canonical(
        self, src: Path, dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> DecodeResult: ...

    def extract_metadata(self, src: Path) -> CanonicalMetadata: ...

    def extract_cover(self, src: Path, dst_dir: Path) -> Path | None: ...

    def encode_preview(
        self,
        src: Path,
        dst: Path,
        *,
        gain_db: float,
        title: str,
        artist: str,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None: ...

    def encode_export(
        self,
        src: Path,
        dst: Path,
        *,
        fmt: ExportFormat,
        metadata: CanonicalMetadata,
        gain_db: float,
        cover: Path | None,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None: ...

    def sum_wavs(
        self, srcs: Sequence[Path], dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> None: ...

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float: ...

    def measure_levels(
        self, path: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> LevelMeasurement: ...


class FfmpegTool:
    """The :class:`MediaTool` implementation backed by the bundled binaries."""

    def __init__(
        self,
        *,
        ffmpeg_path: Path | None = None,
        ffprobe_path: Path | None = None,
    ) -> None:
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._soxr: bool | None = None

    @property
    def ffmpeg(self) -> Path:
        """The resolved FFmpeg executable."""
        if self._ffmpeg_path is None:
            try:
                self._ffmpeg_path = binaries.ffmpeg()
            except FileNotFoundError as error:
                raise _bundle_error("ffmpeg", error) from error
        return self._ffmpeg_path

    @property
    def ffprobe(self) -> Path:
        """The resolved ffprobe executable."""
        if self._ffprobe_path is None:
            try:
                self._ffprobe_path = binaries.ffprobe()
            except FileNotFoundError as error:
                raise _bundle_error("ffprobe", error) from error
        return self._ffprobe_path

    def supports_soxr(self) -> bool:
        """Whether this FFmpeg build offers the SoX resampler (probed once)."""
        if self._soxr is None:
            self._soxr = self._detect_soxr()
        return self._soxr

    def probe(self, path: Path) -> MediaInfo:
        document = self._probe_document(path)
        return _media_info(document, _require_audio(document, path))

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        document = self._probe_document(src)
        audio = _require_audio(document, src)
        info = _media_info(document, audio)
        tags = info.tags
        return CanonicalMetadata(
            title=_tag(tags, "title"),
            artist=_tag(tags, "artist"),
            album=_tag(tags, "album"),
            album_artist=_tag(tags, "album_artist"),
            track=_tag(tags, "track"),
            disc=_tag(tags, "disc"),
            date=_tag(tags, "date"),
            genre=_tag(tags, "genre"),
            comment=_tag(tags, "comment"),
            composer=_tag(tags, "composer"),
            copyright=_tag(tags, "copyright"),
            duration_s=info.duration_s,
            sample_rate=info.sample_rate,
            channels=info.channels,
        )

    def extract_cover(self, src: Path, dst_dir: Path) -> Path | None:
        document = self._probe_document(src)
        picture = _attached_picture(document)
        if picture is None:
            return None

        codec = _as_str(picture.get("codec_name"))
        suffix = _IMAGE_SUFFIXES.get(codec, f".{codec}" if codec else ".img")
        index = _as_int(picture.get("index")) or 0
        dst_dir.mkdir(parents=True, exist_ok=True)
        target = dst_dir / f"cover{suffix}"
        partial = dst_dir / f"cover.partial{suffix}"
        _remove(partial)
        run = process.run_capture(
            [
                str(self.ffmpeg),
                "-v",
                "error",
                "-y",
                "-nostdin",
                "-i",
                str(src),
                "-map",
                f"0:{index}",
                "-frames:v",
                "1",
                "-c:v",
                "copy",
                "-f",
                "image2",
                str(partial),
            ]
        )
        if not run.ok:
            _remove(partial)
            raise StemForgeError(
                errors.MEDIA_ENCODE_FAILED,
                "ffmpeg could not extract the embedded cover",
                retryable=True,
                detail=_tail(run.stderr) or None,
                context={"path": str(src)},
            )
        os.replace(partial, target)
        return target

    def decode_canonical(
        self,
        src: Path,
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> DecodeResult:
        cancel.check()
        source = self.probe(src)
        if source.duration_s > MAX_DURATION_S:
            raise StemForgeError(
                errors.IMPORT_TOO_LONG,
                "source exceeds the 30-minute import limit",
                detail=f"duration={source.duration_s:.3f}",
                context={"path": str(src), "duration_s": source.duration_s},
            )

        partial = _partial_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _remove(partial)
        argv = self._decode_argv(src, partial, source)
        log.debug("media: decode %s", _describe(argv))
        try:
            run = process.run_capture(
                argv,
                cancel_check=cancel.check,
                on_stderr_line=lambda line: log.debug("ffmpeg: %s", line),
            )
            if not run.ok:
                raise StemForgeError(
                    errors.DECODE_FAILED,
                    "ffmpeg could not decode the source",
                    retryable=True,
                    detail=_tail(run.stderr) or None,
                    context={"path": str(src)},
                )
            info = self._validate(
                partial,
                _Expect(codec=CANONICAL_CODEC, duration_s=source.duration_s),
                operation="decode",
            )
            cancel.check()
        except BaseException:
            _remove(partial)
            raise

        os.replace(partial, dst)
        return DecodeResult(
            path=dst,
            duration_s=info.duration_s,
            sample_rate=info.sample_rate,
            channels=info.channels,
            downmixed=source.channels > CANONICAL_CHANNELS,
        )

    def encode_preview(
        self,
        src: Path,
        dst: Path,
        *,
        gain_db: float,
        title: str,
        artist: str,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        source = self.probe(src)
        argv = [
            str(self.ffmpeg),
            "-v",
            "error",
            "-y",
            "-nostdin",
            "-i",
            str(src),
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-af",
            f"volume={gain_db}dB",
            "-c:a",
            "libmp3lame",
            "-b:a",
            PREVIEW_BITRATE,
            "-ar",
            str(CANONICAL_SAMPLE_RATE),
            "-ac",
            str(CANONICAL_CHANNELS),
            "-id3v2_version",
            "3",
        ]
        argv.extend(_tag_args({"title": title, "artist": artist}, fields=("title", "artist")))
        self._encode(
            argv,
            dst,
            _Expect(
                codec="mp3",
                duration_s=source.duration_s,
                format="mp3",
                bit_rate=192_000,
                video=False,
            ),
            operation="preview",
            cancel=cancel,
            log=log,
        )

    def encode_export(
        self,
        src: Path,
        dst: Path,
        *,
        fmt: ExportFormat,
        metadata: CanonicalMetadata,
        gain_db: float,
        cover: Path | None,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        source = self.probe(src)
        if cover is not None and not cover.is_file():
            raise StemForgeError(
                errors.MEDIA_ENCODE_FAILED,
                "the cover art file does not exist",
                retryable=True,
                context={"path": str(cover)},
            )
        embed_cover = cover if fmt is not ExportFormat.WAV24 else None

        argv = [str(self.ffmpeg), "-v", "error", "-y", "-nostdin", "-i", str(src)]
        if embed_cover is not None:
            argv.extend(["-i", str(embed_cover)])
        argv.extend(["-map", "0:a:0"])
        if embed_cover is not None:
            argv.extend(["-map", "1:v:0", "-c:v", "copy", "-disposition:v", "attached_pic"])
        argv.extend(["-map_metadata", "-1", "-map_chapters", "-1", "-af", f"volume={gain_db}dB"])
        argv.extend(_export_codec_args(fmt))
        tag_fields = WAV_TAG_FIELDS if fmt is ExportFormat.WAV24 else EXPORT_TAG_FIELDS
        argv.extend(_tag_args(dict(metadata.tags()), fields=tag_fields))

        expect = _Expect(
            codec=_EXPORT_CODECS[fmt],
            duration_s=source.duration_s,
            format=_EXPORT_FORMATS[fmt],
            bit_depth=_EXPORT_BIT_DEPTHS.get(fmt),
            bit_rate=320_000 if fmt is ExportFormat.MP3_320 else None,
            video=embed_cover is not None,
        )
        self._encode(argv, dst, expect, operation="export", cancel=cancel, log=log)

    def _encode(
        self,
        argv: list[str],
        dst: Path,
        expect: _Expect,
        *,
        operation: str,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        partial = _partial_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _remove(partial)
        log.debug("media: %s %s", operation, _describe([*argv, str(partial)]))
        try:
            run = process.run_capture(
                [*argv, str(partial)],
                cancel_check=cancel.check,
                on_stderr_line=lambda line: log.debug("ffmpeg: %s", line),
            )
            if not run.ok:
                raise StemForgeError(
                    errors.MEDIA_ENCODE_FAILED,
                    f"ffmpeg could not encode the {operation}",
                    retryable=True,
                    detail=_tail(run.stderr) or None,
                    context={"path": str(dst)},
                )
            self._validate(partial, expect, operation=operation)
            cancel.check()
        except BaseException:
            _remove(partial)
            raise
        os.replace(partial, dst)

    def sum_wavs(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        if not srcs:
            raise StemForgeError(errors.INTERNAL_UNEXPECTED, "sum_wavs needs at least one input")
        durations = [
            info.duration_s for info in (self.probe(src) for src in srcs) if info.duration_s > 0
        ]
        argv = [str(self.ffmpeg), "-v", "error", "-y", "-nostdin"]
        for src in srcs:
            argv.extend(["-i", str(src)])
        argv.extend(
            [
                "-filter_complex",
                f"amix=inputs={len(srcs)}:normalize=0",
                "-ac",
                str(CANONICAL_CHANNELS),
                "-ar",
                str(CANONICAL_SAMPLE_RATE),
                "-c:a",
                CANONICAL_CODEC,
                "-rf64",
                "auto",
            ]
        )
        self._encode(
            argv,
            dst,
            _Expect(
                codec=CANONICAL_CODEC,
                duration_s=max(durations) if durations else None,
            ),
            operation="sum",
            cancel=cancel,
            log=log,
        )

    def peak_dbfs(
        self,
        path: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> float:
        cancel.check()
        try:
            run = process.run_capture(
                [
                    str(self.ffmpeg),
                    "-hide_banner",
                    "-nostats",
                    "-v",
                    "info",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-af",
                    "astats",
                    "-f",
                    "null",
                    "-",
                ],
                cancel_check=cancel.check,
                on_stderr_line=lambda line: log.debug("ffmpeg: %s", line),
            )
        except TimeoutError as error:
            raise _probe_error(path, "peak measurement timed out") from error
        if not run.ok:
            raise _probe_error(path, _tail(run.stderr))
        matches = _PEAK_LEVEL.findall(run.stderr)
        if not matches:
            raise _probe_error(path, "astats reported no peak")
        return max(float("-inf") if value == "-inf" else float(value) for value in matches)

    def measure_levels(
        self,
        path: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> LevelMeasurement:
        """Measure the file's peak and its left-minus-right side peak in one pass.

        Import needs both to record the Source's stereo layout, so the same
        FFmpeg pass that finds the presentation peak also finds the side peak.
        The canonical file is stereo, so left minus right is defined.
        """
        cancel.check()
        try:
            run = process.run_capture(
                [
                    str(self.ffmpeg),
                    "-hide_banner",
                    "-nostats",
                    "-v",
                    "info",
                    "-nostdin",
                    "-i",
                    str(path),
                    "-filter_complex",
                    _LEVEL_FILTER,
                    "-map",
                    "[mainout]",
                    "-f",
                    "null",
                    "-",
                    "-map",
                    "[sideout]",
                    "-f",
                    "null",
                    "-",
                ],
                cancel_check=cancel.check,
                on_stderr_line=lambda line: log.debug("ffmpeg: %s", line),
            )
        except TimeoutError as error:
            raise _probe_error(path, "level measurement timed out") from error
        if not run.ok:
            raise _probe_error(path, _tail(run.stderr))
        main = _MAIN_PEAK_LEVEL.findall(run.stderr)
        side = _SIDE_PEAK_LEVEL.findall(run.stderr)
        if not main or not side:
            raise _probe_error(path, "astats reported no peak")
        return LevelMeasurement(
            peak_dbfs=_dbfs(main[0]),
            side_peak_dbfs=_dbfs(side[0]),
        )

    # --- internals ---------------------------------------------------------

    def _decode_argv(self, src: Path, partial: Path, source: MediaInfo) -> list[str]:
        argv = [
            str(self.ffmpeg),
            "-v",
            "error",
            "-y",
            "-nostdin",
            "-i",
            str(src),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
        ]
        if source.sample_rate != CANONICAL_SAMPLE_RATE or source.channels != CANONICAL_CHANNELS:
            if source.sample_rate != CANONICAL_SAMPLE_RATE and self.supports_soxr():
                argv.extend(["-af", "aresample=resampler=soxr:precision=28"])
            argv.extend(["-ac", str(CANONICAL_CHANNELS), "-ar", str(CANONICAL_SAMPLE_RATE)])
        argv.extend(["-c:a", CANONICAL_CODEC, "-rf64", "auto", str(partial)])
        return argv

    def _detect_soxr(self) -> bool:
        # FFmpeg lists filter names under ``-filters`` but resampler engines are
        # AVOptions of ``aresample``; this header is where ``soxr`` appears.
        try:
            run = process.run_capture(
                [str(self.ffmpeg), "-hide_banner", "-h", "filter=aresample"],
                timeout_s=PROBE_TIMEOUT_S,
            )
        except (OSError, TimeoutError):
            return False
        return run.ok and _SOXR_LINE.search(run.stdout) is not None

    def _probe_document(self, path: Path) -> dict[str, object]:
        argv = [
            str(self.ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            run = process.run_capture(argv, timeout_s=PROBE_TIMEOUT_S)
        except TimeoutError as error:
            raise _probe_error(path, "ffprobe timed out") from error
        except OSError as error:
            raise _probe_error(path, str(error)) from error
        if not run.ok:
            raise _probe_error(path, _tail(run.stderr))
        try:
            payload = json.loads(run.stdout)
        except json.JSONDecodeError as error:
            raise _probe_error(path, "ffprobe returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise _probe_error(path, "ffprobe returned no document")
        return cast(dict[str, object], payload)

    def _validate(self, path: Path, expect: _Expect, *, operation: str) -> MediaInfo:
        try:
            document = self._probe_document(path)
            info = _media_info(document, _require_audio(document, path))
        except StemForgeError as error:
            if error.code not in (errors.MEDIA_PROBE_FAILED, errors.IMPORT_NO_AUDIO_STREAM):
                raise
            raise _validation_error(path, operation, f"unreadable output: {error.code}") from error

        problems: list[str] = []
        if info.codec != expect.codec:
            problems.append(f"codec={info.codec}")
        if info.sample_rate != CANONICAL_SAMPLE_RATE:
            problems.append(f"sample_rate={info.sample_rate}")
        if info.channels != CANONICAL_CHANNELS:
            problems.append(f"channels={info.channels}")
        if expect.duration_s is not None and expect.duration_s > 0:
            drift = abs(info.duration_s - expect.duration_s)
            if drift > DURATION_TOLERANCE_S:
                problems.append(f"duration_drift={drift:.3f}")
        if expect.format is not None and info.format != expect.format:
            problems.append(f"format={info.format}")
        if expect.bit_depth is not None and info.bit_depth != expect.bit_depth:
            problems.append(f"bit_depth={info.bit_depth}")
        if expect.bit_rate is not None and (
            info.bit_rate is None or abs(info.bit_rate - expect.bit_rate) > expect.bit_rate * 0.05
        ):
            problems.append(f"bit_rate={info.bit_rate}")
        if expect.video is not None and _has_video(document) != expect.video:
            problems.append(f"video={_has_video(document)}")
        if problems:
            raise _validation_error(path, operation, ", ".join(problems))
        return info


@dataclass(frozen=True, slots=True)
class _Expect:
    """What a produced file must look like."""

    codec: str
    duration_s: float | None = None
    format: str | None = None
    bit_depth: int | None = None
    bit_rate: int | None = None
    video: bool | None = None


def _media_info(document: dict[str, object], audio: dict[str, object]) -> MediaInfo:
    container = _mapping(document.get("format"))
    duration = _as_float(container.get("duration"))
    if duration is None:
        duration = _as_float(audio.get("duration")) or 0.0
    tags = _merged_tags(container, audio)
    return MediaInfo(
        codec=_as_str(audio.get("codec_name")),
        sample_rate=_as_int(audio.get("sample_rate")) or 0,
        channels=_as_int(audio.get("channels")) or 0,
        duration_s=duration,
        format=_as_str(container.get("format_name")),
        tags=MappingProxyType(tags),
        bit_depth=_as_int(audio.get("bits_per_raw_sample"))
        or _as_int(audio.get("bits_per_sample")),
        bit_rate=_as_int(audio.get("bit_rate")) or _as_int(container.get("bit_rate")),
    )


def _merged_tags(container: dict[str, object], audio: dict[str, object]) -> Mapping[str, str]:
    merged: dict[str, str] = {}
    for source in (container.get("tags"), audio.get("tags")):
        for key, value in _mapping(source).items():
            if isinstance(value, str):
                merged[key.lower()] = value
    return merged


def _require_audio(document: dict[str, object], path: Path) -> dict[str, object]:
    streams = _streams(document)
    for stream in streams:
        if _as_str(stream.get("codec_type")) == "audio":
            return stream
    if not streams:
        raise _probe_error(path, "ffprobe reported no streams")
    raise StemForgeError(
        errors.IMPORT_NO_AUDIO_STREAM,
        "source has no audio stream",
        detail=f"streams={len(streams)}",
        context={"path": str(path)},
    )


def _streams(document: dict[str, object]) -> list[dict[str, object]]:
    raw = document.get("streams")
    if not isinstance(raw, list):
        return []
    return [cast(dict[str, object], item) for item in raw if isinstance(item, dict)]


def _has_video(document: dict[str, object]) -> bool:
    return any(_as_str(stream.get("codec_type")) == "video" for stream in _streams(document))


def _attached_picture(document: dict[str, object]) -> dict[str, object] | None:
    for stream in _streams(document):
        disposition = _mapping(stream.get("disposition"))
        if (
            _as_str(stream.get("codec_type")) == "video"
            and _as_int(disposition.get("attached_pic")) == 1
        ):
            return stream
    return None


def _tag(tags: Mapping[str, str], field: str) -> str:
    for key in _TAG_ALIASES.get(field, (field,)):
        value = tags.get(key)
        if value:
            return value
    return ""


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _dbfs(value: str) -> float:
    return float("-inf") if value == "-inf" else float(value)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _partial_path(dst: Path) -> Path:
    if dst.suffix:
        return dst.with_name(f"{dst.stem}.partial{dst.suffix}")
    return dst.with_name(f"{dst.name}.partial")


def _remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _tail(text: str, limit: int = ERROR_TAIL_CHARS) -> str:
    stripped = text.strip()
    return stripped[-limit:] if len(stripped) > limit else stripped


def _describe(argv: Sequence[str]) -> str:
    return " ".join(argv)


def _probe_error(path: Path, detail: str) -> StemForgeError:
    return StemForgeError(
        errors.MEDIA_PROBE_FAILED,
        f"ffprobe could not read {path.name}",
        detail=_tail(detail) or None,
        context={"path": str(path)},
    )


def _validation_error(path: Path, operation: str, detail: str) -> StemForgeError:
    return StemForgeError(
        errors.MEDIA_VALIDATION_FAILED,
        f"{operation} produced an invalid file",
        retryable=True,
        detail=detail,
        context={"path": str(path)},
    )


def _bundle_error(name: str, error: FileNotFoundError) -> StemForgeError:
    return StemForgeError(
        errors.PREFLIGHT_BUNDLE_DAMAGED,
        f"the bundled {name} is missing",
        detail=str(error),
    )
