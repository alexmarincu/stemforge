"""Immutable domain models: Source, Track, ArtifactRef, ArtifactSet, and the rest.

Pure dataclasses only: no I/O, no logging, no configuration. Ticket 08 and later
own behaviour that needs the filesystem; this module owns the shapes and the
values that cross the application seam (ticket 06 §5).
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


class ArtifactKind(StrEnum):
    """The role an Artifact plays in a Track's audio."""

    ORIGINAL = "original"
    STEM = "stem"
    INSTRUMENTAL = "instrumental"
    RENDER = "render"


class RenderScope(StrEnum):
    """Which input a Pitch Render starts from."""

    ORIGINAL = "original"
    STEMS = "stems"


class JobKind(StrEnum):
    """Every long-running Job kind (ticket 06 §3)."""

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
    bundled notice version is treated as absent (ticket 13 §7).
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


@dataclass(frozen=True, slots=True)
class Source:
    """Immutable identity and on-disk locations for one imported source."""

    source_id: str
    origin: str
    origin_kind: OriginKind
    metadata: SourceMetadata
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


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """One immutable audio artifact and its manifest provenance."""

    artifact_id: str
    source_id: str
    kind: ArtifactKind
    name: str
    master_path: Path
    preview_path: Path | None = None
    render_key: str | None = None
    gain_db: float = 0.0
    manifest_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSet:
    """The artifacts one Job produced, sharing provenance and presentation gain."""

    source_id: str
    artifacts: tuple[ArtifactRef, ...]
    render_key: str | None = None
    gain_db: float = 0.0
    peak_dbfs: float | None = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """A requested Pitch Render; it becomes an ArtifactSet only after the Job."""

    track_id: str
    semitones: int
    scope: RenderScope = RenderScope.ORIGINAL
