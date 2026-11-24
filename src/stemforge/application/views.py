"""The immutable values and protocols the UI reads through ``WorkspaceService``.

These are the application layer's presentation-facing types. They may reference
domain enums and pipeline result types, but they carry no mutable state and no
behaviour beyond small derived properties.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from stemforge.artifacts import RemoveReport
from stemforge.domain.models import RenderScope, Track
from stemforge.domain.roles import INSTRUMENTAL_ROLE
from stemforge.engines.media import ExportFormat

# Presentation order and labels for Separation outputs. Instrumental is a stored
# Artifact, not a Stem (GLOSSARY), so the map is deliberately not named for stems.
SEPARATION_ROLE_LABELS: Final[Mapping[str, str]] = {
    "vocals": "Vocals",
    INSTRUMENTAL_ROLE: "Instrumental",
    "drums": "Drums",
    "bass": "Bass",
    "other": "Other",
}


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
    def created_at(self) -> datetime: ...

    @property
    def detail(self) -> str | None: ...

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
    label: str
    url: str
    gain_db: float = 0.0


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

    ``label`` is the tab text (``+3`` or, when both scopes exist at one
    semitone, ``+3 master`` / ``+3 stems``; two methods at one semitone also
    gain their method label). ``based_on_earlier_separation`` records that the
    set's recorded inputs no longer match that method's current set; such a set
    stays listed and selectable. An original-scope set has no method and one
    output role.
    """

    render_key: str
    semitones: int
    scope: RenderScope
    label: str
    created_at: datetime
    based_on_earlier_separation: bool = False
    method: str | None = None
    output_roles: tuple[str, ...] = ("original",)


@dataclass(frozen=True, slots=True)
class SeparationMethodView:
    """One Separation method as the card's selector sees it.

    The descriptor owns the stable id, label, description key, and whether
    choosing it requires Model weights; the card never reconstructs a method
    from its id, and the description prose lives with the UI copy.
    """

    id: str
    label: str
    description_key: str
    requires_model: bool


@dataclass(frozen=True, slots=True)
class SeparationAvailability:
    """Whether one Separation method can run on a Track, and why not.

    ``reason`` is the stable token (``mono`` or ``dual_mono``) the card maps to
    copy; it is ``None`` when the method is available or the Source's layout is
    unknown, which leaves the decision to the Job.
    """

    method: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SeparationSet:
    """One committed Separation set as the Separation card sees it.

    A Source keeps one set per method, so the view names its method and method
    version beside the roles the descriptor declares. ``set_key`` is the stable
    identity used by preview lookups and includes the method.
    """

    set_key: str
    created_at: datetime
    label: str
    method: str
    method_version: int
    output_roles: tuple[str, ...]
    stem_roles: tuple[str, ...]
    based_on_earlier_separation: bool = False
