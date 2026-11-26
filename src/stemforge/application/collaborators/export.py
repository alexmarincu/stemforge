"""The Export feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from stemforge import errors
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.application.views import (
    ExportBatchItem,
    ExportBatchResult,
    ExportedFile,
    ImportedFile,
    SavedOriginal,
)
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import ExportBatchItemState, JobKind, JobState, Track
from stemforge.engines.media import ExportFormat, MediaTool
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.pipeline.export_batch_job import (
    ExportBatchItemOutcome,
    batch_targets,
    run_export_batch_job,
)
from stemforge.pipeline.export_job import ExportTarget, resolve_export_target, run_export_job
from stemforge.pipeline.export_job import export_targets as enumerate_export_targets
from stemforge.pipeline.export_names import suggest_base_name, unique_export_path
from stemforge.pipeline.save_original_job import require_native_file, run_save_original_job
from stemforge.settings import SettingsStore


class _BatchOutcomeRecord:
    """The lock-guarded per-item record for one batch Job (ADR 0015).

    It is created before the Job is submitted, so the worker can write an
    outcome the moment it starts, and it is stored by job id once the runner
    assigns one. Reads happen after a terminal state; the lock keeps the record
    safe across the worker and UI threads.
    """

    def __init__(
        self,
        *,
        track_id: str,
        source_id: str,
        version_key: str,
        export_format: ExportFormat,
        destination_dir: Path,
        targets: Sequence[ExportTarget],
    ) -> None:
        self._lock = threading.Lock()
        self._track_id = track_id
        self._source_id = source_id
        self._version_key = version_key
        self._format = export_format
        self._destination = destination_dir
        self._items = [
            ExportBatchItem(
                key=target.key,
                label=target.label,
                state=ExportBatchItemState.NOT_EXPORTED,
                label_key=target.label_key,
                label_context=target.label_context,
            )
            for target in targets
        ]

    def record(self, outcome: ExportBatchItemOutcome) -> None:
        item = ExportBatchItem(
            key=outcome.key,
            label=outcome.label,
            state=outcome.state,
            path=outcome.path,
            label_key=outcome.label_key,
            label_context=outcome.label_context,
            error_code=outcome.error.code if outcome.error is not None else None,
            error_detail=outcome.error.detail if outcome.error is not None else None,
            error_context=(
                outcome.error.context if outcome.error is not None else MappingProxyType({})
            ),
        )
        with self._lock:
            self._items = [
                item if existing.key == outcome.key else existing for existing in self._items
            ]

    def snapshot(self) -> ExportBatchResult:
        with self._lock:
            items = tuple(self._items)
        return ExportBatchResult(
            track_id=self._track_id,
            source_id=self._source_id,
            version_key=self._version_key,
            format=self._format,
            destination_dir=self._destination,
            items=items,
        )


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
        self._batch_lock = threading.Lock()
        self._batch_outcomes: dict[str, _BatchOutcomeRecord] = {}

    def preferred_export_dir(self) -> Path | None:
        preferred = self._settings.preferred_export_dir
        if preferred is not None and preferred.is_dir():
            return preferred
        return None

    def remember_export_dir(self, directory: Path) -> None:
        self._settings.set_export_dir(directory)

    def export_targets(self, track: Track | str) -> tuple[ExportTarget, ...]:
        """Every existing, valid export selection for one Track.

        The master, the unshifted Stems and Instrumental, and every member of
        each committed pitch render set. Missing artifacts and invalid manifests
        are omitted rather than offered as a failing action.
        """
        found = self._find_track(track)
        if found is None:
            return ()
        return enumerate_export_targets(
            self._artifacts,
            source_id=found.source_id,
            title=found.title,
        )

    def export_batch_targets(
        self,
        track: Track | str,
        version_key: str,
    ) -> tuple[ExportTarget, ...]:
        """The active version's outputs, in card order, without the master.

        This is the one plan the card action, the sheet, and the Job share, so a
        batch never mixes versions or picks up the Original.
        """
        return batch_targets(self.export_targets(track), version_key=version_key)

    def submit_export_batch(
        self,
        track: Track | str,
        version_key: str,
        fmt: ExportFormat | str,
        destination_dir: Path | str,
        *,
        target_keys: Sequence[str] | None = None,
    ) -> str:
        """Submit one batch Export Job and return its id.

        ``target_keys`` selects a subset for recovery; the order is always the
        batch plan's order, so a failed-only or remaining-only resubmission
        keeps the card's primary-first sequence. The runner returns an existing
        job id for a duplicate identity; the existing record is kept.
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
        selected = _select_targets(
            self.export_batch_targets(found, version_key),
            target_keys,
        )
        if not selected:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "there are no outputs to export",
                detail="target",
                context={"track_id": found.track_id, "version": version_key},
            )

        destination = Path(destination_dir).expanduser().absolute()
        identity = (
            JobKind.EXPORT_BATCH.value,
            found.track_id,
            version_key,
            export_format.value,
            str(destination),
            *(target.key for target in selected),
        )
        outcome = _BatchOutcomeRecord(
            track_id=found.track_id,
            source_id=found.source_id,
            version_key=version_key,
            export_format=export_format,
            destination_dir=destination,
            targets=selected,
        )
        job = Job(
            kind=JobKind.EXPORT_BATCH,
            identity=identity,
            title=found.title,
            run=self._export_batch_run(
                found,
                selected,
                export_format,
                destination,
                media,
                outcome,
            ),
        )
        job_id = self._ledger.submit(
            job,
            track_id=found.track_id,
            source_id=found.source_id,
        )
        self._remember_batch(job_id, outcome)
        return job_id

    def export_batch_result(self, job_id: str) -> ExportBatchResult | None:
        """The batch's last recorded per-item outcomes, or ``None`` if unknown."""
        with self._batch_lock:
            outcome = self._batch_outcomes.get(job_id)
        return None if outcome is None else outcome.snapshot()

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
        resolved = self._resolve_target(found, target)

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
        same collision sequence when it commits.
        """
        found = self._find_track(track)
        if found is None:
            raise KeyError(track.track_id if isinstance(track, Track) else track)
        resolved = self._resolve_target(found, target)
        destination = Path(destination_dir).expanduser().absolute()
        return unique_export_path(
            destination,
            resolved.base_name,
            ExportFormat(fmt).suffix,
        )

    def imported_file(self, track: Track | str) -> ImportedFile | None:
        """Read the manifest's native entry for the sheet, without hashing or probing.

        A YouTube Source is omitted because its native file is the downloaded
        container, not a file the user supplied.
        """
        found = self._find_track(track)
        if found is None:
            return None
        try:
            native = require_native_file(self._artifacts, found.source_id)
        except StemForgeError:
            return None
        return ImportedFile(
            base_name=suggest_base_name(found.title),
            suffix=native.suffix,
            size_bytes=native.size_bytes,
        )

    def submit_save_original(
        self,
        track: Track | str,
        destination_dir: Path | str,
        *,
        destination_name: str | None = None,
    ) -> str:
        """Guard the Source as local, then queue the copy Job.

        The local-only guard runs before submission, so a direct call for a
        YouTube Source fails ``export.original_unavailable``/``not_local``
        instead of queuing a Job that can never succeed. No media engine is
        needed: the Job copies the Native file and never probes or encodes.
        """
        found = self._find_track(track)
        if found is None:
            raise KeyError(track.track_id if isinstance(track, Track) else track)

        require_native_file(self._artifacts, found.source_id)
        destination = Path(destination_dir).expanduser().absolute()
        job = Job(
            kind=JobKind.SAVE_ORIGINAL,
            identity=(
                JobKind.SAVE_ORIGINAL.value,
                found.track_id,
                str(destination),
                destination_name or "-",
            ),
            title=found.title,
            run=self._save_original_run(
                found,
                destination,
                destination_name,
            ),
        )
        return self._ledger.submit(
            job,
            track_id=found.track_id,
            source_id=found.source_id,
        )

    def save_original_result(self, job_id: str) -> SavedOriginal | None:
        """Read the runner's result keys once the Job reports success."""
        try:
            snapshot = self._ledger.snapshot(job_id)
        except KeyError:
            return None
        if snapshot.state is not JobState.SUCCEEDED:
            return None
        keys = snapshot.result_keys
        try:
            return SavedOriginal(
                job_id=job_id,
                path=Path(keys["path"]),
                size_bytes=int(keys["size"]),
                sha256=keys["sha256"],
            )
        except (KeyError, ValueError):
            return None

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

    def _save_original_run(
        self,
        track: Track,
        destination: Path,
        destination_name: str | None,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_save_original_job(
                ctx,
                artifacts=self._artifacts,
                source_id=track.source_id,
                title=track.title,
                destination_dir=destination,
                destination_name=destination_name,
            )
            return {
                "path": str(result.path),
                "size": str(result.size_bytes),
                "sha256": result.sha256,
            }

        return run

    def _export_batch_run(
        self,
        track: Track,
        targets: Sequence[ExportTarget],
        fmt: ExportFormat,
        destination: Path,
        media: MediaTool,
        outcome: _BatchOutcomeRecord,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            return run_export_batch_job(
                ctx,
                artifacts=self._artifacts,
                media=media,
                source_id=track.source_id,
                targets=targets,
                fmt=fmt,
                destination_dir=destination,
                record=outcome.record,
            )

        return run

    def _remember_batch(self, job_id: str, outcome: _BatchOutcomeRecord) -> None:
        """Store the record and drop entries whose Job left the runner history."""
        with self._batch_lock:
            if job_id in self._batch_outcomes:
                return
            self._batch_outcomes[job_id] = outcome
            live = {snapshot.job_id for snapshot in self._ledger.jobs()}
            for stale in tuple(self._batch_outcomes):
                if stale not in live:
                    self._batch_outcomes.pop(stale, None)

    def _resolve_target(self, found: Track, target: ExportTarget | str) -> ExportTarget:
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
        return resolved

    def _find_track(self, track: Track | str) -> Track | None:
        track_id = track.track_id if isinstance(track, Track) else str(track)
        return self._workspace.find(track_id)


def _select_targets(
    available: Sequence[ExportTarget],
    target_keys: Sequence[str] | None,
) -> tuple[ExportTarget, ...]:
    """Filter the plan to a recovery subset while keeping the plan's order."""
    if target_keys is None:
        return tuple(available)
    wanted = set(target_keys)
    selected = tuple(target for target in available if target.key in wanted)
    missing = wanted.difference(target.key for target in selected)
    if missing:
        raise StemForgeError(
            errors.MEDIA_PROBE_FAILED,
            "a selected export target is no longer available",
            detail="target",
            context={"targets": sorted(missing)},
        )
    return selected
