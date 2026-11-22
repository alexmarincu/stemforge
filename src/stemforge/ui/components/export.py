"""The Export sheet and the Export status bar.

The sheet is the format authority: WAV 24-bit by default, FLAC
24-bit, or MP3 320, with a computed size estimate. The source is fixed by the
entry point — an Outputs row's export icon or the transport's Export for the
current listening target — so the sheet never asks the user to re-pick an
artifact. It then opens the native save dialog through the injected seam, which
owns the strict filter and the extension repair. There is no "Save original", no
batch, and no second player.

A failed Export is never a modal error: the page's one Job bar shows progress
while the Job runs, then the inline status bar shows the destination on success
or the coded error with Retry. Retry re-submits the
same spec without reopening the sheet or the save dialog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from nicegui import ui

from stemforge import errors
from stemforge.application import ExportFormat, ExportTarget
from stemforge.domain.models import Track
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import SaveAudioFileDialog, SaveSelection
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    error_details,
)
from stemforge.ui.failure import JobFailure
from stemforge.ui.toast import toast

if TYPE_CHECKING:
    from stemforge.application import WorkspaceService

FORMAT_ORDER: Final = (ExportFormat.WAV24, ExportFormat.FLAC24, ExportFormat.MP3_320)
FORMAT_LABELS: Final[Mapping[ExportFormat, str]] = {
    ExportFormat.WAV24: formatting.EXPORT_FORMAT_WAV24,
    ExportFormat.FLAC24: formatting.EXPORT_FORMAT_FLAC24,
    ExportFormat.MP3_320: formatting.EXPORT_FORMAT_MP3_320,
}
# A computed, deliberately rough
# per-minute size. The Job's own free-space check is the authoritative estimate.
_MB_PER_MINUTE: Final[Mapping[ExportFormat, float]] = {
    ExportFormat.WAV24: 15.9,
    ExportFormat.FLAC24: 9.4,
    ExportFormat.MP3_320: 2.4,
}
DEFAULT_FORMAT: Final = ExportFormat.WAV24

JobStarted = Callable[[str, "ExportSpec"], None]


@dataclass(frozen=True, slots=True)
class ExportSpec:
    """Everything Retry needs, with no dependency on the save dialog again."""

    track_id: str
    target_key: str
    target_label: str
    fmt: ExportFormat
    destination_dir: Path
    destination_name: str
    corrected: bool = False


@dataclass(frozen=True, slots=True)
class ExportSuccess:
    """One finished Export that wrote its destination."""

    spec: ExportSpec
    path: Path


@dataclass(frozen=True, slots=True)
class ExportFailure:
    """One finished Export that failed; Retry reuses the same spec."""

    spec: ExportSpec
    failure: JobFailure


type ExportNotice = ExportSuccess | ExportFailure


class ExportSheet:
    """The one Export sheet for a page; the format choice is remembered per Track."""

    def __init__(
        self,
        service: WorkspaceService,
        save_dialog: SaveAudioFileDialog,
        *,
        on_job_started: JobStarted,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._save_dialog = save_dialog
        self._on_job_started = on_job_started
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        self._formats: dict[str, ExportFormat] = {}
        self._target_key: str | None = None
        self._track: Track | None = None
        self._dialog: ui.dialog | None = None
        self._body: ui.column | None = None
        self._initial_directory: Path | None = None
        self._submit_error: JobFailure | None = None
        self._saving = False

    # -- composition --------------------------------------------------------

    def build(self) -> None:
        """Create the one dialog inside the page's persistent slot.

        NiceGUI deletes a ``ui.dialog`` created inside a transient event slot as
        soon as that slot is rebuilt, so the sheet is built once with the page
        and only its body is re-rendered afterwards.
        """
        if self._dialog is not None:
            return
        self._dialog = ui.dialog()
        with self._dialog, ui.card().classes("sf-card sf-export-modal").mark("export-sheet"):
            self._body = ui.column().classes("sf-export-body")

    def open(
        self,
        track: Track,
        *,
        target_key: str,
        busy: bool = False,
        directory: Path | None = None,
    ) -> None:
        """Open the sheet for one Track and the artifact the entry point chose."""
        self.build()
        self._track = track
        self._target_key = target_key
        self._initial_directory = directory
        self._submit_error = None
        self._saving = False
        self._render(busy=busy)
        assert self._dialog is not None
        self._dialog.open()

    def close(self) -> None:
        if self._dialog is not None:
            self._dialog.close()

    def _render(self, *, busy: bool) -> None:
        body = self._body
        track = self._track
        if body is None or track is None:
            return
        body.clear()
        targets = self._service.export_targets(track)
        selected_target = self._selected_target(track, targets)
        with body:
            ui.label(formatting.EXPORT_TITLE).classes("sf-export-title")
            if selected_target is None:
                ui.label(formatting.EXPORT_NO_TARGETS).classes("sf-lede")
                self._footer(busy=busy, can_submit=False)
                return
            ui.label(formatting.EXPORT_FORMAT_LABEL).classes("sf-versions-label")
            self._format_rows(track)
            self._summary(track, selected_target)
            self._submit_error_block()
            self._footer(busy=busy, can_submit=True)

    def _format_rows(self, track: Track) -> None:
        selected = self._formats.get(track.track_id, DEFAULT_FORMAT)
        with ui.element("div").classes("sf-format-list"):
            for fmt in FORMAT_ORDER:
                active = " is-active" if fmt is selected else ""
                row = (
                    ui.element("div")
                    .classes(f"sf-format-row{active}")
                    .mark("export-format", f"export-format-{fmt.value}")
                )
                with row:
                    ui.element("span").classes("sf-format-radio")
                    with ui.element("div").classes("sf-format-main"):
                        ui.label(FORMAT_LABELS[fmt]).classes("sf-format-name")
                        ui.label(self._estimate(track, fmt)).classes("sf-format-size")
                row.on("click", lambda _=None, item=fmt: self._choose_format(track, item))

    def _summary(self, track: Track, target: ExportTarget) -> None:
        fmt = self._formats.get(track.track_id, DEFAULT_FORMAT)
        with ui.element("div").classes("sf-export-summary"):
            with ui.element("div").classes("sf-fact"):
                ui.label(formatting.EXPORT_SOURCE_LABEL).classes("sf-fact-key")
                ui.label(target.label).classes("sf-fact-value").mark("export-source-label")
            with ui.element("div").classes("sf-fact"):
                ui.label(formatting.EXPORT_DEFAULT_NAME_LABEL).classes("sf-fact-key")
                ui.label(f"{target.base_name}{fmt.suffix}").classes("sf-fact-value").mark(
                    "export-default-name"
                )
            with ui.element("div").classes("sf-fact"):
                ui.label(formatting.EXPORT_DESTINATION_LABEL).classes("sf-fact-key")
                ui.label(self._destination_label()).classes("sf-fact-value").mark(
                    "export-destination-hint"
                )

    def _submit_error_block(self) -> None:
        if self._submit_error is None:
            return
        failure = self._submit_error
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
        )
        with ui.element("div").classes("sf-notice").mark("export-submit-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            error_details(
                code=failure.code,
                marker="export-submit",
                diagnostics=self._diagnostics,
                clipboard=self._clipboard,
                open_path=self._open_path,
            )

    def _footer(self, *, busy: bool, can_submit: bool) -> None:
        with ui.element("div").classes("sf-export-footer"):
            cancel = (
                ui.button(formatting.CANCEL, on_click=lambda _=None: self.close(), color=None)
                .props("flat no-caps size=sm")
                .mark("export-cancel")
            )
            cancel.set_enabled(not busy)
            confirm = (
                ui.button(formatting.EXPORT_CONFIRM, on_click=self._confirm)
                .props("unelevated no-caps size=sm")
                .classes("sf-export-confirm")
                .mark("export-confirm")
            )
            confirm.set_enabled(not busy and can_submit)

    # -- selection ----------------------------------------------------------

    def _selected_target(
        self, track: Track, targets: tuple[ExportTarget, ...]
    ) -> ExportTarget | None:
        return next((target for target in targets if target.key == self._target_key), None)

    def _choose_format(self, track: Track, fmt: ExportFormat) -> None:
        self._formats[track.track_id] = fmt
        self._render(busy=False)

    @staticmethod
    def _estimate(track: Track, fmt: ExportFormat) -> str:
        minutes = max(0.0, track.duration_s or 0.0) / 60
        return formatting.EXPORT_ESTIMATE.format(size=f"{minutes * _MB_PER_MINUTE[fmt]:.1f}")

    # -- submission ---------------------------------------------------------

    async def _confirm(self) -> None:
        track = self._track
        if track is None or self._saving:
            return
        targets = self._service.export_targets(track)
        target = self._selected_target(track, targets)
        fmt = self._formats.get(track.track_id, DEFAULT_FORMAT)
        if target is None:
            return
        # The native dialog is modal but the webview keeps
        # running, so the sheet's own actions stay disabled while it is open.
        self._saving = True
        self._render(busy=True)
        selection = await self._save_dialog.save_audio_file(
            default_name=f"{target.base_name}{fmt.suffix}",
            suffix=fmt.suffix,
            directory=self._default_directory(),
        )
        self._saving = False
        if selection is None:
            self._render(busy=False)
            return
        body = self._body
        if selection.corrected and body is not None:
            # The await outlived the button slot; attach the toast to the
            # persistent sheet body instead of the deleted click context.
            with body:
                toast(
                    formatting.EXPORT_EXTENSION_CHANGED.format(
                        suffix=fmt.suffix,
                        label=FORMAT_LABELS[fmt],
                    ),
                    type="info",
                )
        spec = self._spec(track, target, fmt, selection)
        try:
            job_id = self._service.submit_export(
                track,
                target.key,
                fmt,
                spec.destination_dir,
                destination_name=spec.destination_name,
            )
        except StemForgeError as error:
            self._submit_error = JobFailure.from_error(error)
            self._render(busy=False)
            return
        self._submit_error = None
        self.close()
        self._on_job_started(job_id, spec)

    def _spec(
        self,
        track: Track,
        target: ExportTarget,
        fmt: ExportFormat,
        selection: SaveSelection,
    ) -> ExportSpec:
        return ExportSpec(
            track_id=track.track_id,
            target_key=target.key,
            target_label=target.label,
            fmt=fmt,
            destination_dir=selection.path.parent,
            destination_name=selection.path.name,
            corrected=selection.corrected,
        )

    def _default_directory(self) -> Path | None:
        if self._initial_directory is not None:
            return self._initial_directory
        return self._service.preferred_export_dir()

    def _destination_label(self) -> str:
        preferred = self._default_directory()
        return str(preferred) if preferred is not None else formatting.EXPORT_DESTINATION_DEFAULT


class ExportStatusBar:
    """The inline success/failure surface that replaces the Job bar."""

    def build(
        self,
        notice: ExportNotice,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_dismiss: Callable[[], None],
        on_choose_folder: Callable[[], None] | None = None,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        if isinstance(notice, ExportSuccess):
            self._success(notice, on_dismiss=on_dismiss)
            return
        self._failure(
            notice,
            busy=busy,
            on_retry=on_retry,
            on_dismiss=on_dismiss,
            on_choose_folder=on_choose_folder,
            diagnostics=diagnostics,
            clipboard=clipboard,
            open_path=open_path,
        )

    def _success(self, notice: ExportSuccess, *, on_dismiss: Callable[[], None]) -> None:
        with ui.element("div").classes("sf-exportbar is-success").mark("export-success"):
            ui.icon("check", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(formatting.EXPORTED_TO.format(path=notice.path)).classes(
                    "sf-exportbar-title"
                ).mark("export-destination")
                if notice.spec.corrected:
                    ui.label(
                        formatting.EXPORT_EXTENSION_CHANGED.format(
                            suffix=notice.spec.fmt.suffix,
                            label=FORMAT_LABELS[notice.spec.fmt],
                        )
                    ).classes("sf-exportbar-detail").mark("export-correction")
            (
                ui.button(
                    formatting.EXPORT_DISMISS, on_click=lambda _=None: on_dismiss(), color=None
                )
                .props("flat dense no-caps size=sm")
                .mark("export-dismiss")
            )

    def _failure(
        self,
        notice: ExportFailure,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_dismiss: Callable[[], None],
        on_choose_folder: Callable[[], None] | None,
        diagnostics: DiagnosticsProvider | None,
        clipboard: ClipboardWriter | None,
        open_path: PathOpener | None,
    ) -> None:
        failure = notice.failure
        code = failure.code or errors.INTERNAL_UNEXPECTED
        reason = (
            "export"
            if code == errors.STORAGE_DISK_FULL
            else formatting.reason_from_detail(failure.detail)
        )
        copy = formatting.copy_for(code, reason, context=failure.context)
        with ui.element("div").classes("sf-exportbar is-failure").mark("export-failure"):
            ui.icon("error_outline", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(copy.headline).classes("sf-exportbar-title").mark("export-headline")
                ui.label(copy.action).classes("sf-exportbar-detail")
                error_details(
                    code=code,
                    marker="export-error",
                    job_id=failure.job_id,
                    log_path=failure.log_path,
                    diagnostics=diagnostics,
                    clipboard=clipboard,
                    open_path=open_path,
                )
            if failure.retryable:
                retry = (
                    ui.button(formatting.RETRY, on_click=lambda _=None: on_retry())
                    .props("unelevated dense no-caps size=sm")
                    .mark("export-retry")
                )
                retry.set_enabled(not busy)
            if on_choose_folder is not None and code in {
                errors.EXPORT_WRITE_FAILED,
                errors.STORAGE_DISK_FULL,
            }:
                choose = (
                    ui.button(
                        formatting.EXPORT_CHOOSE_FOLDER,
                        on_click=lambda _=None: on_choose_folder(),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("export-choose-folder")
                )
                choose.set_enabled(not busy)
            (
                ui.button(
                    formatting.EXPORT_DISMISS, on_click=lambda _=None: on_dismiss(), color=None
                )
                .props("flat dense no-caps size=sm")
                .mark("export-dismiss")
            )
