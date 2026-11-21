"""The Export feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from stemforge import errors
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.application.views import ExportedFile
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, JobState, Track
from stemforge.engines.media import ExportFormat, MediaTool
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.pipeline.export_job import ExportTarget, resolve_export_target, run_export_job
from stemforge.pipeline.export_job import export_targets as enumerate_export_targets
from stemforge.pipeline.export_names import unique_export_path
from stemforge.settings import SettingsStore


class ExportService:
    """Owns export target enumeration, submission, and result reads."""

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
        media = self._media
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
        return self._ledger.submit(
            job,
            track_id=found.track_id,
            source_id=found.source_id,
        )

    def export_result(self, job_id: str) -> ExportedFile | None:
        """Return the finished file's facts, or ``None`` before success."""
        try:
            snapshot = self._ledger.snapshot(job_id)
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
        return self._workspace.find(track_id)
