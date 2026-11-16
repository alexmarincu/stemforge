"""``WorkspaceService``: the only API the UI calls (ticket 06 §2).

The service owns the Workspace and orchestrates Jobs on behalf of the UI. It is
constructible headless: no NiceGUI, no torch, and injectable placeholders for the
ArtifactStore, JobRunner, and engine adapters (ticket 06 §13). Local import is
wired here; Separation, Render, and Export follow in their tickets.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from stemforge import errors
from stemforge.artifacts import ArtifactStore, RemoveReport, SweepReport
from stemforge.domain.models import JobKind, ModelConsent, OriginKind, Source, Track
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.engines.media import MediaTool
from stemforge.engines.youtube import YouTubeTool, YouTubeVideo, YtDlpImporter, parse_youtube_url
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelSpec, ModelStatus, ModelStore
from stemforge.pipeline.import_job import run_local_import
from stemforge.pipeline.model_job import (
    ModelInstallJob,
    Transport,
    consent_is_current,
    require_model_consent,
)
from stemforge.pipeline.youtube_job import run_youtube_import
from stemforge.platform.paths import AppPaths
from stemforge.settings import RecentFile, SettingsStore


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
        youtube_delay: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._paths = paths if paths is not None else settings.paths
        self._artifacts = (
            artifacts
            if artifacts is not None
            else ArtifactStore(self._paths, cap_bytes=self._settings.settings.cache_cap_bytes)
        )
        self._runner = runner if runner is not None else JobRunner(paths=self._paths)
        self._engines = engines if engines is not None else ServiceEngines()
        self._models = models if models is not None else ModelStore(self._paths)
        self._youtube_delay = youtube_delay
        self._workspace = Workspace()
        self._lock = threading.Lock()

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
        """Remove a Track and delete its cached audio when nothing else uses it."""
        with self._lock:
            track = self._workspace.remove_track(track_id)
            self._artifacts.release(track.source_id)
            still_live = self._artifacts.is_live(track.source_id)
        if still_live:
            return RemoveOutcome(track=track, cache_removed=False)
        report = self._artifacts.remove_source(track.source_id)
        removed = report.source_removed or bool(report.renders_removed)
        return RemoveOutcome(track=track, cache_removed=removed, report=report)

    def sweep_stale(self) -> SweepReport:
        """Run the startup sweep once; the UI shows recovery copy when it deleted work."""
        return self._artifacts.sweep_stale()

    def youtube_cache_bytes(self) -> int:
        """The size of ``<cache>/yt-dlp/`` shown in Settings & About."""
        return self._artifacts.youtube_cache_bytes()

    def clear_youtube_cache(self) -> int:
        """Delete only ``<cache>/yt-dlp/`` and return the bytes freed."""
        return self._artifacts.clear_youtube_cache()

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
        URL that can only 404.
        """
        track = next(
            (item for item in self.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return ()
        targets: list[PreviewTarget] = []
        source_url = self._preview_url(track.source.preview_path)
        if source_url is not None:
            targets.append(PreviewTarget(key="original", label="Original", url=source_url))
        renders = sorted(self._artifacts.iter_renders(), key=lambda item: item.created_at)
        for artifact in renders:
            if artifact.source_id != track.source_id:
                continue
            preview = artifact.path_for("original_preview")
            url = self._preview_url(preview)
            if url is None:
                continue
            targets.append(
                PreviewTarget(
                    key=artifact.render_key,
                    label=_render_label(artifact.manifest),
                    url=url,
                )
            )
        return tuple(targets)

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
            run=self._model_install_run(transport),
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

    def youtube_enabled(self) -> bool:
        """Whether the empty-state YouTube row is enabled (ticket 09 §7)."""
        return self._settings.youtube_enabled

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
        return YtDlpImporter(cache_dir=self._paths.cache / "yt-dlp")

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


def _render_label(manifest: Mapping[str, object]) -> str:
    """A version label for the A/B hook: ``+3`` or ``+3 stems``."""
    semitones = manifest.get("semitones")
    label = (
        f"{semitones:+d}"
        if isinstance(semitones, int) and not isinstance(semitones, bool)
        else "Render"
    )
    if manifest.get("scope") == "stems":
        label += " stems"
    return label
