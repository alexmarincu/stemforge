"""The local-import feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

from stemforge import errors
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, Source, Track
from stemforge.engines.media import MediaTool
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.pipeline.import_job import run_local_import
from stemforge.settings import SettingsStore


class ImportService:
    """Owns local-file import and the one finalization path every import shares."""

    def __init__(
        self,
        settings: SettingsStore,
        artifacts: ArtifactStore,
        ledger: JobLedger,
        workspace: WorkspaceState,
        media: MediaTool | None,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts
        self._ledger = ledger
        self._workspace = workspace
        self._media = media

    def import_local(self, path: str | Path) -> str:
        media = self._media
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
        return self._ledger.submit(job)

    def accept_source(self, source: Source) -> Track:
        """Create (or focus) the Track and refresh the recent-files entry."""
        with self._workspace.lock:
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

    def _import_run(self, path: Path, media: MediaTool) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_local_import(
                ctx,
                path,
                artifacts=self._artifacts,
                media=media,
                finalize=self.accept_source,
            )
            return {
                "source_id": result.source.source_id,
                "track_id": result.track.track_id,
            }

        return run
