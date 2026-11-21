"""``WorkspaceService``: the only API the UI calls (ticket 06 §2).

The service owns the Workspace and orchestrates Jobs on behalf of the UI. It is
constructible headless: no NiceGUI, no torch, and injectable placeholders for the
ArtifactStore, JobRunner, and engine adapters (ticket 06 §13). Local import and
Export are wired here; Separation and Render follow in their tickets.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import quote
from uuid import uuid4

from stemforge import errors
from stemforge.artifacts import (
    ArtifactStore,
    ClearReport,
    EvictionReport,
    RemoveReport,
    RenderArtifact,
    SeparationArtifact,
    SweepReport,
)
from stemforge.domain.models import (
    JobKind,
    JobState,
    ModelConsent,
    OriginKind,
    RenderScope,
    Source,
    Track,
)
from stemforge.domain.roles import INSTRUMENTAL_ROLE, STEM_ROLES
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.engines.media import ExportFormat as ExportFormat
from stemforge.engines.media import MediaTool
from stemforge.engines.pitch import (
    MAX_SEMITONES,
    MIN_SEMITONES,
    PitchEngine,
    validate_semitones,
)
from stemforge.engines.separation import SeparationEngine
from stemforge.engines.youtube import (
    YouTubeTool,
    YouTubeVideo,
    YtDlpImporter,
    parse_youtube_url,
    shipped_tool_versions,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.jobs.models import JobSnapshot
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelSpec, ModelStatus, ModelStore
from stemforge.pipeline.export_job import (
    ExportTarget as ExportTarget,
)
from stemforge.pipeline.export_job import (
    export_targets as enumerate_export_targets,
)
from stemforge.pipeline.export_job import (
    resolve_export_target,
    run_export_job,
)
from stemforge.pipeline.export_names import unique_export_path
from stemforge.pipeline.import_job import run_local_import
from stemforge.pipeline.model_job import (
    ModelInstallJob,
    Transport,
    consent_is_current,
    require_model_consent,
)
from stemforge.pipeline.pitch_job import find_pitch_render, run_pitch_job
from stemforge.pipeline.separation_job import run_separation_job
from stemforge.pipeline.youtube_job import run_youtube_import
from stemforge.platform import resources
from stemforge.platform.paths import AppPaths
from stemforge.settings import RecentFile, SettingsStore


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
class ServiceEngines:
    """The engine adapters a WorkspaceService may call; tests inject fakes."""

    separation: object | None = None
    pitch: object | None = None
    media: MediaTool | None = None
    youtube: YouTubeTool | None = None


@dataclass(frozen=True, slots=True)
class RemoveOutcome:
    """What removing one Track did to the Workspace and the cache."""

    track: Track
    cache_removed: bool
    report: RemoveReport | None = None


@dataclass(frozen=True, slots=True)
class PreviewTarget:
    """One playable preview URL for a Track (ticket 09).

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
    """The Bundle-root licence artifacts the About modal exposes (ticket 22).

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
    """One immutable Render set as version tabs and A/B see it (ticket 11).

    ``label`` is the tab text (``+3`` or, when both scopes exist at one
    semitone, ``+3 master`` / ``+3 stems``). ``based_on_earlier_separation`` is
    the ticket-05/08 note that the set's recorded input hashes no longer match
    the current Stems; such a set stays listed and selectable.
    """

    render_key: str
    semitones: int
    scope: RenderScope
    label: str
    created_at: datetime
    based_on_earlier_separation: bool = False


@dataclass(frozen=True, slots=True)
class SeparationSet:
    """One committed Separation set as the Separation card sees it.

    The artifact store keeps one Stems set per Source: a re-separation
    replaces it (ticket 05). The view is still a list so the UI contract does
    not have to change when an archived set appears; ``set_key`` is the stable
    identity used by preview lookups.
    """

    set_key: str
    created_at: datetime
    label: str
    stem_roles: tuple[str, ...]
    based_on_earlier_separation: bool = False


# Separation output labels for the UI. Instrumental is a stored Artifact, not a
# Stem (GLOSSARY), so this map is deliberately not named for stems.
_SEPARATION_ROLE_LABELS: Mapping[str, str] = {
    "vocals": "Vocals",
    INSTRUMENTAL_ROLE: "Instrumental",
    "drums": "Drums",
    "bass": "Bass",
    "other": "Other",
}

# Display order shared by the service preview API and the Outputs card.
SEPARATION_OUTPUT_ROLES: tuple[str, ...] = (
    "vocals",
    INSTRUMENTAL_ROLE,
    "drums",
    "bass",
    "other",
)


class WorkspaceService:
    """Owns the Workspace and orchestrates Jobs on behalf of the UI."""

    def __init__(
        self,
        settings: SettingsStore,
        *,
        paths: AppPaths | None = None,
        artifacts: ArtifactStore | None = None,
        runner: JobRunner | None = None,
        engines: ServiceEngines | None = None,
        models: ModelStore | None = None,
        model_transport: Transport | None = None,
        youtube_delay: Callable[[float], None] = time.sleep,
        bundle_root: Path | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths if paths is not None else settings.paths
        self._bundle_root = (
            Path(bundle_root) if bundle_root is not None else resources.bundle_root()
        )
        self._artifacts = (
            artifacts
            if artifacts is not None
            else ArtifactStore(self._paths, cap_bytes=self._settings.settings.cache_cap_bytes)
        )
        self._runner = runner if runner is not None else JobRunner(paths=self._paths)
        self._engines = engines if engines is not None else ServiceEngines()
        self._models = models if models is not None else ModelStore(self._paths)
        self._model_transport = model_transport
        self._youtube_delay = youtube_delay
        self._workspace = Workspace()
        self._lock = threading.Lock()
        self._pending_separation_track_id: str | None = None
        self._job_owners: dict[str, tuple[str | None, str | None]] = {}

    @property
    def settings(self) -> SettingsStore:
        return self._settings

    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def artifacts(self) -> ArtifactStore:
        return self._artifacts

    @property
    def runner(self) -> JobRunner:
        return self._runner

    @property
    def engines(self) -> ServiceEngines:
        return self._engines

    @property
    def models(self) -> ModelStore:
        return self._models

    @property
    def bundle_root(self) -> Path:
        """The directory holding ``LICENSES/`` and the licence documents."""
        return self._bundle_root

    def licence_files(self) -> LicenceFiles:
        """Resolve the Bundle-root licence artifacts without touching the CWD."""
        return LicenceFiles(
            root=self._bundle_root,
            license=self._bundle_root / resources.LICENSE_FILENAME,
            notices=self._bundle_root / resources.NOTICES_FILENAME,
            source_offer=self._bundle_root / resources.SOURCE_OFFER_FILENAME,
            licenses_dir=self._bundle_root / resources.LICENSES_DIRNAME,
        )

    def snapshot(self) -> WorkspaceSnapshot:
        """Return an immutable view of the Workspace for the UI."""
        with self._lock:
            return self._workspace.snapshot()

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._settings.recent_files()

    def remove_recent(self, source_id: str) -> bool:
        return self._settings.remove_recent(source_id)

    def recent_available(self, source_id: str) -> bool:
        """Whether a recent entry can be opened without asking the user to find it.

        A local path must still be a file. A YouTube URL is not a filesystem path;
        availability is unknowable until a download is attempted, so it stays
        clickable and the importer owns the network outcome.
        """
        entry = next((item for item in self.recent_files() if item.source_id == source_id), None)
        if entry is None:
            return False
        if entry.origin_kind is OriginKind.YOUTUBE:
            return True
        return Path(entry.origin).is_file()

    def open_recent(self, source_id: str) -> str | None:
        """Re-import one recent entry and return its Job id.

        A missing local file raises the coded ``import.file_unavailable`` before
        any Job starts (ticket 07 §5: nothing starts until the user acts). A
        YouTube entry goes back through ``submit_youtube``, so a cached video
        opens without a network call and a missing one re-downloads.
        """
        entry = next((item for item in self.recent_files() if item.source_id == source_id), None)
        if entry is None:
            raise KeyError(source_id)
        if entry.origin_kind is OriginKind.YOUTUBE:
            return self.submit_youtube(entry.origin)
        path = Path(entry.origin)
        if not path.is_file():
            raise StemForgeError(
                errors.IMPORT_FILE_UNAVAILABLE,
                "the recent file is no longer readable",
                detail="missing",
                context={"path": str(path)},
            )
        return self.import_local(path)

    def remove_track(self, track_id: str) -> RemoveOutcome:
        """Remove a Track and delete its cached audio when nothing else uses it.

        Removing the Track that owns an active Job cancels that Job first and
        waits for the runner to acknowledge it (ticket 14 §6), so the runner can
        never delete a cache subtree that a Job is still writing.
        """
        with self._lock:
            track = self._workspace.find(track_id)
            if track is None:
                raise KeyError(track_id)
            owned = [
                job_id
                for job_id in self._active_job_ids_locked()
                if self._owns_track(job_id, track.track_id, track.source_id)
            ]
        for job_id in owned:
            self._runner.cancel(job_id)
        if owned:
            self._runner.wait_idle(timeout=5.0)
        with self._lock:
            track = self._workspace.remove_track(track_id)
            self._artifacts.release(track.source_id)
            still_live = self._artifacts.is_live(track.source_id)
            for job_id in owned:
                self._job_owners.pop(job_id, None)
        if still_live:
            return RemoveOutcome(track=track, cache_removed=False)
        report = self._artifacts.remove_source(track.source_id)
        removed = report.source_removed or bool(report.renders_removed)
        return RemoveOutcome(track=track, cache_removed=removed, report=report)

    def _active_job_ids_locked(self) -> tuple[str, ...]:
        return tuple(snapshot.job_id for snapshot in self._runner.jobs() if snapshot.active)

    def _owns_track(self, job_id: str, track_id: str, source_id: str) -> bool:
        owner = self._job_owners.get(job_id)
        if owner is None:
            return False
        owner_track, owner_source = owner
        return owner_track == track_id or owner_source == source_id

    def _remember_job(self, job_id: str, *, track_id: str | None, source_id: str | None) -> str:
        with self._lock:
            self._job_owners[job_id] = (track_id, source_id)
        return job_id

    def sweep_stale(self) -> SweepReport:
        """Run the startup sweep once; the UI shows recovery copy when it deleted work."""
        return self._artifacts.sweep_stale()

    def enforce_cache_cap(self) -> EvictionReport:
        """Run the startup LRU pass after the sweep (ticket 07 §2, §7)."""
        return self._artifacts.enforce_cap()

    def cache_usage_bytes(self) -> int:
        """The cache meter's used size; the UI never reads the store directly."""
        return self._artifacts.usage_bytes()

    def cache_cap_bytes(self) -> int:
        """The configured cache ceiling (ticket 07 §2)."""
        return self._artifacts.cap_bytes

    def logs_dir(self) -> Path:
        """The app's log root, shown and opened from Settings & About."""
        return self._paths.logs

    def youtube_cache_bytes(self) -> int:
        """The size of ``<cache>/yt-dlp/`` shown in Settings & About."""
        return self._artifacts.youtube_cache_bytes()

    def clear_youtube_cache(self) -> int:
        """Delete only ``<cache>/yt-dlp/`` and return the bytes freed."""
        return self._artifacts.clear_youtube_cache()

    def clear_cache(self) -> ClearReport:
        """Delete every non-live cached artifact plus the tool caches (ticket 07)."""
        return self._artifacts.clear_cache()

    def clear_recent(self) -> None:
        """Clear the recent-files list; cached audio is untouched (ticket 07)."""
        self._settings.clear_recent()

    # -- Preview -----------------------------------------------------------

    def has_preview(self, track_id: str) -> bool:
        """Whether the Track's source preview exists inside the cache."""
        track = next(
            (item for item in self.snapshot().tracks if item.track_id == track_id),
            None,
        )
        return track is not None and self._preview_url(track.source.preview_path) is not None

    def preview_targets(self, track_id: str) -> tuple[PreviewTarget, ...]:
        """The playable previews for one Track, original first.

        Only files inside the cache root are exposed. A missing preview is simply
        absent, which lets the UI mark its row unavailable instead of requesting a
        URL that can only 404. Render sets follow in creation order, newest last;
        a stems set previews its shifted Instrumental because that is the one
        primary output the set always carries.
        """
        track = next(
            (item for item in self.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return ()
        return self._preview_targets(track)

    def pitch_render_preview_target(
        self,
        track: Track,
        render_key: str,
        role: str,
    ) -> PreviewTarget | None:
        """The preview of one member of a Render set (ticket 08 §7).

        A master set carries only its shifted master; a stems set carries Vocals,
        Instrumental, Drums, Bass, and Other. The key is stable and unique across
        versions so the single player can tell the rows apart.
        """
        view = next(
            (
                item
                for item in self._render_views(track.source_id)
                if item.artifact.render_key == render_key
            ),
            None,
        )
        if view is None:
            return None
        if view.scope is RenderScope.ORIGINAL:
            if role != "original":
                return None
            file_role = "original_preview"
        else:
            if role not in (*STEM_ROLES, INSTRUMENTAL_ROLE):
                return None
            file_role = f"{role}_preview"
        url = self._preview_url(view.artifact.path_for(file_role))
        if url is None:
            return None
        return PreviewTarget(
            key=f"render:{view.artifact.render_key}:{role}",
            label=_SEPARATION_ROLE_LABELS.get(role, role.title()),
            url=url,
            gain_db=view.artifact.gain_db,
        )

    def _preview_targets(self, track: Track) -> tuple[PreviewTarget, ...]:
        targets: list[PreviewTarget] = []
        source_url = self._preview_url(track.source.preview_path)
        if source_url is not None:
            targets.append(
                PreviewTarget(
                    key="original",
                    label="Original",
                    url=source_url,
                    gain_db=track.gain_db,
                )
            )
        for view in self._render_views(track.source_id):
            url = self._render_preview_url(view)
            if url is None:
                continue
            targets.append(
                PreviewTarget(
                    key=view.artifact.render_key,
                    label=view.label,
                    url=url,
                    gain_db=view.artifact.gain_db,
                )
            )
        return tuple(targets)

    # -- Pitch -------------------------------------------------------------

    def has_separation(self, track: Track) -> bool:
        """Whether the Track has committed Stems, so the stems scope can be offered."""
        return self._artifacts.find_separation(track.source_id) is not None

    def pitch_sets(self, track: Track) -> tuple[PitchSet, ...]:
        """Every committed render set for the Track, oldest first (ticket 08 §7)."""
        return tuple(
            PitchSet(
                render_key=view.artifact.render_key,
                semitones=view.semitones,
                scope=view.scope,
                label=view.label,
                created_at=view.artifact.created_at,
                based_on_earlier_separation=view.based_on_earlier_separation,
            )
            for view in self._render_views(track.source_id)
        )

    def pitch_set(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> PitchSet | None:
        """The set that satisfies a Render request for ``(semitones, scope)``.

        Scope is part of the query: a master render never answers a stems
        request (ticket 08 §5). A stems set built from an earlier separation is
        not a cache hit for the current Stems, so Render is offered instead of
        Open while the old set stays in the tabs.
        """
        if isinstance(semitones, bool) or not isinstance(semitones, int):
            return None
        if not MIN_SEMITONES <= semitones <= MAX_SEMITONES or semitones == 0:
            return None
        resolved_scope = RenderScope(scope)
        found = find_pitch_render(
            self._artifacts,
            source_id=track.source_id,
            semitones=semitones,
            scope=resolved_scope,
        )
        sets = self.pitch_sets(track)
        if found is not None:
            match = next(
                (item for item in sets if item.render_key == found.artifact.render_key),
                None,
            )
            if match is not None and not match.based_on_earlier_separation:
                return match
        for item in reversed(sets):
            if (
                item.semitones == semitones
                and item.scope is resolved_scope
                and not item.based_on_earlier_separation
            ):
                return item
        return None

    def pitch_preview_target(self, track: Track, render_key: str) -> PreviewTarget | None:
        """The playable preview of one render set, or ``None`` when it is gone."""
        return next(
            (target for target in self._preview_targets(track) if target.key == render_key),
            None,
        )

    def render_pitch(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> str:
        """Submit one Pitch Render Job and return its id (ticket 03, ticket 08 §5).

        The request is explicit: the UI's stepper never calls this by itself.
        ``scope`` is ``original`` for the default flow, or ``stems`` when the
        user asks for the explicit stems action after a Separation.
        """
        shift = validate_semitones(semitones)
        resolved_scope = RenderScope(scope)
        pitch = cast("PitchEngine | None", self._engines.pitch)
        media = self._engines.media
        if pitch is None or media is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "the pitch render engines are not configured",
            )
        if (
            resolved_scope is RenderScope.STEMS
            and self._artifacts.find_separation(track.source_id) is None
        ):
            raise ValueError(f"stems are not available for this source: {track.source_id}")
        job = Job(
            kind=JobKind.RENDER_PITCH,
            identity=(
                JobKind.RENDER_PITCH.value,
                track.source_id,
                resolved_scope.value,
                str(shift),
            ),
            title=track.title,
            run=self._pitch_run(track, shift, resolved_scope, pitch, media),
        )
        return self._remember_job(
            self._runner.submit(job),
            track_id=track.track_id,
            source_id=track.source_id,
        )

    def _pitch_run(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope,
        pitch: PitchEngine,
        media: MediaTool,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_pitch_job(
                ctx,
                artifacts=self._artifacts,
                pitch=pitch,
                media=media,
                source_id=track.source_id,
                semitones=semitones,
                scope=scope,
                title=track.title,
            )
            return {
                "track_id": track.track_id,
                "render_key": result.artifact.render_key,
                "scope": result.scope.value,
                "semitones": str(result.semitones),
            }

        return run

    def _render_views(self, source_id: str) -> tuple[_RenderView, ...]:
        """Resolve render manifests into tab labels, scope, and staleness."""
        artifacts = [item for item in self._artifacts.iter_renders() if item.source_id == source_id]
        parsed: list[tuple[RenderArtifact, RenderScope, int]] = []
        for artifact in artifacts:
            scope = _render_scope(artifact)
            semitones = _render_semitones(artifact)
            if scope is None or semitones is None:
                continue
            parsed.append((artifact, scope, semitones))
        parsed.sort(key=lambda item: (item[0].created_at, item[0].render_key))
        collisions = {(semitones, scope) for _, scope, semitones in parsed}
        separation = self._artifacts.find_separation(source_id) if parsed else None
        views: list[_RenderView] = []
        for artifact, scope, semitones in parsed:
            label = _semitone_label(semitones)
            other = RenderScope.STEMS if scope is RenderScope.ORIGINAL else RenderScope.ORIGINAL
            if (semitones, other) in collisions:
                label += " master" if scope is RenderScope.ORIGINAL else " stems"
            based_on_earlier = scope is RenderScope.STEMS and _based_on_earlier_separation(
                artifact, separation
            )
            views.append(
                _RenderView(
                    artifact=artifact,
                    semitones=semitones,
                    scope=scope,
                    label=label,
                    based_on_earlier_separation=based_on_earlier,
                )
            )
        return tuple(views)

    def _render_preview_url(self, view: _RenderView) -> str | None:
        role = "original_preview" if view.scope is RenderScope.ORIGINAL else "instrumental_preview"
        return self._preview_url(view.artifact.path_for(role))

    def _preview_url(self, path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            root = self._artifacts.root.resolve(strict=False)
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                return None
            relative = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            return None
        return "/media/" + quote(relative, safe="/")

    # -- Separation --------------------------------------------------------

    def separation_sets(self, track: Track) -> tuple[SeparationSet, ...]:
        """Every committed Separation set for the Track, oldest first (ticket 14).

        The store replaces the Stems directory on a re-separation, so there is
        at most one set. The newest set is the last item and is the card's
        default; the tuple shape keeps older labelled sets representable.
        """
        artifact = self._artifacts.find_separation(track.source_id)
        if artifact is None:
            return ()
        return (
            SeparationSet(
                set_key=_separation_key(artifact),
                created_at=artifact.created_at,
                label="Separation",
                stem_roles=STEM_ROLES,
                based_on_earlier_separation=False,
            ),
        )

    def separation_preview_target(
        self,
        track: Track,
        set_key: str,
        stem: str,
    ) -> PreviewTarget | None:
        """The playable preview of one stem in one Separation set.

        ``stem`` is a Demucs role or ``instrumental``. A missing set, an unknown
        role, or a cleared preview returns ``None`` so the row can be marked
        unavailable instead of pointing at a URL that can only 404.
        """
        artifact = self._separation_artifact(track, set_key)
        if artifact is None or stem not in SEPARATION_OUTPUT_ROLES:
            return None
        path = artifact.path_for(f"{stem}_preview")
        url = self._preview_url(path)
        if url is None:
            return None
        return PreviewTarget(
            key=f"{set_key}:{stem}",
            label=_SEPARATION_ROLE_LABELS[stem],
            url=url,
            gain_db=artifact.gain_db,
        )

    def separate(self, track: Track) -> str:
        """Submit one Separation Job, or raise ``separation.model_missing``.

        A missing model never reaches the runner: the card shows the gate and the
        request is latched so a successful install can resubmit it exactly once
        (ticket 06 §3, ticket 14 §4.3). A checksum failure is only discovered by
        the Job's before-use verification; the page latches that outcome through
        :meth:`remember_pending_separation`.
        """
        try:
            return self._submit_separation(track)
        except StemForgeError as error:
            if error.code == errors.SEPARATION_MODEL_MISSING:
                self.remember_pending_separation(track.track_id)
            raise

    def _submit_separation(self, track: Track) -> str:
        separation = cast("SeparationEngine | None", self._engines.separation)
        media = self._engines.media
        if separation is None or media is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "the separation engines are not configured",
            )
        status = self._models.status()
        if not status.installed:
            raise StemForgeError(
                errors.SEPARATION_MODEL_MISSING,
                "the separation model is not installed",
                detail=f"reason={status.reason or 'absent'}",
                context={
                    "model": status.name,
                    "path": str(status.path),
                    "reason": status.reason or "absent",
                },
            )
        job = Job(
            kind=JobKind.SEPARATE,
            identity=(JobKind.SEPARATE.value, track.source_id),
            title=track.title,
            run=self._separation_run(track, separation, media),
        )
        return self._remember_job(
            self._runner.submit(job),
            track_id=track.track_id,
            source_id=track.source_id,
        )

    def _separation_run(
        self,
        track: Track,
        separation: SeparationEngine,
        media: MediaTool,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_separation_job(
                ctx,
                artifacts=self._artifacts,
                engine=separation,
                media=media,
                models=self._models,
                source_id=track.source_id,
                title=track.title,
            )
            return {
                "track_id": track.track_id,
                "set_key": _separation_key(result.artifact),
            }

        return run

    def _separation_artifact(self, track: Track, set_key: str) -> SeparationArtifact | None:
        artifact = self._artifacts.find_separation(track.source_id)
        if artifact is None or _separation_key(artifact) != set_key:
            return None
        return artifact

    def pending_separation_track_id(self) -> str | None:
        """The Track whose Separation waits for the model install, if any."""
        with self._lock:
            return self._pending_separation_track_id

    def remember_pending_separation(self, track_id: str) -> None:
        """Latch one post-install resubmit; the first request wins."""
        if not track_id:
            raise ValueError("track id must not be empty")
        with self._lock:
            if self._pending_separation_track_id is None:
                self._pending_separation_track_id = track_id

    def resubmit_pending_separation(self, track: Track) -> str | None:
        """Submit the latched Separation exactly once, or ``None``.

        The latch is consumed before the submission is attempted, so a failure
        here (including a second ``model_missing``) cannot loop. ``None`` means
        this Track was not the latched one; nothing is submitted.
        """
        with self._lock:
            if self._pending_separation_track_id != track.track_id:
                return None
            self._pending_separation_track_id = None
        return self._submit_separation(track)

    # -- Export ------------------------------------------------------------

    def preferred_export_dir(self) -> Path | None:
        """The last directory the user exported to, when it still exists."""
        preferred = self._settings.preferred_export_dir
        if preferred is not None and preferred.is_dir():
            return preferred
        return None

    def remember_export_dir(self, directory: Path) -> None:
        """Persist the destination the native save dialog returned (ticket 08 §9)."""
        self._settings.set_export_dir(directory)

    def export_targets(self, track: Track | str) -> tuple[ExportTarget, ...]:
        """Every existing, valid export selection for one Track.

        The master, the unshifted Stems and Instrumental, and every member of
        each committed pitch render set. Missing artifacts and invalid manifests
        are omitted rather than offered as a failing action (ticket 05 §7).
        """
        found = self._find_track(track)
        if found is None:
            return ()
        return enumerate_export_targets(
            self._artifacts,
            source_id=found.source_id,
            title=found.title,
        )

    def submit_export(
        self,
        track: Track | str,
        target: ExportTarget | str,
        fmt: ExportFormat | str,
        destination_dir: Path | str,
        *,
        destination_name: str | None = None,
    ) -> str:
        """Submit one Export Job and return its id.

        ``target`` is a value from :meth:`export_targets` or its stable key. The
        Job re-resolves the key against the committed manifests, so a queued
        Export follows a re-separation instead of a stale path. The native save
        dialog and format sheet (#18) own the file name; ``destination_name``
        carries the dialog's sanitised choice so the Job writes that name.
        """
        found = self._find_track(track)
        if found is None:
            raise KeyError(track.track_id if isinstance(track, Track) else track)
        media = self._engines.media
        if media is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "no media engine is configured for export",
            )

        export_format = ExportFormat(fmt)
        target_key = target.key if isinstance(target, ExportTarget) else str(target)
        if isinstance(target, ExportTarget) and target.source_id != found.source_id:
            raise StemForgeError(
                errors.MEDIA_PROBE_FAILED,
                "the selected export target belongs to another source",
                detail="target",
                context={"source_id": found.source_id, "target": target_key},
            )
        resolved = resolve_export_target(
            self._artifacts,
            source_id=found.source_id,
            key=target_key,
            title=found.title,
        )
        if resolved is None:
            raise StemForgeError(
                errors.MEDIA_PROBE_FAILED,
                "the selected export target is no longer available",
                detail="target",
                context={"source_id": found.source_id, "target": target_key},
            )

        destination = Path(destination_dir).expanduser().absolute()
        job = Job(
            kind=JobKind.EXPORT,
            identity=(
                JobKind.EXPORT.value,
                found.track_id,
                resolved.key,
                export_format.value,
                str(destination),
                destination_name or "-",
            ),
            title=found.title,
            run=self._export_run(
                resolved,
                export_format,
                destination,
                media,
                destination_name,
            ),
        )
        return self._remember_job(
            self._runner.submit(job),
            track_id=found.track_id,
            source_id=found.source_id,
        )

    def export_result(self, job_id: str) -> ExportedFile | None:
        """Return the finished file's facts, or ``None`` before success."""
        try:
            snapshot = self._runner.snapshot(job_id)
        except KeyError:
            return None
        if snapshot.state is not JobState.SUCCEEDED:
            return None
        keys = snapshot.result_keys
        try:
            return ExportedFile(
                job_id=job_id,
                path=Path(keys["path"]),
                format=ExportFormat(keys["format"]),
                size_bytes=int(keys["size"]),
                duration_s=float(keys["duration_s"]),
                gain_db=float(keys["gain_db"]),
            )
        except (KeyError, ValueError):
            return None

    def preview_export(
        self,
        track: Track | str,
        target: ExportTarget | str,
        fmt: ExportFormat | str,
        destination_dir: Path | str,
    ) -> Path:
        """The filename the Job would write, without creating anything.

        The format sheet uses this for its size/name summary; the Job uses the
        same collision sequence when it commits (ticket 08 §9).
        """
        found = self._find_track(track)
        if found is None:
            raise KeyError(track.track_id if isinstance(track, Track) else track)
        target_key = target.key if isinstance(target, ExportTarget) else str(target)
        if isinstance(target, ExportTarget) and target.source_id != found.source_id:
            raise StemForgeError(
                errors.MEDIA_PROBE_FAILED,
                "the selected export target belongs to another source",
                detail="target",
                context={"source_id": found.source_id, "target": target_key},
            )
        resolved = resolve_export_target(
            self._artifacts,
            source_id=found.source_id,
            key=target_key,
            title=found.title,
        )
        if resolved is None:
            raise StemForgeError(
                errors.MEDIA_PROBE_FAILED,
                "the selected export target is no longer available",
                detail="target",
                context={"source_id": found.source_id, "target": target_key},
            )
        destination = Path(destination_dir).expanduser().absolute()
        return unique_export_path(
            destination,
            resolved.base_name,
            ExportFormat(fmt).suffix,
        )

    def _export_run(
        self,
        target: ExportTarget,
        fmt: ExportFormat,
        destination: Path,
        media: MediaTool,
        destination_name: str | None = None,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_export_job(
                ctx,
                artifacts=self._artifacts,
                media=media,
                source_id=target.source_id,
                target=target,
                fmt=fmt,
                destination_dir=destination,
                destination_name=destination_name,
            )
            return {
                "path": str(result.path),
                "format": result.fmt.value,
                "size": str(result.size_bytes),
                "duration_s": f"{result.duration_s:.6f}",
                "gain_db": f"{result.gain_db:.6f}",
                "sha256": result.sha256,
                "manifest": json.dumps(result.manifest, sort_keys=True, ensure_ascii=False),
            }

        return run

    def _find_track(self, track: Track | str) -> Track | None:
        track_id = track.track_id if isinstance(track, Track) else str(track)
        return next(
            (item for item in self.snapshot().tracks if item.track_id == track_id),
            None,
        )

    # -- Model -------------------------------------------------------------

    def model_spec(self) -> ModelSpec:
        """The pinned model identity and licence-notice version."""
        return self._models.spec

    def model_status(self) -> ModelStatus:
        """A cheap installed-or-not check; the Job/verify seam re-hashes before use."""
        return self._models.status()

    def model_consent(self) -> ModelConsent | None:
        return self._settings.model_consent()

    def model_consent_is_current(self) -> bool:
        return consent_is_current(
            self._settings.model_consent(),
            required_version=self._models.spec.licence_notice_version,
        )

    def accept_model_licence(self) -> ModelConsent:
        """Persist the explicit acknowledgement required before downloading."""
        return self._settings.accept_model_licence(version=self._models.spec.licence_notice_version)

    def install_model(
        self,
        *,
        accept_licence: bool = False,
        transport: Transport | None = None,
    ) -> str:
        """Submit a model-install Job and return its id.

        Without a current consent record this raises before submitting, so the
        UI can show the model gate without a failed Job in the history.
        """
        if accept_licence:
            self.accept_model_licence()
        if not self._models.is_installed():
            require_model_consent(
                self._settings.model_consent(),
                required_version=self._models.spec.licence_notice_version,
            )
        spec = self._models.spec
        job = Job(
            kind=JobKind.MODEL_INSTALL,
            identity=(JobKind.MODEL_INSTALL.value, spec.name, spec.revision),
            title=spec.name,
            run=self._model_install_run(self._model_transport if transport is None else transport),
        )
        return self._runner.submit(job)

    def _model_install_run(self, transport: Transport | None) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            job = ModelInstallJob(
                store=self._models,
                consent=self._settings.model_consent(),
                transport=transport,
            )
            result = job.run(ctx)
            return {
                "model": result.status.name,
                "sha256": result.status.sha256 or "",
                "path": str(result.status.path),
            }

        return run

    # -- Jobs ---------------------------------------------------------------

    def jobs(self) -> tuple[JobSnapshot, ...]:
        """Every Job the runner still remembers, newest last."""
        return tuple(self._runner.jobs())

    def job_snapshot(self, job_id: str) -> JobSnapshot:
        """One Job's immutable snapshot; raises ``KeyError`` when forgotten."""
        return self._runner.snapshot(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of one Job (ticket 06 §3)."""
        return self._runner.cancel(job_id)

    def youtube_enabled(self) -> bool:
        """Whether the empty-state YouTube row is enabled (ticket 09 §7)."""
        return self._settings.youtube_enabled

    def youtube_tool_versions(self) -> Mapping[str, str]:
        """The yt-dlp, yt-dlp-ejs, and Deno versions About must show (09 §9).

        About renders from package metadata and the pinned Deno version. It
        never instantiates or calls an engine adapter, so opening the modal
        cannot start a subprocess or block the UI event loop (ADR 0003).
        """
        return dict(shipped_tool_versions())

    def set_youtube_enabled(self, enabled: bool) -> None:
        self._settings.set_youtube_enabled(enabled)

    def youtube_notice_acknowledged(self) -> bool:
        """Whether the one-time YouTube disclaimer was accepted."""
        return self._settings.youtube_notice_is_current()

    def accept_youtube_notice(self) -> None:
        """Persist the one-time YouTube ToS acknowledgment."""
        self._settings.accept_youtube_notice()

    def validate_youtube(self, url: str) -> YouTubeVideo:
        """Validate one YouTube URL for the field; no Job and no service state.

        The UI calls this before the consent gate, so a rejected paste is only a
        field error and never reaches ``submit_youtube``.
        """
        return parse_youtube_url(url)

    def submit_youtube(self, url: str) -> str:
        """Validate one YouTube URL synchronously, then submit its import Job.

        The field-level parser runs first, so a playlist, channel, search, or
        foreign host never creates a Job (ticket 09 §1). The runner dedupes the
        Job identity, so a second submit while any ``import_youtube`` Job is
        active returns that Job's id (ticket 09 §3).
        """
        video = parse_youtube_url(url)
        youtube = self._youtube_tool()
        job = Job(
            kind=JobKind.IMPORT_YOUTUBE,
            identity=(JobKind.IMPORT_YOUTUBE.value,),
            title=video.canonical_url,
            run=self._youtube_run(video, youtube),
        )
        return self._runner.submit(job)

    def _youtube_tool(self) -> YouTubeTool:
        if self._engines.youtube is not None:
            return self._engines.youtube
        return YtDlpImporter(cache_root=self._paths.cache)

    def _youtube_run(self, video: YouTubeVideo, youtube: YouTubeTool) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            media = self._engines.media
            if media is None:
                raise StemForgeError(
                    errors.INTERNAL_UNEXPECTED,
                    "no media engine is configured for YouTube import",
                )
            result = run_youtube_import(
                ctx,
                video,
                artifacts=self._artifacts,
                media=media,
                youtube=youtube,
                finalize=self._accept_source,
                sleep=self._youtube_delay,
            )
            return {
                "source_id": result.source.source_id,
                "track_id": result.track.track_id,
            }

        return run

    def import_local(self, path: str | Path) -> str:
        """Submit a local import Job and return its id (deduped while active)."""
        media = self._engines.media
        if media is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "no media engine is configured for local import",
            )
        source_path = Path(path).expanduser().absolute()
        job = Job(
            kind=JobKind.IMPORT_LOCAL,
            identity=(JobKind.IMPORT_LOCAL.value, str(source_path)),
            title=source_path.name,
            run=self._import_run(source_path, media),
        )
        return self._runner.submit(job)

    def _import_run(self, path: Path, media: MediaTool) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_local_import(
                ctx,
                path,
                artifacts=self._artifacts,
                media=media,
                finalize=self._accept_source,
            )
            return {
                "source_id": result.source.source_id,
                "track_id": result.track.track_id,
            }

        return run

    def _accept_source(self, source: Source) -> Track:
        """Create (or focus) the Track and refresh the recent-files entry."""
        with self._lock:
            existing = self._workspace.find_by_source(source.source_id)
            if existing is not None:
                track = existing
                self._artifacts.touch_source(source.source_id)
            else:
                title = source.title or source.source_id
                track = Track(
                    track_id=uuid4().hex,
                    source=source,
                    title=title,
                )
                self._workspace.add_track(track)
                self._artifacts.retain(source.source_id)

        self._settings.record_recent(
            source_id=source.source_id,
            title=track.title,
            origin=source.origin,
            origin_kind=source.origin_kind,
            duration_s=source.metadata.duration_s,
        )
        return track


@dataclass(frozen=True, slots=True)
class _RenderView:
    """One render artifact with the label, scope, and staleness the UI needs."""

    artifact: RenderArtifact
    semitones: int
    scope: RenderScope
    label: str
    based_on_earlier_separation: bool


def _separation_key(artifact: SeparationArtifact) -> str:
    """A stable, path-safe identity for one committed Separation set."""
    digest = hashlib.sha256(
        f"{artifact.source_id}:{artifact.created_at.isoformat()}".encode()
    ).hexdigest()
    return f"sep-{digest[:16]}"


def _semitone_label(semitones: int) -> str:
    """The tab text for one shift: ``+3``, or a minus-sign prefix when negative."""
    return f"+{semitones}" if semitones > 0 else f"\u2212{abs(semitones)}"


def _render_scope(artifact: RenderArtifact) -> RenderScope | None:
    value = artifact.manifest.get("scope")
    if not isinstance(value, str):
        return None
    try:
        return RenderScope(value)
    except ValueError:
        return None


def _render_semitones(artifact: RenderArtifact) -> int | None:
    value = artifact.manifest.get("semitones")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not MIN_SEMITONES <= value <= MAX_SEMITONES or value == 0:
        return None
    return value


def _recorded_input_hashes(manifest: Mapping[str, object]) -> dict[str, str]:
    raw = manifest.get("inputs")
    if not isinstance(raw, list):
        return {}
    hashes: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        role = entry.get("role")
        digest = entry.get("sha256")
        if isinstance(role, str) and isinstance(digest, str) and digest:
            hashes[role] = digest
    return hashes


def _based_on_earlier_separation(
    artifact: RenderArtifact,
    separation: SeparationArtifact | None,
) -> bool:
    """Whether a stems set's recorded inputs no longer match the current Stems.

    The manifest is the source of truth (ticket 05 §2). Comparison uses the
    separation manifest's recorded stem hashes, never a fresh read of the audio:
    a detail refresh must not hash hundreds of megabytes. If the separation
    records no hashes, the set cannot be proven stale and stays unlabelled.
    """
    recorded = _recorded_input_hashes(artifact.manifest)
    if not recorded:
        return False
    if separation is None:
        return True
    current = {
        entry.role: entry.sha256
        for entry in separation.files
        if entry.role in STEM_ROLES and entry.sha256 is not None
    }
    if not current:
        return False
    roles = {role for role in STEM_ROLES if role in recorded or role in current}
    return any(recorded.get(role) != current.get(role) for role in roles)
