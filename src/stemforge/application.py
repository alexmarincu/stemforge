"""``WorkspaceService``: the only API the UI calls (ticket 06 §2).

The service owns the Workspace and orchestrates Jobs on behalf of the UI. It is
constructible headless: no NiceGUI, no torch, and injectable placeholders for the
ArtifactStore, JobRunner, and engine adapters (ticket 06 §13). Local import is
wired here; Separation, Render, and Export follow in their tickets.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, Source, Track
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.engines.media import MediaTool
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.jobs.runner import JobRunner
from stemforge.pipeline.import_job import run_local_import
from stemforge.platform.paths import AppPaths
from stemforge.settings import RecentFile, SettingsStore


@dataclass(frozen=True, slots=True)
class ServiceEngines:
    """The engine adapters a WorkspaceService may call; tests inject fakes."""

    separation: object | None = None
    pitch: object | None = None
    media: MediaTool | None = None
    youtube: object | None = None


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

    def snapshot(self) -> WorkspaceSnapshot:
        """Return an immutable view of the Workspace for the UI."""
        with self._lock:
            return self._workspace.snapshot()

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._settings.recent_files()

    def remove_recent(self, source_id: str) -> bool:
        return self._settings.remove_recent(source_id)

    # -- Jobs ---------------------------------------------------------------

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
