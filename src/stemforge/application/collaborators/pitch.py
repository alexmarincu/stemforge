"""The Pitch-render feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Mapping

from stemforge import errors
from stemforge.application.renders import render_views
from stemforge.application.state import JobLedger
from stemforge.application.views import PitchSet
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, RenderScope, Track
from stemforge.engines.media import MediaTool
from stemforge.engines.pitch import (
    MAX_SEMITONES,
    MIN_SEMITONES,
    PitchEngine,
    validate_semitones,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.pipeline.pitch_job import find_pitch_render, run_pitch_job


class PitchService:
    """Owns render-set reads and the explicit Pitch Render Job."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        ledger: JobLedger,
        pitch: PitchEngine | None,
        media: MediaTool | None,
    ) -> None:
        self._artifacts = artifacts
        self._ledger = ledger
        self._pitch = pitch
        self._media = media

    def pitch_sets(self, track: Track) -> tuple[PitchSet, ...]:
        return tuple(
            PitchSet(
                render_key=view.artifact.render_key,
                semitones=view.semitones,
                scope=view.scope,
                created_at=view.artifact.created_at,
                based_on_earlier_separation=view.based_on_earlier_separation,
                label=view.label,
            )
            for view in render_views(self._artifacts, track.source_id)
        )

    def pitch_set(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> PitchSet | None:
        """The set that satisfies a Render request for ``(semitones, scope)``.

        Scope is part of the query: a master render never answers a stems
        request. A stems set built from an earlier separation is
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

    def render_pitch(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
    ) -> str:
        """Submit one Pitch Render Job and return its id.

        The request is explicit: the UI's stepper never calls this by itself.
        ``scope`` is ``original`` for the default flow, or ``stems`` when the
        user asks for the explicit stems action after a Separation.
        """
        shift = validate_semitones(semitones)
        resolved_scope = RenderScope(scope)
        pitch = self._pitch
        media = self._media
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
        return self._ledger.submit(
            job,
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
