"""The per-Track Pitch card: request stepper and one scoped Render action.

The stepper is a request, not a view (ticket 08 §5): stepping only changes what a
later Render would ask for. Version tabs live in the Outputs card; nothing in
this module touches an artifact, a manifest, or an engine. Every read and every
Render goes through ``WorkspaceService``.

The primary action follows the current scope: with Stems cached it renders the
four stems and Instrumental; without them it renders the source master. A master
set never satisfies a stems request, and the Outputs tabs keep both visible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from nicegui import ui

from stemforge import errors
from stemforge.domain.models import RenderScope, Track
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    error_details,
)
from stemforge.ui.failure import JobFailure

if TYPE_CHECKING:
    from stemforge.application import PitchSet, WorkspaceService

MIN_SEMITONES = -12
MAX_SEMITONES = 12

SelectCallback = Callable[[str, str], None]
JobCallback = Callable[[str, str, int, RenderScope], None]


@dataclass(frozen=True, slots=True)
class PitchFailure:
    """One failed Render request; Retry re-submits exactly this spec."""

    job_failure: JobFailure
    semitones: int
    scope: RenderScope


class PitchCard:
    """The Pitch card for one page; session state is per Track and per client."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_select: SelectCallback,
        on_job_started: JobCallback,
        on_clear_cache: Callable[[], None] | None = None,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._on_select = on_select
        self._on_job_started = on_job_started
        self._on_clear_cache = on_clear_cache
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        self._requested: dict[str, int] = {}
        self._failures: dict[str, PitchFailure] = {}
        self._current_track_id: str | None = None

    @ui.refreshable_method
    def build(self, track: Track, *, active_key: str, busy: bool) -> None:
        self._current_track_id = track.track_id
        semitones = self._requested.get(track.track_id, 0)
        scope = self._current_scope(track)
        existing = self._service.pitch_set(track, semitones, scope) if semitones != 0 else None
        with ui.element("section").classes("sf-card").mark("pitch-card"):
            ui.label(formatting.PITCH_TITLE).classes("sf-card-title")
            self._stepper(track, semitones)
            self._status(track, semitones, active_key, existing)
            with ui.element("div").classes("sf-actions"):
                self._primary(track, semitones, active_key, existing, busy)
            self._error(track, busy)
            ui.label(formatting.PITCH_TEMPO_NOTE).classes("sf-fineprint")

    # -- pieces -------------------------------------------------------------

    def _current_scope(self, track: Track) -> RenderScope:
        """Stems when Separation is cached, otherwise the source master."""
        return RenderScope.STEMS if self._service.has_separation(track) else RenderScope.ORIGINAL

    def _stepper(self, track: Track, semitones: int) -> None:
        with ui.element("div").classes("sf-stepper"):
            (
                ui.button(icon="remove", on_click=lambda _=None: self._step(track, -1), color=None)
                .props("flat dense round")
                .classes("sf-control sf-control--icon")
                .mark("pitch-down")
                .set_enabled(semitones > MIN_SEMITONES)
            )
            with ui.element("div").classes("sf-semi"):
                ui.label(formatting.format_semitones(semitones)).classes("sf-semi-value").mark(
                    "pitch-semitones"
                )
                ui.label(formatting.PITCH_SEMITONES_UNIT).classes("sf-semi-unit")
            (
                ui.button(icon="add", on_click=lambda _=None: self._step(track, 1), color=None)
                .props("flat dense round")
                .classes("sf-control sf-control--icon")
                .mark("pitch-up")
                .set_enabled(semitones < MAX_SEMITONES)
            )

    def _status(
        self,
        track: Track,
        semitones: int,
        active_key: str,
        existing: PitchSet | None,
    ) -> None:
        if semitones == 0:
            text = formatting.PITCH_NEUTRAL_HINT
        elif existing is not None and existing.render_key == active_key:
            text = formatting.PITCH_VIEWING_STATUS.format(label=existing.label)
        elif existing is not None:
            text = formatting.PITCH_CACHED_HINT.format(label=existing.label)
        else:
            text = formatting.PITCH_NOT_RENDERED.format(
                label=formatting.format_semitones(semitones)
            )
        ui.label(text).classes("sf-pitch-status").mark("pitch-status")

    def _primary(
        self,
        track: Track,
        semitones: int,
        active_key: str,
        existing: PitchSet | None,
        busy: bool,
    ) -> None:
        if semitones == 0:
            (
                ui.button(formatting.PITCH_RENDER)
                .props("unelevated no-caps")
                .classes("sf-control sf-control--primary")
                .mark("pitch-primary", "pitch-render")
                .set_enabled(False)
            )
            return
        if existing is not None and existing.render_key == active_key:
            ui.label(formatting.PITCH_VIEWING).classes("sf-fineprint").mark("pitch-passive")
            return
        if existing is not None:
            (
                ui.button(
                    f"{formatting.PITCH_OPEN} {existing.label} version",
                    on_click=lambda _=None: self._select(track, existing.render_key),
                    color=None,
                )
                .props("flat no-caps")
                .classes("sf-control")
                .mark("pitch-primary", "pitch-open")
            )
            return
        rendered = formatting.format_semitones(semitones)
        button = (
            ui.button(
                f"{formatting.PITCH_RENDER} {rendered}",
                on_click=lambda _=None: self._submit(track, semitones, self._current_scope(track)),
            )
            .props("unelevated no-caps")
            .classes("sf-control sf-control--primary")
            .mark("pitch-primary", "pitch-render")
        )
        button.set_enabled(not busy)

    def _error(self, track: Track, busy: bool) -> None:
        request_failure = self._failures.get(track.track_id)
        if request_failure is None:
            return
        failure = request_failure.job_failure
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
        )
        with ui.element("div").classes("sf-notice").mark("pitch-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            error_details(
                code=failure.code,
                marker="pitch-error",
                job_id=failure.job_id,
                log_path=failure.log_path,
                diagnostics=self._diagnostics,
                clipboard=self._clipboard,
                open_path=self._open_path,
            )
            if failure.retryable:
                retry = (
                    ui.button(
                        formatting.RETRY,
                        on_click=lambda _=None: self._retry(track),
                    )
                    .props("unelevated no-caps size=sm")
                    .mark("pitch-retry")
                )
                retry.set_enabled(not busy)
            if self._on_clear_cache is not None and failure.code in {
                errors.STORAGE_DISK_FULL,
                errors.STORAGE_CACHE_FULL,
            }:
                clear = (
                    ui.button(
                        formatting.SETTINGS_CACHE_CLEAR,
                        on_click=lambda _=None: self._on_clear_cache(),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("pitch-clear-cache")
                )
                clear.set_enabled(not busy)

    # -- actions ------------------------------------------------------------

    def _step(self, track: Track, delta: int) -> None:
        current = self._requested.get(track.track_id, 0)
        self._requested[track.track_id] = max(MIN_SEMITONES, min(MAX_SEMITONES, current + delta))
        self.build.refresh()

    def _select(self, track: Track, render_key: str) -> None:
        self._on_select(track.track_id, render_key)

    def _submit(self, track: Track, semitones: int, scope: RenderScope) -> None:
        try:
            job_id = self._service.render_pitch(track, semitones, scope)
        except StemForgeError as error:
            self._failures[track.track_id] = PitchFailure(
                job_failure=JobFailure.from_error(error),
                semitones=semitones,
                scope=scope,
            )
            self.build.refresh()
            return
        except ValueError:
            # The scope raced a lost separation; the next refresh removes the
            # stale request. Nothing was submitted, so no Job or error appears.
            self.build.refresh()
            return
        self._failures.pop(track.track_id, None)
        self._on_job_started(job_id, track.track_id, semitones, scope)
        self.build.refresh()

    def _retry(self, track: Track) -> None:
        failure = self._failures.get(track.track_id)
        if failure is None:
            return
        self._submit(track, failure.semitones, failure.scope)

    # -- page callbacks -----------------------------------------------------

    def record_failure(
        self,
        track_id: str,
        failure: JobFailure,
        semitones: int,
        scope: RenderScope,
    ) -> None:
        """Show one failed Render inline without touching the active version."""
        self._failures[track_id] = PitchFailure(
            job_failure=replace(
                failure,
                code=failure.code or errors.PITCH_RENDER_FAILED,
            ),
            semitones=semitones,
            scope=scope,
        )
        if track_id == self._current_track_id:
            self.build.refresh()

    def clear_failure(self, track_id: str) -> None:
        if self._failures.pop(track_id, None) is not None and track_id == self._current_track_id:
            self.build.refresh()
