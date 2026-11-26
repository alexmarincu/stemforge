"""The Separation feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Mapping

from stemforge import errors
from stemforge.application.identity import separation_key
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.application.views import SeparationSet
from stemforge.artifacts import ArtifactStore
from stemforge.domain.display import SEPARATION_LABEL_KEY
from stemforge.domain.models import JobKind, Track
from stemforge.domain.roles import STEM_ROLES
from stemforge.engines.media import MediaTool
from stemforge.engines.separation import SeparationEngine
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.models import ModelStore
from stemforge.pipeline.separation_job import run_separation_job


class SeparationService:
    """Owns Separation sets, the model gate, and the post-install resubmit latch."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        ledger: JobLedger,
        workspace: WorkspaceState,
        models: ModelStore,
        separation: SeparationEngine | None,
        media: MediaTool | None,
    ) -> None:
        self._artifacts = artifacts
        self._ledger = ledger
        self._workspace = workspace
        self._models = models
        self._separation = separation
        self._media = media
        self._pending_track_id: str | None = None

    def has_separation(self, track: Track) -> bool:
        return self._artifacts.find_separation(track.source_id) is not None

    def separation_sets(self, track: Track) -> tuple[SeparationSet, ...]:
        """Every committed Separation set for the Track, oldest first.

        The store replaces the Stems directory on a re-separation, so there is
        at most one set. The newest set is the last item and is the card's
        default; the tuple shape keeps older labelled sets representable.
        """
        artifact = self._artifacts.find_separation(track.source_id)
        if artifact is None:
            return ()
        return (
            SeparationSet(
                set_key=separation_key(artifact),
                created_at=artifact.created_at,
                stem_roles=STEM_ROLES,
                based_on_earlier_separation=False,
                label_key=SEPARATION_LABEL_KEY,
            ),
        )

    def separate(self, track: Track) -> str:
        """Submit one Separation Job, or raise ``separation.model_missing``.

        A missing model never reaches the runner: the card shows the gate and
        the request is latched so a successful install can resubmit it exactly
        once. A checksum failure is only discovered by the Job's before-use
        verification; the page latches that outcome through
        :meth:`remember_pending_separation`.
        """
        try:
            return self._submit_separation(track)
        except StemForgeError as error:
            if error.code == errors.SEPARATION_MODEL_MISSING:
                self.remember_pending_separation(track.track_id)
            raise

    def _submit_separation(self, track: Track) -> str:
        separation = self._separation
        media = self._media
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
        return self._ledger.submit(
            job,
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
                "set_key": separation_key(result.artifact),
            }

        return run

    def pending_separation_track_id(self) -> str | None:
        with self._workspace.lock:
            return self._pending_track_id

    def remember_pending_separation(self, track_id: str) -> None:
        if not track_id:
            raise ValueError("track id must not be empty")
        with self._workspace.lock:
            if self._pending_track_id is None:
                self._pending_track_id = track_id

    def resubmit_pending_separation(self, track: Track) -> str | None:
        """Submit the latched Separation exactly once, or ``None``.

        The latch is consumed before the submission is attempted, so a failure
        here (including a second ``model_missing``) cannot loop. ``None`` means
        this Track was not the latched one; nothing is submitted.
        """
        with self._workspace.lock:
            if self._pending_track_id != track.track_id:
                return None
            self._pending_track_id = None
        return self._submit_separation(track)
