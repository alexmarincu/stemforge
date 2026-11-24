"""Immutable domain models: Source, Track, and the Job vocabulary.

Pure dataclasses only: no I/O, no logging, no configuration. Modules that come
later own behaviour that needs the filesystem; this module owns the shapes and
the values that cross the application seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OriginKind(StrEnum):
    """Where a Source came from."""

    FILE = "file"
    YOUTUBE = "youtube"


class RenderScope(StrEnum):
    """Which input a Pitch Render starts from."""

    ORIGINAL = "original"
    STEMS = "stems"


class StereoLayout(StrEnum):
    """The channel arrangement a Source was imported with.

    ``dual_mono`` is a stereo file whose left and right channels are identical,
    so it carries no stereo image. A Source imported before this fact existed
    has no layout at all; callers treat that as unknown rather than guessing.
    """

    STEREO = "stereo"
    MONO = "mono"
    DUAL_MONO = "dual_mono"


def parse_stereo_layout(value: object) -> StereoLayout | None:
    """Parse a recorded layout, treating a missing or unknown value as unknown."""
    if isinstance(value, StereoLayout):
        return value
    if isinstance(value, str):
        try:
            return StereoLayout(value)
        except ValueError:
            return None
    return None


def stereo_unsupported_reason(layout: StereoLayout | None) -> str | None:
    """Return the stable refusal reason for a layout without a stereo image.

    Equality rather than identity keeps a value that arrived as its recorded
    string (a hand-built record, a test double) on the same path.
    """
    if layout == StereoLayout.MONO:
        return StereoLayout.MONO.value
    if layout == StereoLayout.DUAL_MONO:
        return StereoLayout.DUAL_MONO.value
    return None


class JobKind(StrEnum):
    """Every long-running Job kind."""

    IMPORT_LOCAL = "import_local"
    IMPORT_YOUTUBE = "import_youtube"
    SEPARATE = "separate"
    RENDER_PITCH = "render_pitch"
    EXPORT = "export"
    MODEL_INSTALL = "model_install"


class JobState(StrEnum):
    """The Job state machine; cancellation is recorded, not a separate state."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ModelConsent:
    """The user's explicit acceptance of the model-weights licence notice.

    Stored in settings under ``models.consent``. A consent older than the
    bundled notice version is treated as absent.
    """

    version: int
    acknowledged: bool
    accepted_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """The canonical metadata record carried by a Source."""

    title: str
    artist: str = ""
    album: str = ""
    duration_s: float | None = None
    sample_rate: int | None = None
    channels: int | None = None


@dataclass(frozen=True, slots=True)
class Source:
    """Immutable identity and on-disk locations for one imported source."""

    source_id: str
    origin: str
    origin_kind: OriginKind
    metadata: SourceMetadata
    downmixed: bool = False
    original_channels: int | None = None
    stereo_layout: StereoLayout | None = None
    gain_db: float = 0.0
    native_path: Path | None = None
    canonical_path: Path | None = None
    preview_path: Path | None = None
    cover_path: Path | None = None
    content_hash: str | None = None
    created_at: datetime = field(default_factory=_utcnow)

    @property
    def title(self) -> str:
        return self.metadata.title


@dataclass(frozen=True, slots=True)
class Track:
    """The session handle for one Source in the Workspace."""

    track_id: str
    source: Source
    title: str
    semitones: int = 0
    created_at: datetime = field(default_factory=_utcnow)

    @property
    def source_id(self) -> str:
        return self.source.source_id

    @property
    def duration_s(self) -> float | None:
        """The source's duration, so presentation code never reaches through Source."""
        return self.source.metadata.duration_s

    @property
    def gain_db(self) -> float:
        """The Source set's one presentation gain."""
        return self.source.gain_db
