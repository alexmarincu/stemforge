"""The Separation feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from stemforge import errors
from stemforge.application.identity import separation_key
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.application.views import (
    SeparationAvailability,
    SeparationMethodView,
    SeparationSet,
)
from stemforge.artifacts import ArtifactStore
from stemforge.domain.methods import (
    DEFAULT_METHOD_ID,
    KARAOKE_METHOD,
    METHODS,
    auto_method_id,
    method_by_id,
)
from stemforge.domain.models import JobKind, Track, stereo_unsupported_reason
from stemforge.engines.media import MediaTool
from stemforge.engines.separation import SeparationEngine
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.models import ModelStore
from stemforge.pipeline.separation_job import run_separation_job
from stemforge.settings import SettingsStore

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
        settings: SettingsStore,
    ) -> None:
        self._artifacts = artifacts
        self._ledger = ledger
        self._workspace = workspace
        self._models = models
        self._separation = separation
        self._media = media
        self._settings = settings
        self._selected_methods: dict[str, str] = {}
        self._auto_methods: dict[str, str] = {}
        self._pending: tuple[str, str] | None = None

    def separation_methods(self) -> tuple[SeparationMethodView, ...]:
        """The card's selector descriptors, in presentation order."""
        return tuple(
            SeparationMethodView(
                id=descriptor.id,
                label=descriptor.label,
                description_key=descriptor.description_key,
                requires_model=descriptor.requires_model,
            )
            for descriptor in METHODS.values()
        )

    def separation_availability(self, track: Track) -> tuple[SeparationAvailability, ...]:
        """Whether each method can run on this Track's recorded stereo layout.

        A stereo-only method is unavailable for a mono or dual-mono Source. An
        unknown layout is not a refusal: the Job measures the canonical audio
        before it stages anything.
        """
        reason = stereo_unsupported_reason(track.source.stereo_layout)
        return tuple(
            SeparationAvailability(
                method=descriptor.id,
                available=not (descriptor.stereo_only and reason is not None),
                reason=reason if descriptor.stereo_only else None,
            )
            for descriptor in METHODS.values()
        )

    def separation_method(self, track: Track) -> str:
        """The method the card and Outputs show for this Track.

        Resolution order: the per-Track view selection, then the newest
        committed set's method, then the persisted preference, then auto.
        """
        return self._resolve_method(track)

    def selected_separation_set(self, track: Track) -> SeparationSet | None:
        """The selected method's committed set, or ``None`` when it has none."""
        method = self._resolve_method(track)
        return next(
            (view for view in reversed(self.separation_sets(track)) if view.method == method),
            None,
        )

    def has_separation(self, track: Track) -> bool:
        """Whether the selected method has a committed set."""
        return self.selected_separation_set(track) is not None

    def has_any_separation(self, track: Track) -> bool:
        """Whether any method has a committed set for the Track."""
        return bool(self._artifacts.separation_sets(track.source_id))

    def select_separation_method(self, track: Track, method_id: str) -> None:
        """Change the per-Track view selection only; never persist or submit."""
        with self._workspace.lock:
            if method_by_id(method_id) is None:
                _LOG.warning("unknown Separation method %r; using auto", method_id)
                self._selected_methods.pop(track.track_id, None)
                self._auto_methods.pop(track.track_id, None)
                return
            self._selected_methods[track.track_id] = method_id
            self._auto_methods.pop(track.track_id, None)

    def forget_track(self, track_id: str) -> None:
        """Drop the Track's view selection and any pending resubmit."""
        with self._workspace.lock:
            self._selected_methods.pop(track_id, None)
            self._auto_methods.pop(track_id, None)
            if self._pending is not None and self._pending[0] == track_id:
                self._pending = None

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
        """Submit one Separation Job and persist the method the user chose.

        An unset method resolves to the Track's current selection. A
        stereo-only method refuses a Source whose recorded layout has no stereo
        image, before any Job is submitted. The resolved method is persisted
        before the model gate, so an explicit Demucs request survives the
        install. Only a model-requiring method checks the model store; a missing
        model raises ``separation.model_missing`` and latches one post-install
        resubmit. A Karaoke request clears a pending Demucs latch, so an install
        cannot run a method the user moved away from.
        """
        requested, automatic = self._resolve_request(track, method)
        descriptor = method_by_id(requested)
        assert descriptor is not None
        reason = stereo_unsupported_reason(track.source.stereo_layout)
        if descriptor.stereo_only and reason is not None:
            raise StemForgeError(
                errors.SEPARATION_METHOD_UNSUPPORTED,
                "the source has no stereo image for centre-channel extraction",
                detail=f"reason={reason}",
                context={
                    "source_id": track.source_id,
                    "method": descriptor.id,
                    "reason": reason,
                },
            )
        self._settings.set_separation_method(requested)
        with self._workspace.lock:
            self._selected_methods[track.track_id] = requested
            self._auto_methods.pop(track.track_id, None)
        if requested == KARAOKE_METHOD.id:
            self._clear_pending(track.track_id)
        try:
            return self._submit_separation(track, requested)
        except StemForgeError as error:
            if (
                error.code == errors.SEPARATION_MODEL_MISSING
                and descriptor.requires_model
                and not automatic
            ):
                self.remember_pending_separation(track.track_id, requested)
            raise

    def _resolve_request(self, track: Track, method: str | None) -> tuple[str, bool]:
        """Resolve a submission and whether it came from auto selection."""
        if method is None:
            return self._resolve_method(track), self._is_automatic(track)
        if method_by_id(method) is None:
            _LOG.warning("unknown Separation method %r; using auto", method)
            return (
                auto_method_id(demucs_installed=self._models.status().installed),
                True,
            )
        return method, False

    def _is_automatic(self, track: Track) -> bool:
        with self._workspace.lock:
            if track.track_id in self._selected_methods:
                return False
        if self.separation_sets(track):
            return False
        preference = self._settings.separation_method()
        return preference is None or method_by_id(preference) is None

    def _resolve_method(self, track: Track) -> str:
        with self._workspace.lock:
            explicit = self._selected_methods.get(track.track_id)
        if explicit is not None:
            if method_by_id(explicit) is not None:
                return explicit
            _LOG.warning("unknown selected Separation method %r; using auto", explicit)
            with self._workspace.lock:
                if self._selected_methods.get(track.track_id) == explicit:
                    self._selected_methods.pop(track.track_id, None)

        sets = self.separation_sets(track)
        if sets:
            return sets[-1].method

        preference = self._settings.separation_method()
        if preference is not None:
            if method_by_id(preference) is not None:
                return preference
            _LOG.warning("unknown persisted Separation method %r; using auto", preference)
        with self._workspace.lock:
            remembered = self._auto_methods.get(track.track_id)
        if remembered is not None:
            return remembered
        resolved = auto_method_id(demucs_installed=self._models.status().installed)
        # Auto is resolved once per Track and then kept, so a later model
        # install cannot change the method shown under the user.
        with self._workspace.lock:
            self._auto_methods.setdefault(track.track_id, resolved)
        return resolved

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

    def pending_separation(self) -> tuple[str, str] | None:
        """The latched ``(track_id, method)``, or ``None``."""
        with self._workspace.lock:
            return self._pending

    def pending_separation_track_id(self) -> str | None:
        with self._workspace.lock:
            return self._pending[0] if self._pending is not None else None

    def remember_pending_separation(self, track_id: str, method: str = DEFAULT_METHOD_ID) -> None:
        if not track_id:
            raise ValueError("track id must not be empty")
        if method_by_id(method) is None:
            raise ValueError(f"unknown Separation method: {method!r}")
        with self._workspace.lock:
            if self._pending is None:
                self._pending = (track_id, method)

    def _clear_pending(self, track_id: str) -> None:
        with self._workspace.lock:
            if self._pending is not None and self._pending[0] == track_id:
                self._pending = None

    def resubmit_pending_separation(self, track: Track) -> str | None:
        """Submit the latched Separation and method exactly once, or ``None``.

        The latch is consumed before the submission is attempted, so a failure
        here (including a second ``model_missing``) cannot loop. ``None`` means
        this Track was not the latched one; nothing is submitted.
        """
        with self._workspace.lock:
            if self._pending is None or self._pending[0] != track.track_id:
                return None
            method = self._pending[1]
            self._pending = None
            self._selected_methods[track.track_id] = method
            self._auto_methods.pop(track.track_id, None)
        return self._submit_separation(track, method)
