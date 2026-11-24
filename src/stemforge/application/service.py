"""``WorkspaceService``: the only API the UI calls.

The facade owns the composition root and the cross-feature operations: it wires
the per-feature collaborators (ADR 0013), delegates their reads and Job
submissions, and keeps ``remove_track`` and ``open_recent`` here because each
spans more than one feature. It is constructible headless: no NiceGUI, no torch,
and injectable placeholders for the ArtifactStore, JobRunner, and engine adapters.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from stemforge import errors
from stemforge.artifacts import ArtifactStore, ClearReport, EvictionReport, SweepReport
from stemforge.domain.methods import DEFAULT_METHOD_ID
from stemforge.domain.models import ModelConsent, OriginKind, RenderScope, Track
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.engines.media import ExportFormat as ExportFormat
from stemforge.engines.media import MediaTool
from stemforge.engines.pitch import PitchEngine
from stemforge.engines.separation import SeparationEngine
from stemforge.engines.youtube import YouTubeTool, YouTubeVideo
from stemforge.errors import StemForgeError
from stemforge.jobs.models import JobSnapshot
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelSpec, ModelStatus, ModelStore
from stemforge.pipeline.export_job import ExportTarget as ExportTarget
from stemforge.pipeline.model_job import Transport
from stemforge.platform import resources
from stemforge.platform.paths import AppPaths
from stemforge.settings import RecentFile, SettingsStore

from .collaborators.cache import CacheService
from .collaborators.export import ExportService
from .collaborators.importing import ImportService
from .collaborators.model import ModelService
from .collaborators.pitch import PitchService
from .collaborators.preview import PreviewService
from .collaborators.recent import RecentService
from .collaborators.separation import SeparationService
from .collaborators.youtube import YouTubeService
from .state import JobLedger, WorkspaceState
from .views import (
    ExportedFile,
    LicenceFiles,
    PitchSet,
    PreviewTarget,
    RemoveOutcome,
    SeparationAvailability,
    SeparationMethodView,
    SeparationSet,
)


@dataclass(frozen=True, slots=True)
class ServiceEngines:
    """The engine adapters a WorkspaceService may call; tests inject fakes."""

    separation: object | None = None
    pitch: object | None = None
    media: MediaTool | None = None
    youtube: YouTubeTool | None = None


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
        workspace: Workspace | None = None,
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
        self._state = WorkspaceState(workspace)
        self._ledger = JobLedger(self._runner, self._state.lock)
        self._cache = CacheService(self._artifacts)
        self._model = ModelService(self._settings, self._models, self._ledger, model_transport)
        self._imports = ImportService(
            self._settings,
            self._artifacts,
            self._ledger,
            self._state,
            self._engines.media,
        )
        self._youtube = YouTubeService(
            self._settings,
            self._paths,
            self._artifacts,
            self._ledger,
            accept_source=self._imports.accept_source,
            youtube=self._engines.youtube,
            media=self._engines.media,
            delay=youtube_delay,
        )
        self._recent = RecentService(self._settings)
        self._export = ExportService(
            self._settings,
            self._artifacts,
            self._ledger,
            self._state,
            self._engines.media,
        )
        self._preview = PreviewService(self._artifacts, self._state)
        self._pitch = PitchService(
            self._artifacts,
            self._ledger,
            cast("PitchEngine | None", self._engines.pitch),
            self._engines.media,
        )
        self._separation = SeparationService(
            self._artifacts,
            self._ledger,
            self._state,
            self._models,
            cast("SeparationEngine | None", self._engines.separation),
            self._engines.media,
            self._settings,
        )

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
        return self._state.snapshot()

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._recent.recent_files()

    def remove_recent(self, source_id: str) -> bool:
        return self._recent.remove_recent(source_id)

    def recent_available(self, source_id: str) -> bool:
        """Whether a recent entry can be opened without asking the user to find it."""
        return self._recent.recent_available(source_id)

    def open_recent(self, source_id: str) -> str | None:
        """Re-import one recent entry and return its Job id.

        This is cross-feature composition, so it stays on the facade (ADR 0013):
        the entry is either a local file handled by ``ImportService`` or a
        YouTube URL handled by ``YouTubeService``. A missing local file raises
        the coded ``import.file_unavailable`` before any Job starts (nothing
        starts until the user acts).
        """
        entry = self._recent.find(source_id)
        if entry is None:
            raise KeyError(source_id)
        if entry.origin_kind is OriginKind.YOUTUBE:
            return self._youtube.submit_youtube(entry.origin)
        path = Path(entry.origin)
        if not path.is_file():
            raise StemForgeError(
                errors.IMPORT_FILE_UNAVAILABLE,
                "the recent file is no longer readable",
                detail="missing",
                context={"path": str(path)},
            )
        return self._imports.import_local(path)

    def remove_track(self, track_id: str) -> RemoveOutcome:
        """Remove a Track and delete its cached audio when nothing else uses it.

        Removing the Track that owns an active Job cancels that Job first and
        waits for the runner to acknowledge it, so the runner can
        never delete a cache subtree that a Job is still writing.
        """
        track = self._state.find(track_id)
        if track is None:
            raise KeyError(track_id)
        owned = self._ledger.active_owned_job_ids(
            track_id=track.track_id,
            source_id=track.source_id,
        )
        for job_id in owned:
            self._runner.cancel(job_id)
        if owned:
            self._runner.wait_idle(timeout=5.0)
        with self._state.lock:
            track = self._state.remove_track(track_id)
            self._cache.release_reference(track.source_id)
            still_live = self._cache.is_live(track.source_id)
            self._ledger.forget(owned)
        self._separation.forget_track(track_id)
        if still_live:
            return RemoveOutcome(track=track, cache_removed=False)
        report = self._cache.remove_source(track.source_id)
        removed = report.source_removed or bool(report.renders_removed)
        return RemoveOutcome(track=track, cache_removed=removed, report=report)

    def sweep_stale(self) -> SweepReport:
        """Run the startup sweep once; the UI shows recovery copy when it deleted work."""
        return self._cache.sweep_stale()

    def enforce_cache_cap(self) -> EvictionReport:
        """Run the startup LRU pass after the sweep."""
        return self._cache.enforce_cache_cap()

    def cache_usage_bytes(self) -> int:
        """The cache meter's used size; the UI never reads the store directly."""
        return self._cache.cache_usage_bytes()

    def cache_cap_bytes(self) -> int:
        """The configured cache ceiling."""
        return self._cache.cache_cap_bytes()

    def logs_dir(self) -> Path:
        """The app's log root, shown and opened from Settings & About."""
        return self._paths.logs

    def youtube_cache_bytes(self) -> int:
        """The size of ``<cache>/yt-dlp/`` shown in Settings & About."""
        return self._cache.youtube_cache_bytes()

    def clear_youtube_cache(self) -> int:
        """Delete only ``<cache>/yt-dlp/`` and return the bytes freed."""
        return self._cache.clear_youtube_cache()

    def clear_cache(self) -> ClearReport:
        """Delete every non-live cached artifact plus the tool caches."""
        return self._cache.clear_cache()

    def clear_recent(self) -> None:
        """Clear the recent-files list; cached audio is untouched."""
        self._recent.clear_recent()

    # -- Preview -----------------------------------------------------------

    def has_preview(self, track_id: str) -> bool:
        """Whether the Track's source preview exists inside the cache."""
        return self._preview.has_preview(track_id)

    def preview_targets(self, track_id: str) -> tuple[PreviewTarget, ...]:
        """The playable previews for one Track, original first."""
        return self._preview.preview_targets(track_id)

    def pitch_render_preview_target(
        self,
        track: Track,
        render_key: str,
        role: str,
    ) -> PreviewTarget | None:
        """The preview of one member of a Render set."""
        return self._preview.pitch_render_preview_target(track, render_key, role)

    def pitch_preview_target(self, track: Track, render_key: str) -> PreviewTarget | None:
        """The playable preview of one render set, or ``None`` when it is gone."""
        return self._preview.pitch_preview_target(track, render_key)

    # -- Pitch -------------------------------------------------------------

    def pitch_sets(self, track: Track) -> tuple[PitchSet, ...]:
        """Every committed render set for the Track, oldest first."""
        return self._pitch.pitch_sets(track)

    def pitch_set(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> PitchSet | None:
        """The set that satisfies a Render request for ``(semitones, scope)``."""
        return self._pitch.pitch_set(
            track,
            semitones,
            scope,
            separation_method=self._separation.separation_method(track),
        )

    def render_pitch(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> str:
        """Submit one Pitch Render Job for the selected Separation method."""
        return self._pitch.render_pitch(
            track,
            semitones,
            scope,
            separation_method=self._separation.separation_method(track),
        )

    # -- Separation --------------------------------------------------------

    def has_separation(self, track: Track) -> bool:
        """Whether the selected method has a committed set for this Track."""
        return self._separation.has_separation(track)

    def separation_availability(self, track: Track) -> tuple[SeparationAvailability, ...]:
        """The methods that may run on this Track, with a reason when one may not."""
        return self._separation.separation_availability(track)

    def separation_methods(self) -> tuple[SeparationMethodView, ...]:
        """The Separation methods the card's selector offers."""
        return self._separation.separation_methods()

    def separation_method(self, track: Track) -> str:
        """The selected method id for this Track, after resolution."""
        return self._separation.separation_method(track)

    def selected_separation_set(self, track: Track) -> SeparationSet | None:
        """The selected method's newest committed set, or ``None``."""
        return self._separation.selected_separation_set(track)

    def select_separation_method(self, track: Track, method_id: str) -> None:
        """Change the per-Track view selection without persisting or submitting."""
        self._separation.select_separation_method(track, method_id)

    def separation_sets(self, track: Track) -> tuple[SeparationSet, ...]:
        """Every committed Separation set for the Track, oldest first."""
        return self._separation.separation_sets(track)

    def separation_preview_target(
        self,
        track: Track,
        set_key: str,
        stem: str,
    ) -> PreviewTarget | None:
        """The playable preview of one stem in one Separation set."""
        return self._preview.separation_preview_target(track, set_key, stem)

    def separate(self, track: Track, method: str | None = None) -> str:
        """Submit one Separation Job for the selected method or ``method``."""
        return self._separation.separate(track, method)

    def pending_separation(self) -> tuple[str, str] | None:
        """The latched post-install resubmit, as ``(track_id, method)``."""
        return self._separation.pending_separation()

    def pending_separation_track_id(self) -> str | None:
        """The Track whose Separation waits for the model install, if any."""
        return self._separation.pending_separation_track_id()

    def remember_pending_separation(self, track_id: str, method: str = DEFAULT_METHOD_ID) -> None:
        """Latch one post-install resubmit; the first request wins."""
        self._separation.remember_pending_separation(track_id, method)

    def resubmit_pending_separation(self, track: Track) -> str | None:
        """Submit the latched Separation exactly once, or ``None``."""
        return self._separation.resubmit_pending_separation(track)

    # -- Export ------------------------------------------------------------

    def preferred_export_dir(self) -> Path | None:
        """The last directory the user exported to, when it still exists."""
        return self._export.preferred_export_dir()

    def remember_export_dir(self, directory: Path) -> None:
        """Persist the destination the native save dialog returned."""
        self._export.remember_export_dir(directory)

    def export_targets(self, track: Track | str) -> tuple[ExportTarget, ...]:
        """Every existing, valid export selection for one Track.

        The Track's selected Separation method decides which set is offered;
        every committed Render set stays listed regardless of its method.
        """
        found = self._state.find(track.track_id if isinstance(track, Track) else str(track))
        if found is None:
            return ()
        return self._export.export_targets(
            found,
            separation_method=self._separation.separation_method(found),
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
        """Submit one Export Job and return its id."""
        return self._export.submit_export(
            track,
            target,
            fmt,
            destination_dir,
            destination_name=destination_name,
        )

    def export_result(self, job_id: str) -> ExportedFile | None:
        """Return the finished file's facts, or ``None`` before success."""
        return self._export.export_result(job_id)

    def preview_export(
        self,
        track: Track | str,
        target: ExportTarget | str,
        fmt: ExportFormat | str,
        destination_dir: Path | str,
    ) -> Path:
        """The filename the Job would write, without creating anything."""
        return self._export.preview_export(track, target, fmt, destination_dir)

    # -- Model -------------------------------------------------------------

    def model_spec(self) -> ModelSpec:
        """The pinned model identity and licence-notice version."""
        return self._model.model_spec()

    def model_status(self) -> ModelStatus:
        """A cheap installed-or-not check; the Job/verify seam re-hashes before use."""
        return self._model.model_status()

    def model_consent(self) -> ModelConsent | None:
        return self._model.model_consent()

    def model_consent_is_current(self) -> bool:
        return self._model.model_consent_is_current()

    def accept_model_licence(self) -> ModelConsent:
        """Persist the explicit acknowledgement required before downloading."""
        return self._model.accept_model_licence()

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
        return self._model.install_model(accept_licence=accept_licence, transport=transport)

    # -- Jobs ---------------------------------------------------------------

    def jobs(self) -> tuple[JobSnapshot, ...]:
        """Every Job the runner still remembers, newest last."""
        return self._ledger.jobs()

    def job_snapshot(self, job_id: str) -> JobSnapshot:
        """One Job's immutable snapshot; raises ``KeyError`` when forgotten."""
        return self._ledger.snapshot(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of one Job."""
        return self._ledger.cancel(job_id)

    def youtube_enabled(self) -> bool:
        """Whether the empty-state YouTube row is enabled."""
        return self._youtube.youtube_enabled()

    def youtube_tool_versions(self) -> Mapping[str, str]:
        """The yt-dlp, yt-dlp-ejs, and Deno versions About must show.

        About renders from package metadata and the pinned Deno version. It
        never instantiates or calls an engine adapter, so opening the modal
        cannot start a subprocess or block the UI event loop (ADR 0003).
        """
        return self._youtube.youtube_tool_versions()

    def set_youtube_enabled(self, enabled: bool) -> None:
        self._youtube.set_youtube_enabled(enabled)

    def youtube_notice_acknowledged(self) -> bool:
        """Whether the one-time YouTube disclaimer was accepted."""
        return self._youtube.youtube_notice_acknowledged()

    def accept_youtube_notice(self) -> None:
        """Persist the one-time YouTube ToS acknowledgment."""
        self._youtube.accept_youtube_notice()

    def validate_youtube(self, url: str) -> YouTubeVideo:
        """Validate one YouTube URL for the field; no Job and no service state.

        The UI calls this before the consent gate, so a rejected paste is only a
        field error and never reaches ``submit_youtube``.
        """
        return self._youtube.validate_youtube(url)

    def submit_youtube(self, url: str) -> str:
        """Validate one YouTube URL synchronously, then submit its import Job.

        The field-level parser runs first, so a playlist, channel, search, or
        foreign host never creates a Job. The runner dedupes the
        Job identity, so a second submit while any ``import_youtube`` Job is
        active returns that Job's id.
        """
        return self._youtube.submit_youtube(url)

    def import_local(self, path: str | Path) -> str:
        """Submit a local import Job and return its id (deduped while active)."""
        return self._imports.import_local(path)
