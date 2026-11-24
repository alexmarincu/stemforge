"""The Pitch-render feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Mapping

from stemforge import errors
from stemforge.application.renders import render_views
from stemforge.application.state import JobLedger
from stemforge.application.views import PitchSet
from stemforge.artifacts import ArtifactStore
from stemforge.domain.methods import DEMUCS_METHOD, require_method
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
                label=view.label,
                created_at=view.artifact.created_at,
                based_on_earlier_separation=view.based_on_earlier_separation,
                method=view.method,
                output_roles=view.output_roles,
            )
            for view in render_views(self._artifacts, track.source_id)
        )

    def pitch_set(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
        *,
        separation_method: str = DEMUCS_METHOD.id,
    ) -> PitchSet | None:
        """The set that satisfies a Render request for ``(semitones, scope)``.

        Scope is part of the query: a master render never answers a stems
        request. A stems query also follows the selected Separation method, so
        another method's render is never opened for it. A stems set built from
        an earlier separation of that method is not a cache hit, so Render is
        offered instead of Open while the old set stays in the tabs.
        """
        if isinstance(semitones, bool) or not isinstance(semitones, int):
            return None
        if not MIN_SEMITONES <= semitones <= MAX_SEMITONES or semitones == 0:
            return None
        resolved_scope = RenderScope(scope)
        requested_method = separation_method if resolved_scope is RenderScope.STEMS else None
        found = find_pitch_render(
            self._artifacts,
            source_id=track.source_id,
            semitones=semitones,
            scope=resolved_scope,
            separation_method=requested_method,
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
                and item.method == requested_method
                and not item.based_on_earlier_separation
            ):
                return item
        return None

    def render_pitch(
        self,
        track: Track,
        semitones: int,
        scope: RenderScope | str,
        *,
        separation_method: str = DEMUCS_METHOD.id,
    ) -> str:
        """Submit one Pitch Render Job and return its id.

        The request is explicit: the UI's stepper never calls it by itself.
        ``scope`` is ``original`` for the default flow, or ``stems`` when the
        user asks for the explicit stems action after a Separation. A stems
        request names the selected method and fails when that method has no
        committed set; it never reads another method's set.
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
        method: str | None = None
        if resolved_scope is RenderScope.STEMS:
            descriptor = require_method(separation_method)
            if self._artifacts.find_separation(track.source_id, method=descriptor.id) is None:
                raise ValueError(
                    f"the {descriptor.label} Separation set is not available for this Track"
                )
            method = descriptor.id
        identity: tuple[str, ...] = (
            JobKind.RENDER_PITCH.value,
            track.source_id,
            resolved_scope.value,
            str(shift),
        )
        if method is not None:
            identity += (method,)
        job = Job(
            kind=JobKind.RENDER_PITCH,
            identity=identity,
            title=track.title,
            run=self._pitch_run(track, shift, resolved_scope, pitch, media, method),
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
        separation_method: str | None,
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
                separation_method=separation_method,
                title=track.title,
            )
            return {
                "track_id": track.track_id,
                "render_key": result.artifact.render_key,
                "scope": result.scope.value,
                "semitones": str(result.semitones),
            }

        return run
