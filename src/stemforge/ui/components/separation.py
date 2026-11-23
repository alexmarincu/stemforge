"""The per-Track Separation card: model gate, Job state, and inline errors.

The card owns view state only (the inline failure and the stall acknowledgement).
Every read and every action goes through ``WorkspaceService``; the Outputs card
owns every playable artifact and the page owns the single ``<audio>`` player.
Playback stays enabled while a Job runs; only actions that would submit a second
Job disable.

The model gate is inline and never a modal: size, revision, licence details, and
the explicit Download consent. A missing model renders the gate and
``separation.model_missing`` instead of crashing, and a successful install
resubmits a previously requested Separation exactly once.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

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
from stemforge.ui.failure import JobFailure

if TYPE_CHECKING:
    from stemforge.application import (
        JobView,
        SeparationAvailability,
        SeparationMethodView,
        WorkspaceService,
    )

JobStarted = Callable[[str, str, str | None], None]
CancelJob = Callable[[], None]
MethodSelected = Callable[[Track], None]


class SeparationCard:
    """The Separation card for one page; session state is per Track and client."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_job_started: JobStarted,
        on_cancel: CancelJob,
        on_open_about: Callable[[], None] | None = None,
        on_clear_cache: Callable[[], None] | None = None,
        on_method_selected: MethodSelected | None = None,
        clock: Callable[[], float] = time.monotonic,
        stall_seconds: float = 30.0,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._on_job_started = on_job_started
        self._on_cancel = on_cancel
        self._on_open_about = on_open_about
        self._on_clear_cache = on_clear_cache
        self._on_method_selected = on_method_selected
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        # Injectable so tests advance time instead of sleeping for minutes.
        self.clock = clock
        self.stall_seconds = stall_seconds
        self._failures: dict[str, JobFailure] = {}
        self._requested_methods: dict[str, str] = {}
        self._current_track_id: str | None = None
        self._job: JobView | None = None
        self._job_signature: tuple[str, int, str] | None = None
        self._last_change_at: float | None = None
        self._stalled = False
        self._stall_ack: tuple[str, int, str] | None = None
        self._download_failure: JobFailure | None = None

    # -- composition --------------------------------------------------------

    @ui.refreshable_method
    def build(self, track: Track, *, busy: bool) -> None:
        self._current_track_id = track.track_id
        methods = self._service.separation_methods()
        availability = self._service.separation_availability(track)
        selected_method = self._selected_method(track, methods)
        selected_status = next(
            (item for item in availability if item.method == selected_method.id),
            None,
        )
        unavailable_reason = (
            selected_status.reason
            if selected_status is not None and not selected_status.available
            else None
        )
        selected = self._service.selected_separation_set(track)
        status = self._service.model_status()
        failure = self._failures.get(track.track_id)
        model_missing = (
            selected_method.requires_model
            and failure is not None
            and failure.code == errors.SEPARATION_MODEL_MISSING
        )
        checksum_failure: JobFailure | None = None
        for candidate in (failure, self._download_failure):
            if (
                candidate is not None
                and candidate.code == errors.SEPARATION_MODEL_MISSING
                and formatting.reason_from_detail(candidate.detail) == "checksum"
            ):
                checksum_failure = candidate
                break
        gate_visible = selected_method.requires_model and (not status.installed or model_missing)
        offer_demucs = not selected_method.requires_model and not status.installed
        with ui.element("section").classes("sf-card").mark("separation-card"):
            ui.label(formatting.SEPARATION_TITLE).classes("sf-card-title")
            self._method_control(track, methods, selected_method, availability)
            self._method_requirement(selected_method)
            if unavailable_reason is not None:
                self._method_unavailable(track, unavailable_reason)
            if gate_visible:
                self._gate(busy=busy, checksum_failure=checksum_failure)
            elif offer_demucs:
                self._demucs_offer(track)
            if self._running():
                self._running_block()
                if selected is not None:
                    ui.label(self._last_success_copy(selected.method)).classes("sf-fineprint").mark(
                        "separation-last-success"
                    )
            elif selected is not None:
                ui.label(self._done_copy(selected.method)).classes("sf-separation-status").mark(
                    "separation-status"
                )
                if gate_visible or failure is not None:
                    ui.label(self._last_success_copy(selected.method)).classes("sf-fineprint").mark(
                        "separation-last-success"
                    )
                if not gate_visible and unavailable_reason is None:
                    self._primary(
                        track,
                        busy,
                        method=selected.method,
                        label=formatting.SEPARATION_AGAIN,
                    )
            else:
                ui.label(self._not_separated_copy(selected_method.id)).classes(
                    "sf-separation-status"
                ).mark("separation-status")
                ui.label(self._not_separated_hint(selected_method.id)).classes("sf-fineprint")
                if not gate_visible and unavailable_reason is None:
                    self._primary(
                        track,
                        busy,
                        method=selected_method.id,
                        label=formatting.SEPARATION_ACTION,
                    )
            self._error(track, busy)
            if selected_method.id == "karaoke" and unavailable_reason is None:
                ui.label(formatting.SEPARATION_KARAOKE_FINEPRINT).classes("sf-fineprint").mark(
                    "separation-karaoke-fineprint"
                )
            if not gate_visible and selected is not None and selected.method == "demucs":
                ui.label(formatting.SEPARATION_PRIMARY_NOTE).classes("sf-fineprint")

    def _selected_method(
        self, track: Track, methods: tuple[SeparationMethodView, ...]
    ) -> SeparationMethodView:
        selected_id = self._service.separation_method(track)
        return next(
            (method for method in methods if method.id == selected_id),
            methods[0],
        )

    def _method_control(
        self,
        track: Track,
        methods: tuple[SeparationMethodView, ...],
        selected: SeparationMethodView,
        availability: tuple[SeparationAvailability, ...],
    ) -> None:
        unavailable = {item.method for item in availability if not item.available}
        with ui.element("div").classes("sf-method").mark("separation-method-picker"):
            with ui.element("div").classes("sf-method-switch").mark("separation-method-control"):
                for method in methods:
                    classes = "sf-method-option sf-control"
                    if method.id == selected.id:
                        classes += " is-active"
                    button = (
                        ui.button(
                            method.label,
                            on_click=lambda _=None, item=method.id: self._choose_method(
                                track, item
                            ),
                            color=None,
                        )
                        .props("flat dense no-caps")
                        .classes(classes)
                        .mark("separation-method", f"separation-method-{method.id}")
                    )
                    if method.id in unavailable:
                        button.set_enabled(False)
            ui.select(
                options={method.id: method.label for method in methods},
                value=selected.id,
                on_change=lambda event: self._choose_method(track, str(event.value)),
            ).props("dense outlined options-dense").classes("sf-method-fallback").mark(
                "separation-method-select"
            )

    def _method_unavailable(self, track: Track, reason: str) -> None:
        with ui.element("div").classes("sf-notice"):
            ui.label(formatting.separation_unavailable_copy(reason)).classes(
                "sf-notice-action"
            ).mark("separation-method-unavailable")
            (
                ui.button(
                    formatting.SEPARATION_METHOD_USE_DEMUCS,
                    on_click=lambda _=None: self._choose_method(track, "demucs"),
                    color=None,
                )
                .props("flat no-caps size=sm")
                .mark("separation-use-demucs")
            )

    @staticmethod
    def _method_requirement(method: SeparationMethodView) -> None:
        copy = (
            formatting.SEPARATION_METHOD_MODEL_REQUIRED
            if method.requires_model
            else formatting.SEPARATION_METHOD_NO_MODEL
        )
        ui.label(copy).classes("sf-fineprint").mark("separation-method-requirement")

    def _choose_method(self, track: Track, method_id: str) -> None:
        """Change the view only: no settings write and no Job submission."""
        self._service.select_separation_method(track, method_id)
        if self._on_method_selected is not None:
            self._on_method_selected(track)
        else:
            self.build.refresh()

    def _demucs_offer(self, track: Track) -> None:
        with ui.element("div").classes("sf-demucs-offer").mark("separation-demucs-offer"):
            ui.label(formatting.SEPARATION_DEMUCS_NOT_INSTALLED).classes("sf-fineprint")
            (
                ui.button(
                    formatting.SEPARATION_DEMUCS_CHOOSE,
                    on_click=lambda _=None: self._choose_method(track, "demucs"),
                    color=None,
                )
                .props("flat no-caps size=sm")
                .mark("separation-demucs-offer-action")
            )

    @staticmethod
    def _done_copy(method: str) -> str:
        if method == "karaoke":
            return formatting.SEPARATION_DONE_KARAOKE
        return formatting.SEPARATION_DONE

    @staticmethod
    def _not_separated_copy(method: str) -> str:
        if method == "karaoke":
            return formatting.SEPARATION_NOT_SEPARATED_KARAOKE
        return formatting.SEPARATION_NOT_SEPARATED

    @staticmethod
    def _not_separated_hint(method: str) -> str:
        if method == "karaoke":
            return formatting.SEPARATION_NOT_SEPARATED_HINT_KARAOKE
        return formatting.SEPARATION_NOT_SEPARATED_HINT

    @staticmethod
    def _last_success_copy(method: str) -> str:
        if method == "karaoke":
            return formatting.SEPARATION_LAST_SUCCESS_KARAOKE
        return formatting.SEPARATION_LAST_SUCCESS

    # -- model gate ---------------------------------------------------------

    def _gate(
        self,
        *,
        busy: bool = False,
        checksum_failure: JobFailure | None = None,
    ) -> None:
        copy = formatting.model_licence_copy(self._service.model_spec())
        reason_copy = (
            formatting.copy_for(checksum_failure.code, "checksum")
            if checksum_failure is not None
            else None
        )
        with ui.element("div").classes("sf-gate").mark("separation-gate"):
            if reason_copy is not None:
                ui.label(reason_copy.headline).classes("sf-gate-title").mark("model-checksum-title")
                ui.label(reason_copy.action).classes("sf-gate-summary").mark("model-checksum-hint")
            else:
                ui.label(copy.title).classes("sf-gate-title")
                ui.label(copy.summary).classes("sf-gate-summary")
            ui.label(copy.identity).classes("sf-gate-meta").mark("model-identity")
            ui.label(formatting.MODEL_LICENCE_DETAILS_LABEL).classes("sf-card-title")
            ui.label(copy.notice).classes("sf-gate-notice").mark("model-licence-notice")
            ui.label(formatting.SEPARATION_MODEL_CONSENT).classes("sf-fineprint")
            error_details(
                code=errors.SEPARATION_MODEL_MISSING,
                marker="separation-gate-error",
                job_id=checksum_failure.job_id if checksum_failure is not None else None,
                log_path=checksum_failure.log_path if checksum_failure is not None else None,
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
                        color=None,
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
            (
                ui.button(
                    copy.details_label, on_click=lambda _=None: self._open_about(), color=None
                )
                .props("flat no-caps size=sm")
                .mark("model-licence-details")
            )

    def _open_about(self) -> None:
        if self._on_open_about is not None:
            self._on_open_about()

    def _download_error(self, failure: JobFailure, *, busy: bool = False) -> None:
        reason = formatting.reason_from_detail(failure.detail)
        copy = formatting.copy_for(failure.code, reason, context=failure.context)
        checksum = failure.code == errors.SEPARATION_MODEL_MISSING and reason == "checksum"
        # The gate above already carries the checksum headline and hint.
        if not checksum:
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
                .mark("model-download-clear-cache")
            )
            clear.set_enabled(not busy)

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
                formatting.SEPARATION_CANCEL, on_click=lambda _=None: self._on_cancel(), color=None
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
                        color=None,
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-keep-waiting")
                )
                (
                    ui.button(
                        formatting.SEPARATION_CANCEL,
                        on_click=lambda _=None: self._on_cancel(),
                        color=None,
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-stall-cancel")
                )

    # -- failures -----------------------------------------------------------

    def _error(self, track: Track, busy: bool) -> None:
        failure = self._failures.get(track.track_id)
        if failure is None or failure.code == errors.SEPARATION_MODEL_MISSING:
            return
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
        )
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
            if failure.code == errors.SEPARATION_METHOD_UNSUPPORTED:
                (
                    ui.button(
                        formatting.SEPARATION_METHOD_USE_DEMUCS,
                        on_click=lambda _=None: self._choose_method(track, "demucs"),
                        color=None,
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-use-demucs")
                )
            if failure.retryable:
                retry = (
                    ui.button(
                        formatting.RETRY,
                        on_click=lambda _=None: self._retry(track),
                    )
                    .props("unelevated no-caps size=sm")
                    .mark("separation-retry")
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
                    .mark("separation-clear-cache")
                )
                clear.set_enabled(not busy)

    # -- actions ------------------------------------------------------------

    def _primary(
        self,
        track: Track,
        busy: bool,
        *,
        method: str,
        label: str,
        marker: str = "separation-action",
    ) -> None:
        method_label = next(
            (item.label for item in self._service.separation_methods() if item.id == method),
            method,
        )
        with ui.element("div").classes("sf-actions"):
            button = (
                ui.button(
                    label,
                    icon="call_split",
                    on_click=lambda _=None, item=method: self._submit(track, item),
                )
                .props("unelevated no-caps")
                .classes("sf-control sf-control--primary")
                .mark(marker)
            )
            button.set_enabled(not busy)
            ui.label(formatting.SEPARATION_METHOD_ACTION.format(method=method_label)).classes(
                "sf-fineprint"
            ).mark("separation-action-method")

    def request_separation(self, track: Track) -> None:
        """The Outputs placeholder's Separate action submits the selected method."""
        self._submit(track, self._service.separation_method(track))

    def _download(self) -> None:
        try:
            job_id = self._service.install_model(accept_licence=True)
        except StemForgeError as error:
            self._download_failure = JobFailure.from_error(error)
            self.build.refresh()
            return
        self._download_failure = None
        self._on_job_started(job_id, "model_install", None)
        self.build.refresh()

    def _submit(self, track: Track, method: str | None = None) -> None:
        requested = method or self._service.separation_method(track)
        availability = next(
            (
                item
                for item in self._service.separation_availability(track)
                if item.method == requested
            ),
            None,
        )
        if availability is not None and not availability.available:
            self.build.refresh()
            return
        self._requested_methods[track.track_id] = requested
        try:
            job_id = self._service.separate(track, method)
        except StemForgeError as error:
            self._failures[track.track_id] = JobFailure.from_error(error)
            self.build.refresh()
            return
        self._failures.pop(track.track_id, None)
        self._on_job_started(job_id, "separate", track.track_id)
        self.build.refresh()

    def _retry(self, track: Track) -> None:
        self._submit(track)

    def _keep_waiting(self) -> None:
        self._stall_ack = self._job_signature
        self._stalled = False
        self.build.refresh()

    # -- page callbacks -----------------------------------------------------

    def observe(self, job: JobView | None) -> None:
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

    def record_failure(self, track_id: str, failure: JobFailure) -> None:
        self._failures[track_id] = replace(
            failure,
            code=failure.code or errors.SEPARATION_ENGINE_ERROR,
        )
        if failure.code == errors.SEPARATION_MODEL_MISSING:
            self._service.remember_pending_separation(
                track_id,
                self._requested_methods.get(track_id, "demucs"),
            )
        if track_id == self._current_track_id:
            self.build.refresh()

    def record_model_failure(self, failure: JobFailure) -> None:
        self._download_failure = replace(
            failure,
            code=failure.code or errors.SEPARATION_MODEL_DOWNLOAD_FAILED,
            retryable=True,
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

    def record_success(self, track_id: str) -> None:
        self._failures.pop(track_id, None)
        if track_id == self._current_track_id:
            self.build.refresh()

    def clear_failure(self, track_id: str) -> None:
        if self._failures.pop(track_id, None) is not None and track_id == self._current_track_id:
            self.build.refresh()

    def failure_code(self, track_id: str) -> str | None:
        """The rail's red dot needs the last failure without re-rendering the card."""
        failure = self._failures.get(track_id)
        return failure.code if failure is not None else None

    def forget_track(self, track_id: str) -> None:
        """Drop per-Track view state when the Track is removed."""
        self._failures.pop(track_id, None)
        self._requested_methods.pop(track_id, None)
