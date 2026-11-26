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

from stemforge import errors, i18n
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
    from stemforge.application import JobView, WorkspaceService

JobStarted = Callable[[str, str, str | None], None]
CancelJob = Callable[[], None]


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
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        # Injectable so tests advance time instead of sleeping for minutes.
        self.clock = clock
        self.stall_seconds = stall_seconds
        self._failures: dict[str, JobFailure] = {}
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
        has_stems = self._service.has_separation(track)
        status = self._service.model_status()
        failure = self._failures.get(track.track_id)
        model_missing = failure is not None and failure.code == errors.SEPARATION_MODEL_MISSING
        checksum_failure: JobFailure | None = None
        for candidate in (failure, self._download_failure):
            if (
                candidate is not None
                and candidate.code == errors.SEPARATION_MODEL_MISSING
                and formatting.reason_from_detail(candidate.detail) == "checksum"
            ):
                checksum_failure = candidate
                break
        with ui.element("section").classes("sf-card").mark("separation-card"):
            ui.label(i18n.t("separation.title")).classes("sf-card-title")
            gate_visible = not status.installed or model_missing
            if gate_visible:
                self._gate(busy=busy, checksum_failure=checksum_failure)
            if self._running():
                self._running_block()
                if has_stems:
                    ui.label(i18n.t("separation.last_success")).classes("sf-fineprint").mark(
                        "separation-last-success"
                    )
            elif has_stems:
                ui.label(i18n.t("separation.done")).classes("sf-separation-status").mark(
                    "separation-status"
                )
                if gate_visible or failure is not None:
                    ui.label(i18n.t("separation.last_success")).classes("sf-fineprint").mark(
                        "separation-last-success"
                    )
                if not gate_visible:
                    self._primary(track, busy, again=True)
            elif not gate_visible:
                ui.label(i18n.t("separation.not_separated")).classes("sf-separation-status").mark(
                    "separation-status"
                )
                ui.label(i18n.t("separation.not_separated_hint")).classes("sf-fineprint")
                self._primary(track, busy, again=False)
            self._error(track, busy)
            if not gate_visible and has_stems:
                ui.label(i18n.t("separation.primary_note")).classes("sf-fineprint")

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
            ui.label(copy.details_label).classes("sf-card-title")
            ui.label(copy.notice).classes("sf-gate-notice").mark("model-licence-notice")
            ui.label(i18n.t("separation.model.consent")).classes("sf-fineprint")
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
                progress = (
                    i18n.t(
                        "separation.model.downloading_detail",
                        detail=self._job.detail,
                        percent=percent,
                    )
                    if self._job.detail
                    else i18n.t("separation.model.downloading_progress", percent=percent)
                )
                ui.label(progress).classes("sf-separation-status").mark("model-download-progress")
                (
                    ui.button(
                        i18n.t("common.cancel"),
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
                i18n.t("separation.model.retry"),
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
                    i18n.t("settings.cache.clear"),
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
        ui.label(i18n.t("separation.running", percent=percent)).classes(
            "sf-separation-status"
        ).mark("separation-status")
        ui.linear_progress(value=float(self._job.progress), show_value=False).classes(
            "sf-job-progress"
        ).mark("separation-progress")
        if self._job.stage:
            ui.label(self._job.stage).classes("sf-fineprint").mark("separation-stage")
        ui.label(i18n.t("separation.worker_note")).classes("sf-fineprint")
        (
            ui.button(
                i18n.t("common.cancel"), on_click=lambda _=None: self._on_cancel(), color=None
            )
            .props("flat no-caps size=sm")
            .mark("separation-cancel")
        )
        if self._stalled:
            self._stall_block()

    def _stall_block(self) -> None:
        seconds = max(1, round(self.stall_seconds))
        with ui.element("div").classes("sf-stall").mark("separation-stall"):
            ui.label(i18n.t("separation.stall", seconds=seconds)).classes("sf-stall-text")
            with ui.element("div").classes("sf-actions"):
                (
                    ui.button(
                        i18n.t("separation.keep_waiting"),
                        on_click=lambda _=None: self._keep_waiting(),
                        color=None,
                    )
                    .props("flat no-caps size=sm")
                    .mark("separation-keep-waiting")
                )
                (
                    ui.button(
                        i18n.t("common.cancel"),
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
            if failure.retryable:
                retry = (
                    ui.button(
                        i18n.t("common.retry"),
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
                        i18n.t("settings.cache.clear"),
                        on_click=lambda _=None: self._on_clear_cache(),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("separation-clear-cache")
                )
                clear.set_enabled(not busy)

    # -- actions ------------------------------------------------------------

    def _primary(self, track: Track, busy: bool, *, again: bool) -> None:
        button = (
            ui.button(
                i18n.t("separation.again") if again else i18n.t("separation.action"),
                icon="call_split",
                on_click=lambda _=None: self._submit(track),
            )
            .props("unelevated no-caps")
            .classes("sf-control sf-control--primary")
            .mark("separation-action")
        )
        button.set_enabled(not busy)

    def request_separation(self, track: Track) -> None:
        """The Outputs placeholder's Separate button uses the same request path."""
        self._submit(track)

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

    def _submit(self, track: Track) -> None:
        try:
            job_id = self._service.separate(track)
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
            self._service.remember_pending_separation(track_id)
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
