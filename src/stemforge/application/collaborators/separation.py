"""The Separation feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from stemforge import errors
from stemforge.application.identity import separation_key
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.application.views import SeparationSet
from stemforge.artifacts import ArtifactStore
from stemforge.domain.methods import (
    DEFAULT_METHOD_ID,
    auto_method_id,
    method_by_id,
)
from stemforge.domain.models import JobKind, Track
from stemforge.engines.media import MediaTool
from stemforge.engines.separation import SeparationEngine
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.models import ModelStore
from stemforge.pipeline.separation_job import run_separation_job

_LOG = logging.getLogger(__name__)


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
        return bool(self._artifacts.separation_sets(track.source_id))

    def separation_sets(self, track: Track) -> tuple[SeparationSet, ...]:
        """Project each committed artifact through its method descriptor."""
        views: list[SeparationSet] = []
        for artifact in self._artifacts.separation_sets(track.source_id):
            descriptor = method_by_id(artifact.method)
            if descriptor is None:
                continue
            views.append(
                SeparationSet(
                    set_key=separation_key(artifact),
                    created_at=artifact.created_at,
                    label=descriptor.label,
                    method=artifact.method,
                    method_version=artifact.method_version,
                    output_roles=descriptor.output_roles,
                    stem_roles=descriptor.stems,
                    based_on_earlier_separation=False,
                )
            )
        return tuple(views)

    def separate(self, track: Track, method: str | None = None) -> str:
        """Submit one Separation Job with the requested or automatic method.

        An unset method resolves to Demucs when installed and Karaoke otherwise.
        Only a model-requiring method checks the model store; a missing model
        raises ``separation.model_missing`` and latches one post-install
        resubmit, while Karaoke never reads the store and never latches.
        """
        requested = self._resolve_method(method)
        descriptor = method_by_id(requested)
        assert descriptor is not None
        try:
            return self._submit_separation(track, requested)
        except StemForgeError as error:
            if error.code == errors.SEPARATION_MODEL_MISSING and descriptor.requires_model:
                self.remember_pending_separation(track.track_id)
            raise

    def _resolve_method(self, method: str | None) -> str:
        if method is None:
            return auto_method_id(demucs_installed=self._models.status().installed)
        if method_by_id(method) is None:
            _LOG.warning("unknown Separation method %r; using auto", method)
            return auto_method_id(demucs_installed=self._models.status().installed)
        return method

    def _submit_separation(self, track: Track, method: str) -> str:
        separation = self._separation
        media = self._media
        descriptor = method_by_id(method)
        if separation is None or media is None or descriptor is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "the separation engines are not configured",
            )
        if descriptor.requires_model:
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
            identity=(JobKind.SEPARATE.value, track.source_id, descriptor.id),
            title=track.title,
            run=self._separation_run(track, separation, media, descriptor.id),
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
        method: str,
    ) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            result = run_separation_job(
                ctx,
                artifacts=self._artifacts,
                engine=separation,
                media=media,
                models=self._models,
                source_id=track.source_id,
                method=method,
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
        return self._submit_separation(track, DEFAULT_METHOD_ID)
