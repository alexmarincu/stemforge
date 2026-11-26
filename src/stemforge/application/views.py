"""The immutable values and protocols the UI reads through ``WorkspaceService``.

These are the application layer's presentation-facing types. They may reference
domain enums and pipeline result types, but they carry no mutable state and no
behaviour beyond small derived properties.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

from stemforge.artifacts import RemoveReport
from stemforge.domain.models import ExportBatchItemState as ExportBatchItemState
from stemforge.domain.models import RenderScope, Track
from stemforge.domain.roles import INSTRUMENTAL_ROLE
from stemforge.engines.media import ExportFormat

# Presentation order for Separation outputs. Instrumental is a stored Artifact,
# not a Stem (GLOSSARY), so the tuple is deliberately not named for stems.
SEPARATION_OUTPUT_ROLES: Final[tuple[str, ...]] = (
    "vocals",
    INSTRUMENTAL_ROLE,
    "drums",
    "bass",
    "other",
)


class PreflightCheckView(Protocol):
    """One preflight result as the banner reads it."""

    @property
    def name(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def message(self) -> str: ...


class PreflightView(Protocol):
    """The preflight facts the UI reads, kept out of the platform layer."""

    @property
    def degraded(self) -> bool: ...

    @property
    def checks(self) -> Sequence[PreflightCheckView]: ...


class JobKindValue(Protocol):
    """A Job kind or state that renders its stable string form."""

    @property
    def value(self) -> str: ...


class JobView(Protocol):
    """The read-only Job fields every UI surface may read.

    ``ui`` must not import ``stemforge.jobs`` (import-linter); this structural
    view is the single application-seam description of ``jobs.JobSnapshot``.
    """

    @property
    def job_id(self) -> str: ...

    @property
    def kind(self) -> JobKindValue: ...

    @property
    def state(self) -> JobKindValue: ...

    @property
    def progress(self) -> float: ...

    @property
    def stage(self) -> str: ...

    @property
    def stage_code(self) -> str | None: ...

    @property
    def stage_context(self) -> Mapping[str, object]: ...

    @property
    def created_at(self) -> datetime: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def detail_code(self) -> str | None: ...

    @property
    def detail_context(self) -> Mapping[str, object]: ...

    @property
    def active(self) -> bool: ...

    @property
    def finished(self) -> bool: ...

    @property
    def error_code(self) -> str | None: ...

    @property
    def error_detail(self) -> str | None: ...

    @property
    def error_context(self) -> Mapping[str, object]: ...

    @property
    def retryable(self) -> bool: ...

    @property
    def log_path(self) -> Path | None: ...

    @property
    def result_keys(self) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """What removing one Track did to the Workspace and the cache."""

    track: Track
    cache_removed: bool
    report: RemoveReport | None = None


@dataclass(frozen=True, slots=True)
class PreviewTarget:
    """One playable preview URL for a Track.

    ``original`` is the unshifted source preview; committed pitch renders expose
    their original preview for the A/B hook. The UI never builds these URLs
    itself: the service confines every path to the cache root that the single
    ``/media`` route serves.
    """

    key: str
    url: str
    gain_db: float = 0.0
    label_key: str = ""
    label_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ExportedFile:
    """The file one finished Export Job produced, for the toast and its actions."""

    job_id: str
    path: Path
    format: ExportFormat
    size_bytes: int
    duration_s: float
    gain_db: float


@dataclass(frozen=True, slots=True)
class ImportedFile:
    """The local Source's Native file as the sheet's Imported file block reads it."""

    base_name: str
    suffix: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SavedOriginal:
    """The file one finished Save original Job copied, for the toast and Retry."""

    job_id: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExportBatchItem:
    """One planned output and the outcome the batch recorded for it."""

    key: str
    state: ExportBatchItemState
    path: Path | None = None
    error_code: str | None = None
    error_detail: str | None = None
    error_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    label_key: str = ""
    label_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class ExportBatchResult:
    """A batch Job's last recorded outcomes, safe to read after any terminal state.

    The runner records ``result_keys`` only on success, so the per-item record
    lives in ``ExportService`` (ADR 0015). The page reads this view instead of
    inferring what was written from the Job's state.
    """

    track_id: str
    source_id: str
    version_key: str
    format: ExportFormat
    destination_dir: Path
    items: tuple[ExportBatchItem, ...]

    @property
    def written(self) -> tuple[ExportBatchItem, ...]:
        return self._items_in(ExportBatchItemState.WRITTEN)

    @property
    def failed(self) -> tuple[ExportBatchItem, ...]:
        return self._items_in(ExportBatchItemState.FAILED)

    @property
    def not_exported(self) -> tuple[ExportBatchItem, ...]:
        return self._items_in(ExportBatchItemState.NOT_EXPORTED)

    @property
    def retry_keys(self) -> tuple[str, ...]:
        """Every failed item, in batch order.

        A failed target may become available again after a fresh Separation, so
        the batch offers Retry for the failed set rather than filtering on the
        error's retryable flag.
        """
        return tuple(item.key for item in self.failed)

    @property
    def remaining_keys(self) -> tuple[str, ...]:
        """Every item that is not written, in batch order."""
        return tuple(
            item.key for item in self.items if item.state is not ExportBatchItemState.WRITTEN
        )

    def _items_in(self, state: ExportBatchItemState) -> tuple[ExportBatchItem, ...]:
        return tuple(item for item in self.items if item.state is state)


@dataclass(frozen=True, slots=True)
class LicenceFiles:
    """The Bundle-root licence artifacts the About modal exposes.

    Frozen builds resolve these beside ``_internal/`` (``Path(sys.executable)``
    parent); a source checkout resolves them at the repository root. Nothing is
    read from the current working directory.
    """

    root: Path
    license: Path
    notices: Path
    source_offer: Path
    licenses_dir: Path

    @property
    def missing(self) -> tuple[Path, ...]:
        """Every required path that is absent, in display order."""
        required = (self.license, self.notices, self.source_offer)
        missing = [path for path in required if not path.is_file()]
        if not self.licenses_dir.is_dir():
            missing.append(self.licenses_dir)
        return tuple(missing)

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass(frozen=True, slots=True)
class PitchSet:
    """One immutable Render set as version tabs and A/B see it.

    ``label_key`` and ``label_context`` carry the tab text (``+3``, or
    ``+3 master`` / ``+3 stems`` when both scopes exist at one semitone);
    the UI renders them at display time. ``based_on_earlier_separation``
    records that the set's recorded input hashes no longer match the current
    Stems; such a set stays listed and selectable.
    """

    render_key: str
    semitones: int
    scope: RenderScope
    created_at: datetime
    based_on_earlier_separation: bool = False
    label_key: str = ""
    label_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class SeparationSet:
    """One committed Separation set as the Separation card sees it.

    The artifact store keeps one Stems set per Source: a re-separation
    replaces it. The view is still a list so the UI contract does
    not have to change when an archived set appears; ``set_key`` is the stable
    identity used by preview lookups.
    """

    set_key: str
    created_at: datetime
    stem_roles: tuple[str, ...]
    based_on_earlier_separation: bool = False
    label_key: str = ""
    label_context: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
