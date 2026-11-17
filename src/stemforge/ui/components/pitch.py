"""The per-Track Pitch card: request stepper, version tabs, and inline errors.

The stepper is a request, not a view (ticket 08 §5): stepping only changes what a
later Render would ask for. Nothing in this module touches an artifact, a
manifest, or an engine — every read and every Render goes through
``WorkspaceService``, and the page owns the single ``<audio>`` player.

Version tabs are immutable render sets in creation order, ``Original`` first.
A set is the newest committed set for its ``(semitones, scope)`` request; a
master set never satisfies a stems request (ticket 08 §5). A stems set built
from an earlier separation stays selectable and carries the ticket-05 label.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nicegui import ui

from stemforge import errors
from stemforge.domain.models import RenderScope, Track
from stemforge.errors import StemForgeError
from stemforge.ui import formatting

if TYPE_CHECKING:
    from stemforge.application import PitchSet, WorkspaceService

MIN_SEMITONES = -12
MAX_SEMITONES = 12

SelectCallback = Callable[[str, str], None]
JobCallback = Callable[[str, str, int, RenderScope], None]


@dataclass(frozen=True, slots=True)
class PitchFailure:
    """One failed Render request; Retry re-submits exactly this spec."""

    code: str
    detail: str | None
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
    ) -> None:
        self._service = service
        self._on_select = on_select
        self._on_job_started = on_job_started
        self._requested: dict[str, int] = {}
        self._failures: dict[str, PitchFailure] = {}
        self._current_track_id: str | None = None

    @ui.refreshable_method
    def build(self, track: Track, *, active_key: str, busy: bool) -> None:
        self._current_track_id = track.track_id
        semitones = self._requested.get(track.track_id, 0)
        sets = self._service.pitch_sets(track)
        has_stems = self._service.has_separation(track)
        with ui.element("section").classes("sf-card").mark("pitch-card"):
            ui.label(formatting.PITCH_TITLE).classes("sf-card-title")
            self._stepper(track, semitones)
            self._status(track, semitones, active_key, sets)
            with ui.element("div").classes("sf-actions"):
                self._primary(track, semitones, active_key, busy)
                if has_stems:
                    self._stems_action(track, semitones, active_key, busy)
            self._versions(track, active_key, sets)
            self._error(track, busy)
            ui.label(formatting.PITCH_TEMPO_NOTE).classes("sf-fineprint")

    # -- pieces -------------------------------------------------------------

    def _stepper(self, track: Track, semitones: int) -> None:
        with ui.element("div").classes("sf-stepper"):
            (
                ui.button(
                    icon="remove",
                    on_click=lambda _=None: self._step(track, -1),
                )
                .props("flat dense round size=sm")
                .mark("pitch-down")
                .set_enabled(semitones > MIN_SEMITONES)
            )
            with ui.element("div").classes("sf-semi"):
                ui.label(formatting.format_semitones(semitones)).classes("sf-semi-value").mark(
                    "pitch-semitones"
                )
                ui.label(formatting.PITCH_SEMITONES_UNIT).classes("sf-semi-unit")
            (
                ui.button(
                    icon="add",
                    on_click=lambda _=None: self._step(track, 1),
                )
                .props("flat dense round size=sm")
                .mark("pitch-up")
                .set_enabled(semitones < MAX_SEMITONES)
            )

    def _status(
        self,
        track: Track,
        semitones: int,
        active_key: str,
        sets: tuple[PitchSet, ...],
    ) -> None:
        active_set = next((item for item in sets if item.render_key == active_key), None)
        existing = (
            self._service.pitch_set(track, semitones, RenderScope.ORIGINAL)
            if semitones != 0
            else None
        )
        if semitones == 0:
            text = formatting.PITCH_NEUTRAL_HINT
        elif active_set is not None and active_set.semitones == semitones:
            text = formatting.PITCH_VIEWING_STATUS.format(label=active_set.label)
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
        busy: bool,
    ) -> None:
        if semitones == 0:
            (
                ui.button(formatting.PITCH_RENDER)
                .props("unelevated no-caps size=sm")
                .mark("pitch-primary", "pitch-render")
                .set_enabled(False)
            )
            return
        existing = self._service.pitch_set(track, semitones, RenderScope.ORIGINAL)
        if existing is not None and existing.render_key == active_key:
            ui.label(formatting.PITCH_VIEWING).classes("sf-fineprint").mark("pitch-passive")
            return
        if existing is not None:
            (
                ui.button(
                    f"{formatting.PITCH_OPEN} {existing.label} version",
                    on_click=lambda _=None: self._select(track, existing.render_key),
                )
                .props("flat no-caps size=sm")
                .mark("pitch-primary", "pitch-open")
            )
            return
        rendered = formatting.format_semitones(semitones)
        button = (
            ui.button(
                f"{formatting.PITCH_RENDER} {rendered}",
                on_click=lambda _=None: self._submit(track, semitones, RenderScope.ORIGINAL),
            )
            .props("unelevated no-caps size=sm")
            .mark("pitch-primary", "pitch-render")
        )
        button.set_enabled(not busy)

    def _stems_action(
        self,
        track: Track,
        semitones: int,
        active_key: str,
        busy: bool,
    ) -> None:
        scope = formatting.PITCH_STEMS_SCOPE
        if semitones == 0:
            (
                ui.button(f"{formatting.PITCH_RENDER} {scope}")
                .props("flat no-caps size=sm")
                .mark("pitch-stems", "pitch-stems-render")
                .set_enabled(False)
            )
            return
        existing = self._service.pitch_set(track, semitones, RenderScope.STEMS)
        if existing is not None and existing.render_key == active_key:
            ui.label(formatting.PITCH_VIEWING).classes("sf-fineprint").mark("pitch-stems-passive")
            return
        rendered = formatting.format_semitones(semitones)
        if existing is not None:
            (
                ui.button(
                    f"{formatting.PITCH_OPEN} {scope} {rendered}",
                    on_click=lambda _=None: self._select(track, existing.render_key),
                )
                .props("flat no-caps size=sm")
                .mark("pitch-stems", "pitch-stems-open")
            )
            return
        button = (
            ui.button(
                f"{formatting.PITCH_RENDER} {scope} {rendered}",
                on_click=lambda _=None: self._submit(track, semitones, RenderScope.STEMS),
            )
            .props("flat no-caps size=sm")
            .mark("pitch-stems", "pitch-stems-render")
        )
        button.set_enabled(not busy)

    def _versions(
        self,
        track: Track,
        active_key: str,
        sets: tuple[PitchSet, ...],
    ) -> None:
        with ui.element("div").classes("sf-versions"):
            ui.label(formatting.PITCH_VERSIONS).classes("sf-versions-label")
            with ui.element("div").classes("sf-tabs").mark("pitch-tabs"):
                self._tab(
                    track,
                    key="original",
                    label=formatting.PITCH_ORIGINAL,
                    active=active_key == "original",
                    stale=False,
                )
                for item in sets:
                    self._tab(
                        track,
                        key=item.render_key,
                        label=item.label,
                        active=item.render_key == active_key,
                        stale=item.based_on_earlier_separation,
                    )

    def _tab(
        self,
        track: Track,
        *,
        key: str,
        label: str,
        active: bool,
        stale: bool,
    ) -> None:
        text = label
        if stale:
            text += f" · {formatting.PITCH_EARLIER_SEPARATION}"
        classes = "sf-version-tab"
        if active:
            classes += " is-active"
        if stale:
            classes += " is-stale"
        markers = ["pitch-tab", f"pitch-tab-{key}"]
        if stale:
            markers.append("pitch-earlier-separation")
        (
            ui.button(text, on_click=lambda _=None: self._select(track, key))
            .props("flat dense no-caps size=sm")
            .classes(classes)
            .mark(*markers)
        )

    def _error(self, track: Track, busy: bool) -> None:
        failure = self._failures.get(track.track_id)
        if failure is None:
            return
        copy = formatting.copy_for(failure.code)
        detail = f"{failure.code} · {failure.detail}" if failure.detail else failure.code
        with ui.element("div").classes("sf-notice").mark("pitch-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            ui.label(detail).classes("sf-notice-detail").mark("pitch-error-code")
            retry = (
                ui.button(
                    formatting.PITCH_RETRY,
                    on_click=lambda _=None: self._retry(track),
                )
                .props("unelevated no-caps size=sm")
                .mark("pitch-retry")
            )
            retry.set_enabled(not busy)

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
                code=error.code,
                detail=error.detail,
                semitones=semitones,
                scope=scope,
            )
            self.build.refresh()
            return
        except ValueError:
            # The stems action raced a lost separation; the next refresh removes
            # the action. Nothing was submitted, so no Job or error appears.
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
        code: str,
        detail: str | None,
        semitones: int,
        scope: RenderScope,
    ) -> None:
        """Show one failed Render inline without touching the active version."""
        self._failures[track_id] = PitchFailure(
            code=code or errors.PITCH_RENDER_FAILED,
            detail=detail,
            semitones=semitones,
            scope=scope,
        )
        if track_id == self._current_track_id:
            self.build.refresh()

    def clear_failure(self, track_id: str) -> None:
        if self._failures.pop(track_id, None) is not None and track_id == self._current_track_id:
            self.build.refresh()
