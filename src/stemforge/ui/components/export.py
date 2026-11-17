"""The Export sheet and the Export status bar.

The sheet is the format/source authority (ticket 08 §9): WAV 24-bit by default,
FLAC 24-bit, or MP3 320, with every target from ``WorkspaceService.export_targets``
(master, Stems, Instrumental, and pitch render members). It then opens the native
save dialog through the injected seam, which owns the strict filter and the
extension repair. There is no "Save original", no batch, and no second player.

A failed Export is never a modal error: the page's one Job bar shows progress
while the Job runs, then the inline status bar shows the destination on success
or the coded error with Retry (ticket 14 §2, §4.6, §8). Retry re-submits the
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

if TYPE_CHECKING:
    from stemforge.application import WorkspaceService

FORMAT_LABELS: Final[Mapping[ExportFormat, str]] = {
    ExportFormat.WAV24: "WAV 24-bit",
    ExportFormat.FLAC24: "FLAC 24-bit",
    ExportFormat.MP3_320: "MP3 320",
}
FORMAT_ORDER: Final = (ExportFormat.WAV24, ExportFormat.FLAC24, ExportFormat.MP3_320)
# Ticket 08 §9 / prototype 08 export sheet: a computed, deliberately rough
# per-minute size. The Job's own free-space check is the authoritative estimate.
_MB_PER_MINUTE: Final[Mapping[ExportFormat, float]] = {
    ExportFormat.WAV24: 15.9,
    ExportFormat.FLAC24: 9.4,
    ExportFormat.MP3_320: 2.4,
}
DEFAULT_FORMAT: Final = ExportFormat.WAV24

EXPORT_TITLE: Final = "Export"
EXPORT_FORMAT: Final = "Format"
EXPORT_SOURCE: Final = "Source"
EXPORT_DEFAULT_NAME: Final = "File name"
EXPORT_DESTINATION: Final = "Save to"
EXPORT_DESTINATION_DEFAULT: Final = "Your system's default location"
EXPORT_CONFIRM: Final = "Export"
EXPORT_CANCEL: Final = "Cancel"
EXPORT_RETRY: Final = "Retry"
EXPORT_DISMISS: Final = "Dismiss"
EXPORT_CANCELLED: Final = "Export cancelled."
EXPORT_NO_TARGETS: Final = "No exportable audio is available for this Track."

JobStarted = Callable[[str, "ExportSpec"], None]
TargetSelected = Callable[[str, str], None]


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
class ExportNotice:
    """One finished Export shown in place of the Job bar."""

    spec: ExportSpec
    path: Path | None = None
    code: str | None = None
    detail: str | None = None
    retryable: bool = False
    job_id: str | None = None
    log_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.path is not None

    @classmethod
    def success(cls, spec: ExportSpec, path: Path) -> ExportNotice:
        return cls(spec=spec, path=path)

    @classmethod
    def failure(
        cls,
        spec: ExportSpec,
        *,
        code: str,
        detail: str | None,
        retryable: bool,
        job_id: str | None = None,
        log_path: Path | None = None,
    ) -> ExportNotice:
        return cls(
            spec=spec,
            code=code,
            detail=detail,
            retryable=retryable,
            job_id=job_id,
            log_path=log_path,
        )


class ExportSheet:
    """The one Export sheet for a page; choices are remembered per Track."""

    def __init__(
        self,
        service: WorkspaceService,
        save_dialog: SaveAudioFileDialog,
        *,
        on_job_started: JobStarted,
        on_target_selected: TargetSelected,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._save_dialog = save_dialog
        self._on_job_started = on_job_started
        self._on_target_selected = on_target_selected
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        self._formats: dict[str, ExportFormat] = {}
        self._targets: dict[str, str] = {}
        self._track: Track | None = None
        self._dialog: ui.dialog | None = None
        self._body: ui.column | None = None
        self._submit_error: tuple[str, str | None] | None = None
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
        initial_target: str | None = None,
        busy: bool = False,
    ) -> None:
        """Open the sheet for one Track, preselecting ``initial_target``."""
        self.build()
        self._track = track
        self._submit_error = None
        self._saving = False
        targets = self._service.export_targets(track)
        keys = {target.key for target in targets}
        remembered = self._targets.get(track.track_id)
        selected = next(
            (key for key in (initial_target, remembered) if key is not None and key in keys),
            targets[0].key if targets else None,
        )
        if selected is not None:
            self._targets[track.track_id] = selected
            self._on_target_selected(track.track_id, selected)
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
        with body:
            ui.label(EXPORT_TITLE).classes("sf-export-title")
            if not targets:
                ui.label(EXPORT_NO_TARGETS).classes("sf-lede")
                self._footer(busy=True, can_submit=False)
                return
            selected_target = self._selected_target(track, targets)
            assert selected_target is not None
            ui.label(EXPORT_FORMAT).classes("sf-versions-label")
            self._format_rows(track)
            ui.label(EXPORT_SOURCE).classes("sf-versions-label")
            with ui.element("div").classes("sf-export-targets"):
                for target in targets:
                    self._target_row(track, target, selected_target)
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

    def _target_row(self, track: Track, target: ExportTarget, selected: ExportTarget) -> None:
        active = " is-active" if target.key == selected.key else ""
        row = (
            ui.element("div")
            .classes(f"sf-target-row{active}")
            .mark("export-target", f"export-target-{target.key}")
        )
        with row:
            ui.element("span").classes("sf-format-radio")
            with ui.element("div").classes("sf-format-main"):
                ui.label(target.label).classes("sf-format-name")
                ui.label(self._target_meta(target)).classes("sf-format-size")
        row.on("click", lambda _=None, item=target: self._choose_target(track, item))

    def _summary(self, track: Track, target: ExportTarget) -> None:
        fmt = self._formats.get(track.track_id, DEFAULT_FORMAT)
        with ui.element("div").classes("sf-export-summary"):
            with ui.element("div").classes("sf-fact"):
                ui.label(EXPORT_DEFAULT_NAME).classes("sf-fact-key")
                ui.label(f"{target.base_name}{fmt.suffix}").classes("sf-fact-value").mark(
                    "export-default-name"
                )
            with ui.element("div").classes("sf-fact"):
                ui.label(EXPORT_DESTINATION).classes("sf-fact-key")
                ui.label(self._destination_label()).classes("sf-fact-value").mark(
                    "export-destination-hint"
                )

    def _submit_error_block(self) -> None:
        if self._submit_error is None:
            return
        code, detail = self._submit_error
        copy = formatting.copy_for(code, detail)
        with ui.element("div").classes("sf-notice").mark("export-submit-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            error_details(
                code=code,
                marker="export-submit",
                diagnostics=self._diagnostics,
                clipboard=self._clipboard,
                open_path=self._open_path,
            )

    def _footer(self, *, busy: bool, can_submit: bool) -> None:
        with ui.element("div").classes("sf-export-footer"):
            cancel = (
                ui.button(EXPORT_CANCEL, on_click=lambda _=None: self.close())
                .props("flat no-caps size=sm")
                .mark("export-cancel")
            )
            cancel.set_enabled(not busy)
            confirm = (
                ui.button(EXPORT_CONFIRM, on_click=self._confirm)
                .props("unelevated no-caps size=sm")
                .classes("sf-export-confirm")
                .mark("export-confirm")
            )
            confirm.set_enabled(not busy and can_submit)

    # -- selection ----------------------------------------------------------

    def _selected_target(
        self, track: Track, targets: tuple[ExportTarget, ...]
    ) -> ExportTarget | None:
        key = self._targets.get(track.track_id)
        found = next((target for target in targets if target.key == key), None)
        if found is not None:
            return found
        first = targets[0] if targets else None
        if first is not None:
            self._targets[track.track_id] = first.key
        return first

    def _choose_format(self, track: Track, fmt: ExportFormat) -> None:
        self._formats[track.track_id] = fmt
        self._render(busy=False)

    def _choose_target(self, track: Track, target: ExportTarget) -> None:
        self._targets[track.track_id] = target.key
        self._on_target_selected(track.track_id, target.key)
        self._render(busy=False)

    def _target_meta(self, target: ExportTarget) -> str:
        scope = {
            "master": "master",
            "separation": "stem",
        }.get(target.scope, "render")
        if target.render_key is not None and target.semitones:
            return f"{scope} · {target.semitones:+d} st"
        return scope

    @staticmethod
    def _estimate(track: Track, fmt: ExportFormat) -> str:
        minutes = max(0.0, track.source.metadata.duration_s or 0.0) / 60
        return f"about {minutes * _MB_PER_MINUTE[fmt]:.1f} MB"

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
        # Ticket 01 §8.3: the native dialog is modal but the webview keeps
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
                ui.notify(
                    f"Extension changed to {fmt.suffix} for {FORMAT_LABELS[fmt]}.",
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
            self._submit_error = (error.code, error.detail)
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
        preferred = self._service.settings.preferred_export_dir
        if preferred is not None and preferred.is_dir():
            return preferred
        return None

    def _destination_label(self) -> str:
        preferred = self._default_directory()
        return str(preferred) if preferred is not None else EXPORT_DESTINATION_DEFAULT


class ExportStatusBar:
    """The inline success/failure surface that replaces the Job bar."""

    def build(
        self,
        notice: ExportNotice,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_dismiss: Callable[[], None],
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        if notice.succeeded:
            self._success(notice, on_dismiss=on_dismiss)
            return
        self._failure(
            notice,
            busy=busy,
            on_retry=on_retry,
            on_dismiss=on_dismiss,
            diagnostics=diagnostics,
            clipboard=clipboard,
            open_path=open_path,
        )

    def _success(self, notice: ExportNotice, *, on_dismiss: Callable[[], None]) -> None:
        with ui.element("div").classes("sf-exportbar is-success").mark("export-success"):
            ui.icon("check", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(f"Exported to {notice.path}").classes("sf-exportbar-title").mark(
                    "export-destination"
                )
                if notice.spec.corrected:
                    ui.label(
                        f"Extension changed to {notice.spec.fmt.suffix} "
                        f"for {FORMAT_LABELS[notice.spec.fmt]}."
                    ).classes("sf-exportbar-detail").mark("export-correction")
            (
                ui.button(EXPORT_DISMISS, on_click=lambda _=None: on_dismiss())
                .props("flat dense no-caps size=sm")
                .mark("export-dismiss")
            )

    def _failure(
        self,
        notice: ExportNotice,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_dismiss: Callable[[], None],
        diagnostics: DiagnosticsProvider | None,
        clipboard: ClipboardWriter | None,
        open_path: PathOpener | None,
    ) -> None:
        code = notice.code or errors.INTERNAL_UNEXPECTED
        reason = (
            "export"
            if code == errors.STORAGE_DISK_FULL
            else formatting.reason_from_detail(notice.detail)
        )
        copy = formatting.copy_for(code, reason)
        with ui.element("div").classes("sf-exportbar is-failure").mark("export-failure"):
            ui.icon("error_outline", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(copy.headline).classes("sf-exportbar-title").mark("export-headline")
                ui.label(copy.action).classes("sf-exportbar-detail")
                error_details(
                    code=code,
                    marker="export-error",
                    job_id=notice.job_id,
                    log_path=notice.log_path,
                    diagnostics=diagnostics,
                    clipboard=clipboard,
                    open_path=open_path,
                )
            if notice.retryable:
                retry = (
                    ui.button(EXPORT_RETRY, on_click=lambda _=None: on_retry())
                    .props("unelevated dense no-caps size=sm")
                    .mark("export-retry")
                )
                retry.set_enabled(not busy)
            (
                ui.button(EXPORT_DISMISS, on_click=lambda _=None: on_dismiss())
                .props("flat dense no-caps size=sm")
                .mark("export-dismiss")
            )
