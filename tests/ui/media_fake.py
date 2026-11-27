"""One scripted ``MediaTool`` for every headless UI flow.

The fake writes byte stand-ins and never spawns FFmpeg. ``probe`` derives its
container facts from the source suffix so a flow can exercise a non-WAV Native
file, while decode still reports the canonical stereo 44.1 kHz output the real
adapter produces. The class implements the whole production protocol, so a new
method fails the conformance test instead of disappearing from one suite.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import (
    CanonicalMetadata,
    DecodeResult,
    ExportFormat,
    MediaInfo,
)
from stemforge.errors import StemForgeError

DEFAULT_DURATION_S = 120.0
CANONICAL_SAMPLE_RATE = 44_100
CANONICAL_CHANNELS = 2


@dataclass(frozen=True, slots=True)
class MediaProfile:
    """The container and stream facts ``probe`` reports for one suffix."""

    codec: str
    format: str
    sample_rate: int = CANONICAL_SAMPLE_RATE
    channels: int = CANONICAL_CHANNELS
    bit_depth: int | None = None
    bit_rate: int | None = None


WAV_PROFILE = MediaProfile(
    codec="pcm_f32le",
    format="wav",
    bit_depth=32,
    bit_rate=CANONICAL_SAMPLE_RATE * CANONICAL_CHANNELS * 32,
)

DEFAULT_PROFILES: Mapping[str, MediaProfile] = {
    ".wav": WAV_PROFILE,
    ".mp3": MediaProfile(codec="mp3", format="mp3", bit_rate=192_000),
    ".flac": MediaProfile(
        codec="flac",
        format="flac",
        bit_depth=24,
        bit_rate=CANONICAL_SAMPLE_RATE * CANONICAL_CHANNELS * 24,
    ),
    ".m4a": MediaProfile(codec="aac", format="mov,mp4,m4a,3gp,3g2,mj2", bit_rate=256_000),
    ".opus": MediaProfile(codec="opus", format="ogg", bit_rate=160_000),
    ".webm": MediaProfile(codec="opus", format="matroska,webm", bit_rate=160_000),
}


class FakeMedia:
    """A configurable ``MediaTool`` double shared by the page-flow suites."""

    def __init__(
        self,
        *,
        duration_s: float = DEFAULT_DURATION_S,
        title: str | None = None,
        peak_dbfs: float = -3.0,
        fail_probe: bool = False,
        fail_decode: StemForgeError | None = None,
        source_channels: int | None = None,
        formats: Mapping[str, MediaProfile] | None = None,
        cover: Path | None = None,
    ) -> None:
        self.duration_s = duration_s
        self.title = title
        self.peak_dbfs_value = peak_dbfs
        self.fail_probe = fail_probe
        self.fail_decode = fail_decode
        self.source_channels = source_channels
        self.formats = {**DEFAULT_PROFILES, **(formats or {})}
        self.cover = cover
        self.tags_by_path: dict[Path, dict[str, str]] = {}
        self.encode_error: StemForgeError | None = None
        self.corrupt_tags = False
        self.encode_gate: threading.Event | None = None
        self.encode_gate_after: int | None = None
        self.encode_started = threading.Event()
        self.encode_calls = 0
        self.fail_source_names: set[str] = set()
        self.probed: list[tuple[Path, MediaInfo]] = []
        self.decode_started = threading.Event()
        self._gate: threading.Event | None = None

    def block_decoding(self) -> threading.Event:
        """Make the next decode wait until the returned event is set."""
        self._gate = threading.Event()
        return self._gate

    def probe(self, path: Path) -> MediaInfo:
        if self.fail_probe:
            raise StemForgeError(errors.MEDIA_PROBE_FAILED, "fake probe failure")
        profile = self._profile(path)
        info = MediaInfo(
            codec=profile.codec,
            sample_rate=profile.sample_rate,
            channels=self._channels(profile),
            duration_s=self.duration_s,
            format=profile.format,
            tags=dict(self.tags_by_path.get(path, {})),
            bit_depth=profile.bit_depth,
            bit_rate=profile.bit_rate,
        )
        self.probed.append((path, info))
        return info

    def decode_canonical(
        self, src: Path, dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> DecodeResult:
        cancel.check()
        self.decode_started.set()
        if self._gate is not None:
            self._gate.wait(timeout=10)
        cancel.check()
        if self.fail_decode is not None:
            raise self.fail_decode
        dst.write_bytes(b"canonical:" + src.read_bytes())
        source_channels = self._channels(self._profile(src))
        return DecodeResult(
            path=dst,
            duration_s=self.duration_s,
            sample_rate=CANONICAL_SAMPLE_RATE,
            channels=CANONICAL_CHANNELS,
            downmixed=source_channels > CANONICAL_CHANNELS,
        )

    def extract_metadata(self, src: Path) -> CanonicalMetadata:
        profile = self._profile(src)
        return CanonicalMetadata(
            title=src.stem if self.title is None else self.title,
            duration_s=self.duration_s,
            sample_rate=profile.sample_rate,
            channels=self._channels(profile),
        )

    def extract_cover(self, src: Path, dst_dir: Path) -> Path | None:
        del src
        if self.cover is None:
            return None
        dst_dir.mkdir(parents=True, exist_ok=True)
        target = dst_dir / f"cover{self.cover.suffix.lower() or '.img'}"
        target.write_bytes(self.cover.read_bytes())
        return target

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
        del gain_db, title, artist, log
        cancel.check()
        dst.write_bytes(b"preview:" + src.read_bytes())

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
        del fmt, gain_db, cover
        cancel.check()
        self.encode_calls += 1
        self.encode_started.set()
        if self.encode_gate is not None and (
            self.encode_gate_after is None or self.encode_calls >= self.encode_gate_after
        ):
            self.encode_gate.wait(timeout=10)
        cancel.check()
        if src.stem in self.fail_source_names:
            raise StemForgeError(
                errors.MEDIA_ENCODE_FAILED,
                f"fake encode failure for {src.stem}",
                retryable=True,
            )
        if self.encode_error is not None:
            raise self.encode_error
        dst.write_bytes(b"encoded:" + src.read_bytes())
        tags = dict(metadata.tags())
        if self.corrupt_tags and "title" in tags:
            tags["title"] = "not the record"
        self.tags_by_path[dst] = tags
        log.debug("fake export wrote %s", dst)

    def sum_wavs(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        del log
        cancel.check()
        dst.write_bytes(b"sum:" + b"|".join(path.read_bytes() for path in srcs))

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        del path, log
        cancel.check()
        return self.peak_dbfs_value

    def _profile(self, path: Path) -> MediaProfile:
        return self.formats.get(path.suffix.lower(), WAV_PROFILE)

    def _channels(self, profile: MediaProfile) -> int:
        if self.source_channels is None:
            return profile.channels
        return self.source_channels
