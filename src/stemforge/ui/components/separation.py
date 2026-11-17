"""The per-Track Separation card: model gate, Job state, and stem rows.

The card owns view state only (the expanded secondary stems, the selected set,
the inline failure, and the stall acknowledgement). Every read and every action
goes through ``WorkspaceService``, and the page owns the single ``<audio>``
player (ticket 08 §8). Playback and A/B stay enabled while a Job runs; only
actions that would submit a second Job disable (ticket 08 §11).

The model gate is inline and never a modal: size, revision, licence details, and
the explicit Download consent (ticket 08 §10, ticket 13 §7). A missing model
renders the gate and ``separation.model_missing`` instead of crashing, and a
successful install resubmits a previously requested Separation exactly once
(ticket 06 §3, ticket 14 §4.3).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from nicegui import ui

from stemforge import errors
from stemforge.domain.models import Track
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    error_details,
)

if TYPE_CHECKING:
    from stemforge.application import (
        ExportTarget,
        PreviewTarget,
        SeparationSet,
        WorkspaceService,
    )
    from stemforge.models import ModelStatus

PRIMARY_ROLES: tuple[str, ...] = ("vocals", "instrumental")
SECONDARY_ROLES: tuple[str, ...] = ("drums", "bass", "other")

# Ticket 14 §4.4: these codes carry a retry action even when a pipeline raised
# the code without setting the exception's retryable flag.
_RETRYABLE_CODES = frozenset(
    {
        errors.SEPARATION_WORKER_CRASHED,
        errors.SEPARATION_ENGINE_ERROR,
        errors.SEPARATION_OUTPUT_INVALID,
        errors.MEDIA_VALIDATION_FAILED,
        errors.STORAGE_DISK_FULL,
        errors.STORAGE_CACHE_FULL,
        errors.STORAGE_PERMISSION_DENIED,
    }
)

JobStarted = Callable[[str, str, str | None], None]
PlayTarget = Callable[[Track, "PreviewTarget"], None]
SelectExport = Callable[[Track, "ExportTarget"], None]
CancelJob = Callable[[], None]


class JobKindValue(Protocol):
    @property
    def value(self) -> str: ...


class SeparationJobView(Protocol):
    """The fields the card needs from ``jobs.JobSnapshot`` without importing it."""

    @property
    def job_id(self) -> str: ...

    @property
    def kind(self) -> JobKindValue: ...

    @property
    def progress(self) -> float: ...

    @property
    def stage(self) -> str: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def active(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class SeparationFailure:
    """One inline Separation failure; Retry resubmits the same track spec."""

    code: str
    detail: str | None
    retryable: bool
    job_id: str | None = None
    log_path: Path | None = None


class SeparationCard:
    """The Separation card for one page; session state is per Track and client."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_job_started: JobStarted,
        on_cancel: CancelJob,
        on_play: PlayTarget,
        on_export_target: SelectExport,
        clock: Callable[[], float] = time.monotonic,
        stall_seconds: float = 30.0,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._on_job_started = on_job_started
        self._on_cancel = on_cancel
        self._on_play = on_play
        self._on_export_target = on_export_target
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        # Injectable so tests advance time instead of sleeping for minutes.
        self.clock = clock
        self.stall_seconds = stall_seconds
        self._expanded: set[str] = set()
        self._selected_set: dict[str, str] = {}
        self._failures: dict[str, SeparationFailure] = {}
        self._current_track_id: str | None = None
        self._playing_key: str | None = None
        self._playback_available = True
        self._job: SeparationJobView | None = None
        self._job_signature: tuple[str, int, str] | None = None
        self._last_change_at: float | None = None
        self._stalled = False
        self._stall_ack: tuple[str, int, str] | None = None
        self._download_failure: SeparationFailure | None = None

    # -- composition --------------------------------------------------------

    @ui.refreshable_method
    def build(
        self,
        track: Track,
        *,
        busy: bool,
        playing_key: str | None = None,
        export_key: str | None = None,
        playback_available: bool = True,
    ) -> None:
        self._current_track_id = track.track_id
        self._playing_key = playing_key
        self._playback_available = playback_available
        sets = self._service.separation_sets(track)
        selected = self._resolve_set(track, sets)
        status = self._service.model_status()
        failure = self._failures.get(track.track_id)
        missing_failure = (
            failure.detail
            if failure is not None and failure.code == errors.SEPARATION_MODEL_MISSING
            else None
        )
        with ui.element("section").classes("sf-card").mark("separation-card"):
            ui.label(formatting.SEPARATION_TITLE).classes("sf-card-title")
            gate_visible = not status.installed or missing_failure is not None
            if gate_visible:
                self._gate(status, busy=busy, failure_detail=missing_failure)
            if sets:
                item = next((view for view in sets if view.set_key == selected), sets[-1])
                if self._running():
                    self._running_block()
                else:
                    ui.label(formatting.SEPARATION_DONE).classes("sf-separation-status").mark(
                        "separation-status"
                    )
                    if gate_visible or failure is not None:
                        ui.label(formatting.SEPARATION_LAST_SUCCESS).classes("sf-fineprint").mark(
                            "separation-last-success"
                        )
                self._set_tabs(track, sets, selected)
                self._rows(track, item, export_key)
                if not self._running() and not gate_visible:
                    self._primary(track, busy, again=True)
            elif not gate_visible:
                if self._running():
                    self._running_block()
                else:
                    ui.label(formatting.SEPARATION_NOT_SEPARATED).classes(
                        "sf-separation-status"
                    ).mark("separation-status")
                    ui.label(formatting.SEPARATION_NOT_SEPARATED_HINT).classes("sf-fineprint")
                    self._primary(track, busy, again=False)
            elif self._running():
                self._running_block()
            self._error(track, busy)
            if not gate_visible:
                ui.label(formatting.SEPARATION_PRIMARY_NOTE).classes("sf-fineprint")

    # -- model gate ---------------------------------------------------------

    def _gate(
        self,
        status: ModelStatus,
        *,
        busy: bool = False,
        failure_detail: str | None = None,
    ) -> None:
        copy = formatting.model_licence_copy(self._service.model_spec())
        with ui.element("div").classes("sf-gate").mark("separation-gate"):
            ui.label(copy.title).classes("sf-gate-title")
            ui.label(copy.summary).classes("sf-gate-summary")
            ui.label(copy.identity).classes("sf-gate-meta").mark("model-identity")
            ui.label(formatting.MODEL_LICENCE_DETAILS_LABEL).classes("sf-card-title")
            ui.label(copy.notice).classes("sf-gate-notice").mark("model-licence-notice")
            ui.label(formatting.SEPARATION_MODEL_CONSENT).classes("sf-fineprint")
            reason = status.reason or _detail_reason(failure_detail) or "absent"
            ui.label(f"{errors.SEPARATION_MODEL_MISSING} · reason={reason}").classes(
                "sf-notice-detail"
            ).mark("separation-model-code")
            error_details(
                code=errors.SEPARATION_MODEL_MISSING,
                marker="separation-gate-error",
                diagnostics=self._diagnostics,
                clipboard=self._clipboard,
                open_path=self._open_path,
            )
            download = self._job is not None and self._job.kind.value == "model_install"
            if self._download_failure is not None and not download:
                self._download_error(self._download_failure, busy=busy)
            elif download:
                assert self._job is not None
                percent = round(max(0.0, min(1.0, self._job.progress)) * 100)
                detail = f" · {self._job.detail}" if self._job.detail else ""
                ui.label(f"{formatting.SEPARATION_MODEL_DOWNLOADING}{detail} · {percent}%").classes(
                    "sf-separation-status"
                ).mark("model-download-progress")
                (
                    ui.button(
                        formatting.SEPARATION_CANCEL,
                        on_click=lambda _=None: self._on_cancel(),
                    )
                    .props("flat no-caps size=sm")
                    .mark("model-download-cancel")
                )
            else:
                button = (
                    ui.button(
                        copy.accept_label,
                        icon="download",
                        on_click=lambda _=None: self._download(),
                    )
                    .props("unelevated no-caps size=sm")
                    .mark("model-download")
                )
                button.set_enabled(not busy)

    def _download_error(self, failure: SeparationFailure, *, busy: bool = False) -> None:
        reason = _detail_reason(failure.detail)
        copy = formatting.copy_for(failure.code, reason)
        ui.label(copy.headline).classes("sf-notice-headline")
        ui.label(copy.action).classes("sf-notice-action")
        error_details(
            code=failure.code,
            marker="separation-model-error"
            if failure.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
            else "separation-error",
            job_id=failure.job_id,
            log_path=failure.log_path,
            diagnostics=self._diagnostics,
            clipboard=self._clipboard,
            open_path=self._open_path,
        )
        retry = (
            ui.button(
                formatting.SEPARATION_MODEL_RETRY,
                on_click=lambda _=None: self._download(),
            )
            .props("unelevated no-caps size=sm")
            .mark("model-download-retry")
        )
        retry.set_enabled(not busy)

    # -- running state ------------------------------------------------------

    def _running(self) -> bool:
        return self._job is not None and self._job.active and self._job.kind.value == "separate"

    def _running_block(self) -> None:
        if self._job is None:  # pragma: no cover - guarded by _running
            return
        percent = round(max(0.0, min(1.0, self._job.progress)) * 100)
        ui.label(formatting.SEPARATION_RUNNING.format(percent=percent)).classes(
            "sf-separation-status"
        ).mark("separation-status")
        ui.linear_progress(value=float(self._job.progress), show_value=False).classes(
            "sf-job-progress"
        ).mark("separation-progress")
        if self._job.stage:
            ui.label(self._job.stage).classes("sf-fineprint").mark("separation-stage")
        ui.label(formatting.SEPARATION_WORKER_NOTE).classes("sf-fineprint")
        (
            ui.button(
                formatting.SEPARATION_CANCEL,
                on_click=lambda _=None: self._on_cancel(),
            )
            .props("flat no-caps size=sm")
            .mark("separation-cancel")
        )
        if self._stalled:
            self._stall_block()

    def _stall_block(self) -> None:
        seconds = max(1, round(self.stall_seconds))
        with ui.element("div").classes("sf-stall").mark("separation-stall"):
            ui.label(formatting.SEPARATION_STALL.format(seconds=seconds)).classes("sf-stall-text")
            with ui.element("div").classes("sf-actions"):
                (
                    ui.button(
                        formatting.SEPARATION_KEEP_WAITING,
                        on_click=lambda _=None: self._keep_waiting(),
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-keep-waiting")
                )
                (
                    ui.button(
                        formatting.SEPARATION_CANCEL,
                        on_click=lambda _=None: self._on_cancel(),
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-stall-cancel")
                )

    # -- sets, rows, and A/B ------------------------------------------------

    def _resolve_set(self, track: Track, sets: tuple[SeparationSet, ...]) -> str | None:
        if not sets:
            return None
        keys = {item.set_key for item in sets}
        selected = self._selected_set.get(track.track_id)
        if selected not in keys:
            selected = sets[-1].set_key
            self._selected_set[track.track_id] = selected
        return selected

    def _set_tabs(
        self,
        track: Track,
        sets: tuple[SeparationSet, ...],
        selected: str | None,
    ) -> None:
        if len(sets) <= 1:
            return
        with ui.element("div").classes("sf-versions"):
            ui.label(formatting.SEPARATION_VERSIONS).classes("sf-versions-label")
            with ui.element("div").classes("sf-tabs").mark("separation-sets"):
                for index, item in enumerate(sets):
                    newest = index == len(sets) - 1
                    text = (
                        formatting.SEPARATION_CURRENT_SET
                        if newest
                        else formatting.SEPARATION_OLDER_SET
                    )
                    if item.based_on_earlier_separation:
                        text += f" · {formatting.SEPARATION_EARLIER}"
                    classes = "sf-version-tab"
                    if item.set_key == selected:
                        classes += " is-active"
                    markers = ["separation-set", f"separation-set-{item.set_key}"]
                    if item.based_on_earlier_separation:
                        markers.append("separation-earlier")
                    (
                        ui.button(
                            text,
                            on_click=lambda _=None, key=item.set_key: self._select_set(track, key),
                        )
                        .props("flat dense no-caps size=sm")
                        .classes(classes)
                        .mark(*markers)
                    )

    def _rows(
        self,
        track: Track,
        item: SeparationSet,
        export_key: str | None,
    ) -> None:
        exports = {
            target.role: target
            for target in self._service.export_targets(track)
            if target.scope == "separation" and target.role in PRIMARY_ROLES + SECONDARY_ROLES
        }
        for role in PRIMARY_ROLES:
            self._row(track, item, role, exports.get(role), export_key)
        expanded = track.track_id in self._expanded
        if expanded:
            for role in SECONDARY_ROLES:
                self._row(track, item, role, exports.get(role), export_key)
            (
                ui.button(
                    formatting.SEPARATION_SHOW_LESS,
                    on_click=lambda _=None: self._toggle_more(track),
                )
                .props("flat dense no-caps size=sm")
                .classes("sf-fineprint")
                .mark("separation-show-less")
            )
        else:
            (
                ui.button(
                    formatting.SEPARATION_SHOW_MORE,
                    icon="expand_more",
                    on_click=lambda _=None: self._toggle_more(track),
                )
                .props("flat dense no-caps size=sm")
                .classes("sf-fineprint")
                .mark("separation-show-more")
            )

    def _row(
        self,
        track: Track,
        item: SeparationSet,
        role: str,
        export: ExportTarget | None,
        export_key: str | None,
    ) -> None:
        preview = self._service.separation_preview_target(track, item.set_key, role)
        playing = preview is not None and self._playing_key == preview.key
        selected_export = export is not None and export_key == export.key
        classes = "sf-stem-row"
        if playing:
            classes += " is-playing"
        if selected_export:
            classes += " is-export-target"
        row = ui.element("div").classes(classes).mark("separation-row", f"separation-row-{role}")
        with row:
            play = (
                ui.button(
                    icon="pause" if playing else "play_arrow",
                    on_click=lambda _=None, target=preview: self._play(track, target),
                )
                .props("flat dense round size=sm")
                .mark("stem-play", f"stem-play-{role}")
            )
            enabled = preview is not None and self._playback_available
            play.set_enabled(enabled)
            if preview is None:
                play.tooltip("No cached preview")
            elif not self._playback_available:
                play.tooltip("Playback unavailable")
            with ui.element("div").classes("sf-stem-main"):
                with ui.element("div").classes("sf-stem-title"):
                    ui.label(formatting.SEPARATION_ROLE_LABELS.get(role, role.title())).classes(
                        "sf-stem-name"
                    )
                    if role in PRIMARY_ROLES:
                        ui.label(formatting.SEPARATION_PRIMARY_BADGE).classes("sf-stem-badge")
                    if role == "instrumental":
                        ui.label(formatting.SEPARATION_SUM_BADGE).classes("sf-stem-badge")
                ui.label(formatting.format_duration(track.source.metadata.duration_s)).classes(
                    "sf-stem-meta"
                )
            ab = (
                ui.button(
                    formatting.SEPARATION_AB,
                    on_click=lambda _=None, target=preview: self._ab(track, target),
                )
                .props("flat dense no-caps size=sm")
                .classes("sf-stem-ab")
                .mark("stem-ab", f"stem-ab-{role}")
            )
            ab.set_enabled(preview is not None and self._playback_available)
            ab.tooltip(formatting.SEPARATION_AB_TOOLTIP)
            if export is not None:
                button = (
                    ui.button(
                        icon="file_download",
                        on_click=lambda _=None, target=export: self._on_export_target(
                            track, target
                        ),
                    )
                    .props("flat dense round size=sm")
                    .mark("stem-export", f"stem-export-{role}")
                )
                button.tooltip(
                    formatting.SEPARATION_EXPORT_SELECTED
                    if selected_export
                    else formatting.SEPARATION_EXPORT_TOOLTIP
                )

    # -- failures -----------------------------------------------------------

    def _error(self, track: Track, busy: bool) -> None:
        failure = self._failures.get(track.track_id)
        if failure is None or failure.code == errors.SEPARATION_MODEL_MISSING:
            return
        copy = formatting.copy_for(failure.code, _detail_reason(failure.detail))
        with ui.element("div").classes("sf-notice").mark("separation-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            error_details(
                code=failure.code,
                marker="separation-error",
                job_id=failure.job_id,
                log_path=failure.log_path,
                diagnostics=self._diagnostics,
                clipboard=self._clipboard,
                open_path=self._open_path,
            )
            retryable = failure.retryable or failure.code in _RETRYABLE_CODES
            if retryable:
                retry = (
                    ui.button(
                        formatting.SEPARATION_RETRY,
                        on_click=lambda _=None: self._retry(track),
                    )
                    .props("unelevated no-caps size=sm")
                    .mark("separation-retry")
                )
                retry.set_enabled(not busy)

    # -- actions ------------------------------------------------------------

    def _primary(self, track: Track, busy: bool, *, again: bool) -> None:
        button = (
            ui.button(
                formatting.SEPARATION_AGAIN if again else formatting.SEPARATION_ACTION,
                icon="call_split",
                on_click=lambda _=None: self._submit(track),
            )
            .props("unelevated no-caps size=sm")
            .mark("separation-action")
        )
        button.set_enabled(not busy)

    def _download(self) -> None:
        try:
            job_id = self._service.install_model(accept_licence=True)
        except StemForgeError as error:
            self._download_failure = SeparationFailure(
                code=error.code,
                detail=error.detail,
                retryable=error.retryable,
            )
            self.build.refresh()
            return
        self._download_failure = None
        self._on_job_started(job_id, "model_install", None)
        self.build.refresh()

    def _submit(self, track: Track) -> None:
        try:
            job_id = self._service.separate(track)
        except StemForgeError as error:
            self._failures[track.track_id] = SeparationFailure(
                code=error.code,
                detail=error.detail,
                retryable=error.retryable,
            )
            self.build.refresh()
            return
        self._failures.pop(track.track_id, None)
        self._on_job_started(job_id, "separate", track.track_id)
        self.build.refresh()

    def _retry(self, track: Track) -> None:
        self._submit(track)

    def _select_set(self, track: Track, set_key: str) -> None:
        self._selected_set[track.track_id] = set_key
        self.build.refresh()

    def _toggle_more(self, track: Track) -> None:
        if track.track_id in self._expanded:
            self._expanded.discard(track.track_id)
        else:
            self._expanded.add(track.track_id)
        self.build.refresh()

    def _play(self, track: Track, target: PreviewTarget | None) -> None:
        if target is None:
            return
        self._on_play(track, target)

    def _ab(self, track: Track, target: PreviewTarget | None) -> None:
        if target is None:
            return
        original = next(
            (
                item
                for item in self._service.preview_targets(track.track_id)
                if item.key == "original"
            ),
            None,
        )
        if original is None:
            return
        if self._playing_key == target.key:
            self._on_play(track, original)
        else:
            self._on_play(track, target)

    def _keep_waiting(self) -> None:
        self._stall_ack = self._job_signature
        self._stalled = False
        self.build.refresh()

    # -- page callbacks -----------------------------------------------------

    def observe(self, job: SeparationJobView | None) -> None:
        """Track progress and the stall window from the page's poll."""
        if job is None or not job.active:
            if self._job is not None:
                self._job = None
                self._last_change_at = None
                self._stalled = False
                self._stall_ack = None
                self.build.refresh()
            return
        if job.kind.value not in {"separate", "model_install"}:
            if self._job is not None:
                self._job = None
                self._last_change_at = None
                self._stalled = False
                self._stall_ack = None
                self.build.refresh()
            return
        now = float(self.clock())
        signature = (job.job_id, round(job.progress * 1000), job.stage)
        changed = (
            self._job is None or self._job.job_id != job.job_id or signature != self._job_signature
        )
        if changed:
            self._job_signature = signature
            self._last_change_at = now
            self._stalled = False
            self._stall_ack = None
        self._job = job
        if job.kind.value == "separate" and self._last_change_at is not None:
            silent_for = now - self._last_change_at
            if (
                silent_for >= self.stall_seconds
                and self._stall_ack != signature
                and not self._stalled
            ):
                self._stalled = True
                self.build.refresh()
        if changed:
            self.build.refresh()

    def record_failure(
        self,
        track_id: str,
        code: str,
        detail: str | None,
        *,
        retryable: bool = False,
        job_id: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._failures[track_id] = SeparationFailure(
            code=code or errors.SEPARATION_ENGINE_ERROR,
            detail=detail,
            retryable=retryable,
            job_id=job_id,
            log_path=log_path,
        )
        if code == errors.SEPARATION_MODEL_MISSING:
            self._service.remember_pending_separation(track_id)
        if track_id == self._current_track_id:
            self.build.refresh()

    def record_model_failure(
        self,
        code: str,
        detail: str | None,
        *,
        job_id: str | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._download_failure = SeparationFailure(
            code=code or errors.SEPARATION_MODEL_DOWNLOAD_FAILED,
            detail=detail,
            retryable=True,
            job_id=job_id,
            log_path=log_path,
        )
        self._job = None
        self._last_change_at = None
        self.build.refresh()

    def model_installed(self) -> None:
        self._download_failure = None
        self._job = None
        self._last_change_at = None
        for track_id, failure in tuple(self._failures.items()):
            if failure.code == errors.SEPARATION_MODEL_MISSING:
                self._failures.pop(track_id, None)
        self.build.refresh()

    def record_success(self, track_id: str, set_key: str | None) -> None:
        self._failures.pop(track_id, None)
        if set_key:
            self._selected_set[track_id] = set_key
        if track_id == self._current_track_id:
            self.build.refresh()

    def clear_failure(self, track_id: str) -> None:
        if self._failures.pop(track_id, None) is not None and track_id == self._current_track_id:
            self.build.refresh()

    def forget_track(self, track_id: str) -> None:
        """Drop per-Track view state when the Track is removed."""
        self._expanded.discard(track_id)
        self._selected_set.pop(track_id, None)
        self._failures.pop(track_id, None)


def _detail_reason(detail: str | None) -> str | None:
    if not detail:
        return None
    for part in detail.replace("\n", " ").split():
        if part.startswith("reason="):
            return part.removeprefix("reason=")
    return None
