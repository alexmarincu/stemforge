"""The Export sheet and the Export status bars.

The sheet is the format authority: WAV 24-bit by default, FLAC
24-bit, or MP3 320, with a computed size estimate. In its single mode the source
is fixed by the entry point — an Outputs row's export icon or the transport's
Export for the current listening target — so the sheet never asks the user to
re-pick an artifact. It then opens the native save dialog through the injected
seam, which owns the strict filter and the extension repair. In its batch mode
one format is chosen for the whole Artifact set, the source is the set's output
list, and the native folder dialog replaces the save dialog.

For a local Source's Original target, the single-mode sheet also offers the
Imported file block: Save original copies the Native file unchanged and is its
own Job, not an Export format (ADR 0017).

A failed Export is never a modal error: the page's one Job bar shows progress
while the Job runs, then the inline status bar shows the destination on success
or the coded error with Retry. Retry re-submits the same spec without reopening
the sheet or the save dialog. A batch leaves a per-item results bar instead
(ADR 0015); Save original uses its own sibling bar.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from nicegui import ui

from stemforge import errors, i18n
from stemforge.application import (
    ExportBatchItem,
    ExportBatchItemState,
    ExportBatchResult,
    ExportFormat,
    ExportTarget,
)
from stemforge.domain.models import Track
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import FolderDialog, SaveAudioFileDialog, SaveSelection
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
FORMAT_KEYS: Final[Mapping[ExportFormat, str]] = {
    ExportFormat.WAV24: "export.format.wav24",
    ExportFormat.FLAC24: "export.format.flac24",
    ExportFormat.MP3_320: "export.format.mp3_320",
}
# A computed, deliberately rough
# per-minute size. The Job's own free-space check is the authoritative estimate.
_MB_PER_MINUTE: Final[Mapping[ExportFormat, float]] = {
    ExportFormat.WAV24: 15.9,
    ExportFormat.FLAC24: 9.4,
    ExportFormat.MP3_320: 2.4,
}
DEFAULT_FORMAT: Final = ExportFormat.WAV24
ORIGINAL_TARGET_KEY: Final = "master"


def _format_label(fmt: ExportFormat) -> str:
    """The sheet's display name for one export format."""
    return i18n.t(FORMAT_KEYS[fmt])


JobStarted = Callable[[str, "ExportSpec"], None]
BatchStarted = Callable[[str, "ExportBatchSpec"], None]
SaveOriginalStarted = Callable[[str, "SaveOriginalSpec"], None]


@dataclass(frozen=True, slots=True)
class ExportSpec:
    """Everything Retry needs, with no dependency on the save dialog again."""

    track_id: str
    target_key: str
    fmt: ExportFormat
    destination_dir: Path
    destination_name: str
    corrected: bool = False


@dataclass(frozen=True, slots=True)
class ExportBatchSpec:
    """Everything a failed or cancelled batch needs to resubmit its subset."""

    track_id: str
    source_id: str
    version_key: str
    fmt: ExportFormat
    destination_dir: Path
    target_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExportBatchNotice:
    """A finished batch's recorded outcomes and the Job failure that ended it."""

    spec: ExportBatchSpec
    result: ExportBatchResult
    cancelled: bool = False
    aborted: bool = False
    failure: JobFailure | None = None


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


@dataclass(frozen=True, slots=True)
class SaveOriginalSpec:
    """Everything Retry or another location needs, without reopening the sheet."""

    track_id: str
    suffix: str
    destination_dir: Path
    destination_name: str
    corrected: bool = False


@dataclass(frozen=True, slots=True)
class SaveOriginalSuccess:
    """One finished Save original that copied its destination."""

    spec: SaveOriginalSpec
    path: Path


@dataclass(frozen=True, slots=True)
class SaveOriginalFailure:
    """One finished Save original that failed; Retry reuses the same spec."""

    spec: SaveOriginalSpec
    failure: JobFailure


type SaveOriginalNotice = SaveOriginalSuccess | SaveOriginalFailure


class ExportSheet:
    """The one Export sheet for a page; the format choice is remembered per Track."""

    def __init__(
        self,
        service: WorkspaceService,
        save_dialog: SaveAudioFileDialog,
        folder_dialog: FolderDialog,
        *,
        on_job_started: JobStarted,
        on_batch_started: BatchStarted,
        on_save_original_started: SaveOriginalStarted,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._save_dialog = save_dialog
        self._folder_dialog = folder_dialog
        self._on_job_started = on_job_started
        self._on_batch_started = on_batch_started
        self._on_save_original_started = on_save_original_started
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
        self._batch_mode = False
        self._batch_version_key: str | None = None
        self._batch_folder: Path | None = None

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
        with (
            self._dialog,
            ui.card().classes("sf-card sf-export-modal").mark("export-sheet") as card,
        ):
            self._body = ui.column().classes("sf-export-body")
        # A closed dialog keeps its card in the element tree, so tether the
        # card's visibility to the open state to make ``visible`` mean on screen.
        self._dialog.bind_value_to(card, "visible")

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
        self._batch_mode = False
        self._track = track
        self._target_key = target_key
        self._initial_directory = directory
        self._submit_error = None
        self._saving = False
        self._render(busy=busy)
        assert self._dialog is not None
        self._dialog.open()

    def open_batch(
        self,
        track: Track,
        *,
        version_key: str,
        busy: bool = False,
        directory: Path | None = None,
    ) -> None:
        """Open the sheet for the active version's whole output set."""
        self.build()
        self._batch_mode = True
        self._track = track
        self._target_key = None
        self._batch_version_key = version_key
        self._batch_folder = directory or self._service.preferred_export_dir()
        self._initial_directory = directory
        self._submit_error = None
        self._saving = False
        self._render(busy=busy)
        assert self._dialog is not None
        self._dialog.open()

    def close(self) -> None:
        if self._dialog is not None:
            self._dialog.close()

    def _run_in_sheet(self, callback: Callable[[], None]) -> None:
        """Run a post-dialog callback in the sheet's still-live slot.

        An awaited dialog resumes in the button slot that ``_render(busy=True)``
        replaced, so page-level updates such as ``ui.page_title`` need a live
        element to resolve the client.
        """
        dialog = self._dialog
        if dialog is None:
            callback()
            return
        with dialog:
            callback()

    def _render(self, *, busy: bool) -> None:
        body = self._body
        track = self._track
        if body is None or track is None:
            return
        body.clear()
        if self._batch_mode:
            self._render_batch(track, busy=busy)
            return
        targets = self._service.export_targets(track)
        selected_target = self._selected_target(track, targets)
        with body:
            ui.label(i18n.t("export.title")).classes("sf-export-title")
            if selected_target is None:
                ui.label(i18n.t("export.no_targets")).classes("sf-lede")
                self._footer(busy=busy, can_submit=False)
                return
            ui.label(i18n.t("export.format")).classes("sf-versions-label")
            self._format_rows(track)
            self._summary(track, selected_target)
            self._imported_file_block(track, selected_target, busy=busy)
            self._submit_error_block()
            self._footer(busy=busy, can_submit=True)

    def _render_batch(self, track: Track, *, busy: bool) -> None:
        body = self._body
        if body is None:
            return
        targets = self._batch_targets(track)
        with body:
            ui.label(i18n.t("page.outputs_export_all")).classes("sf-export-title")
            if not targets:
                ui.label(i18n.t("export.no_targets")).classes("sf-lede")
                self._footer(busy=busy, can_submit=False)
                return
            ui.label(i18n.t("export.format")).classes("sf-versions-label")
            self._format_rows(track, count=len(targets))
            self._batch_summary(targets, busy=busy)
            self._submit_error_block()
            self._footer(
                busy=busy,
                can_submit=self._batch_folder is not None,
                confirm=formatting.export_batch_confirm(len(targets)),
                on_confirm=self._confirm_batch,
            )

    def _format_rows(self, track: Track, *, count: int = 1) -> None:
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
                        ui.label(_format_label(fmt)).classes("sf-format-name")
                        ui.label(self._estimate(track, fmt, count=count)).classes("sf-format-size")
                row.on("click", lambda _=None, item=fmt: self._choose_format(track, item))

    def _batch_summary(self, targets: tuple[ExportTarget, ...], *, busy: bool) -> None:
        with ui.element("div").classes("sf-export-summary"):
            with ui.element("div").classes("sf-fact"):
                ui.label(i18n.t("export.source")).classes("sf-fact-key")
                ui.label(
                    ", ".join(formatting.label_text(target.label) for target in targets)
                ).classes("sf-fact-value").mark("export-batch-source")
            with ui.element("div").classes("sf-fact"):
                ui.label(i18n.t("export.destination")).classes("sf-fact-key")
                with ui.element("div").classes("sf-fact-value sf-export-folder"):
                    ui.label(self._batch_destination_label()).classes("sf-export-folder-path").mark(
                        "export-batch-destination"
                    )
                    choose = (
                        ui.button(
                            i18n.t("export.choose_folder_action"),
                            on_click=self._choose_batch_folder,
                            color=None,
                        )
                        .props("flat dense no-caps size=sm")
                        .mark("export-batch-folder-picker")
                    )
                    choose.set_enabled(not busy and not self._saving)
            if self._batch_folder is None:
                ui.label(i18n.t("export.batch.folder_required")).classes("sf-lede").mark(
                    "export-batch-folder-required"
                )

    def _batch_destination_label(self) -> str:
        if self._batch_folder is None:
            return i18n.t("export.destination_default")
        return str(self._batch_folder)

    def _summary(self, track: Track, target: ExportTarget) -> None:
        fmt = self._formats.get(track.track_id, DEFAULT_FORMAT)
        with ui.element("div").classes("sf-export-summary"):
            with ui.element("div").classes("sf-fact"):
                ui.label(i18n.t("export.source")).classes("sf-fact-key")
                ui.label(formatting.label_text(target.label)).classes("sf-fact-value").mark(
                    "export-source-label"
                )
            with ui.element("div").classes("sf-fact"):
                ui.label(i18n.t("export.default_name")).classes("sf-fact-key")
                ui.label(f"{target.base_name}{fmt.suffix}").classes("sf-fact-value").mark(
                    "export-default-name"
                )
            with ui.element("div").classes("sf-fact"):
                ui.label(i18n.t("export.destination")).classes("sf-fact-key")
                ui.label(self._destination_label()).classes("sf-fact-value").mark(
                    "export-destination-hint"
                )

    def _imported_file_block(
        self,
        track: Track,
        target: ExportTarget,
        *,
        busy: bool,
    ) -> None:
        """Offer Save original only on the Original target of a local Source."""
        if target.key != ORIGINAL_TARGET_KEY:
            return
        imported = self._service.imported_file(track)
        if imported is None:
            return
        with ui.element("div").classes("sf-export-summary").mark("imported-file"):
            ui.label(i18n.t("export.imported_file")).classes("sf-versions-label")
            ui.label(formatting.imported_file_meta(imported.suffix, imported.size_bytes)).classes(
                "sf-fact"
            ).mark("imported-file-meta")
            ui.label(i18n.t("export.imported_file_unchanged")).classes("sf-fact")
            save = (
                ui.button(
                    i18n.t("export.save_original_action"),
                    on_click=self._confirm_save_original,
                    color=None,
                )
                .props("flat dense no-caps size=sm")
                .classes("sf-imported-action")
                .mark("save-original")
            )
            save.set_enabled(not busy and not self._saving)

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

    def _footer(
        self,
        *,
        busy: bool,
        can_submit: bool,
        confirm: str | None = None,
        on_confirm: Callable[[], object] | None = None,
    ) -> None:
        with ui.element("div").classes("sf-export-footer"):
            cancel = (
                ui.button(i18n.t("common.cancel"), on_click=lambda _=None: self.close(), color=None)
                .props("flat no-caps size=sm")
                .mark("export-cancel")
            )
            cancel.set_enabled(not busy)
            confirm_button = (
                ui.button(
                    confirm if confirm is not None else i18n.t("common.export"),
                    on_click=on_confirm or self._confirm,
                )
                .props("unelevated no-caps size=sm")
                .classes("sf-export-confirm")
                .mark("export-confirm")
            )
            confirm_button.set_enabled(not busy and can_submit)

    # -- selection ----------------------------------------------------------

    def _selected_target(
        self, track: Track, targets: tuple[ExportTarget, ...]
    ) -> ExportTarget | None:
        return next((target for target in targets if target.key == self._target_key), None)

    def _choose_format(self, track: Track, fmt: ExportFormat) -> None:
        self._formats[track.track_id] = fmt
        self._render(busy=False)

    @staticmethod
    def _estimate(track: Track, fmt: ExportFormat, *, count: int = 1) -> str:
        minutes = max(0.0, track.duration_s or 0.0) / 60
        size = minutes * _MB_PER_MINUTE[fmt] * max(1, count)
        return i18n.t("export.estimate", size=f"{size:.1f}")

    def _batch_targets(self, track: Track) -> tuple[ExportTarget, ...]:
        return self._service.export_batch_targets(
            track,
            self._batch_version_key or "original",
        )

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
                    i18n.t(
                        "export.extension_changed",
                        suffix=fmt.suffix,
                        label=_format_label(fmt),
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
        self._run_in_sheet(lambda: self._on_job_started(job_id, spec))

    async def _confirm_save_original(self) -> None:
        track = self._track
        if track is None or self._saving:
            return
        imported = self._service.imported_file(track)
        if imported is None:
            return
        self._saving = True
        self._render(busy=True)
        selection = await self._save_dialog.save_audio_file(
            default_name=f"{imported.base_name}{imported.suffix}",
            suffix=imported.suffix,
            directory=self._default_directory(),
            native=True,
        )
        self._saving = False
        if selection is None:
            self._render(busy=False)
            return
        body = self._body
        if selection.corrected and body is not None:
            with body:
                toast(
                    i18n.t(
                        "page.save_original_extension_changed",
                        suffix=imported.suffix,
                    ),
                    type="info",
                )
        spec = SaveOriginalSpec(
            track_id=track.track_id,
            suffix=imported.suffix,
            destination_dir=selection.path.parent,
            destination_name=selection.path.name,
            corrected=selection.corrected,
        )
        try:
            job_id = self._service.submit_save_original(
                track,
                spec.destination_dir,
                destination_name=spec.destination_name,
            )
        except StemForgeError as error:
            self._submit_error = JobFailure.from_error(error)
            self._render(busy=False)
            return
        self._submit_error = None
        self.close()
        self._run_in_sheet(lambda: self._on_save_original_started(job_id, spec))

    async def _confirm_batch(self) -> None:
        track = self._track
        folder = self._batch_folder
        if track is None or folder is None or self._saving:
            return
        targets = self._batch_targets(track)
        fmt = self._formats.get(track.track_id, DEFAULT_FORMAT)
        if not targets:
            return
        keys = tuple(target.key for target in targets)
        spec = ExportBatchSpec(
            track_id=track.track_id,
            source_id=track.source_id,
            version_key=self._batch_version_key or "original",
            fmt=fmt,
            destination_dir=folder,
            target_keys=keys,
        )
        try:
            job_id = self._service.submit_export_batch(
                track,
                spec.version_key,
                fmt,
                folder,
                target_keys=keys,
            )
        except StemForgeError as error:
            self._submit_error = JobFailure.from_error(error)
            self._render(busy=False)
            return
        self._submit_error = None
        self.close()
        self._run_in_sheet(lambda: self._on_batch_started(job_id, spec))

    async def _choose_batch_folder(self) -> None:
        if self._saving:
            return
        self._saving = True
        self._render(busy=True)
        try:
            folder = await self._folder_dialog.choose_folder(
                directory=self._batch_folder or self._default_directory()
            )
        except StemForgeError as error:
            self._submit_error = JobFailure.from_error(error)
            self._saving = False
            self._render(busy=False)
            return
        self._saving = False
        if folder is not None:
            self._batch_folder = folder
            with contextlib.suppress(OSError):
                self._service.remember_export_dir(folder)
        self._render(busy=False)

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
        return str(preferred) if preferred is not None else i18n.t("export.destination_default")


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
                ui.label(i18n.t("page.exported_to", path=notice.path)).classes(
                    "sf-exportbar-title"
                ).mark("export-destination")
                if notice.spec.corrected:
                    ui.label(
                        i18n.t(
                            "export.extension_changed",
                            suffix=notice.spec.fmt.suffix,
                            label=_format_label(notice.spec.fmt),
                        )
                    ).classes("sf-exportbar-detail").mark("export-correction")
            (
                ui.button(
                    i18n.t("common.dismiss"), on_click=lambda _=None: on_dismiss(), color=None
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
                    ui.button(i18n.t("common.retry"), on_click=lambda _=None: on_retry())
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
                        i18n.t("export.choose_folder"),
                        on_click=lambda _=None: on_choose_folder(),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("export-choose-folder")
                )
                choose.set_enabled(not busy)
            (
                ui.button(
                    i18n.t("common.dismiss"), on_click=lambda _=None: on_dismiss(), color=None
                )
                .props("flat dense no-caps size=sm")
                .mark("export-dismiss")
            )


class SaveOriginalStatusBar:
    """The inline success/failure surface for a finished Save original Job."""

    def build(
        self,
        notice: SaveOriginalNotice,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_choose_location: Callable[[], object],
        on_dismiss: Callable[[], None],
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        if isinstance(notice, SaveOriginalSuccess):
            self._success(notice, on_dismiss=on_dismiss)
            return
        self._failure(
            notice,
            busy=busy,
            on_retry=on_retry,
            on_choose_location=on_choose_location,
            on_dismiss=on_dismiss,
            diagnostics=diagnostics,
            clipboard=clipboard,
            open_path=open_path,
        )

    def _success(
        self,
        notice: SaveOriginalSuccess,
        *,
        on_dismiss: Callable[[], None],
    ) -> None:
        with ui.element("div").classes("sf-exportbar is-success").mark("save-original-success"):
            ui.icon("check", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(i18n.t("page.save_original_saved_to", path=notice.path)).classes(
                    "sf-exportbar-title"
                ).mark("save-original-destination")
                if notice.spec.corrected:
                    ui.label(
                        i18n.t(
                            "page.save_original_extension_changed",
                            suffix=notice.spec.suffix,
                        )
                    ).classes("sf-exportbar-detail").mark("save-original-correction")
            (
                ui.button(
                    i18n.t("common.dismiss"), on_click=lambda _=None: on_dismiss(), color=None
                )
                .props("flat dense no-caps size=sm")
                .mark("save-original-dismiss")
            )

    def _failure(
        self,
        notice: SaveOriginalFailure,
        *,
        busy: bool,
        on_retry: Callable[[], None],
        on_choose_location: Callable[[], object],
        on_dismiss: Callable[[], None],
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
        with ui.element("div").classes("sf-exportbar is-failure").mark("save-original-failure"):
            ui.icon("error_outline", size="sm").classes("sf-exportbar-icon")
            with ui.element("div").classes("sf-exportbar-main"):
                ui.label(copy.headline).classes("sf-exportbar-title").mark("save-original-headline")
                ui.label(copy.action).classes("sf-exportbar-detail")
                error_details(
                    code=code,
                    marker="save-original-error",
                    job_id=failure.job_id,
                    log_path=failure.log_path,
                    diagnostics=diagnostics,
                    clipboard=clipboard,
                    open_path=open_path,
                )
            if failure.retryable:
                retry = (
                    ui.button(i18n.t("common.retry"), on_click=lambda _=None: on_retry())
                    .props("unelevated dense no-caps size=sm")
                    .mark("save-original-retry")
                )
                retry.set_enabled(not busy)
            if code in {errors.EXPORT_WRITE_FAILED, errors.STORAGE_DISK_FULL}:
                choose = (
                    ui.button(
                        i18n.t("export.choose_location"),
                        on_click=lambda _=None: on_choose_location(),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("save-original-choose-location")
                )
                choose.set_enabled(not busy)
            (
                ui.button(
                    i18n.t("common.dismiss"), on_click=lambda _=None: on_dismiss(), color=None
                )
                .props("flat dense no-caps size=sm")
                .mark("save-original-dismiss")
            )


class ExportBatchStatusBar:
    """The list-shaped results bar a finished batch leaves behind.

    The headline and every row come from the per-item record, never from the
    Job's state: a failed batch may have written files, and a cancelled one
    usually has (ADR 0015).
    """

    def build(
        self,
        notice: ExportBatchNotice,
        *,
        busy: bool,
        on_retry: Callable[[], object],
        on_remaining: Callable[[], object],
        on_choose_folder: Callable[[], object],
        on_dismiss: Callable[[], None],
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        result = notice.result
        written = len(result.written)
        failed = len(result.failed)
        not_exported = len(result.not_exported)
        total = len(result.items)
        complete = total > 0 and written == total
        if complete:
            classes = "sf-exportbar sf-exportbar-batch is-success"
        elif notice.cancelled:
            classes = "sf-exportbar sf-exportbar-batch is-cancelled"
        else:
            classes = "sf-exportbar sf-exportbar-batch is-failure"
        headline = formatting.export_batch_headline(
            written=written,
            failed=failed,
            not_exported=not_exported,
            cancelled=notice.cancelled,
            destination=result.destination_dir,
        )
        with ui.element("div").classes(classes).mark("export-batch-bar"):
            with ui.element("div").classes("sf-exportbar-head"):
                icon = "check" if complete else ("info" if notice.cancelled else "error_outline")
                ui.icon(icon, size="sm").classes("sf-exportbar-icon")
                with ui.element("div").classes("sf-exportbar-main"):
                    ui.label(headline).classes("sf-exportbar-title").mark("export-batch-headline")
                if notice.aborted:
                    choose = (
                        ui.button(
                            i18n.t("export.choose_folder"),
                            on_click=on_choose_folder,
                            color=None,
                        )
                        .props("flat dense no-caps size=sm")
                        .mark("export-batch-choose-folder")
                    )
                    choose.set_enabled(not busy)
                if not_exported:
                    remaining = (
                        ui.button(
                            formatting.export_batch_remaining(len(result.remaining_keys)),
                            on_click=on_remaining,
                            color=None,
                        )
                        .props("unelevated dense no-caps size=sm")
                        .mark("export-batch-remaining")
                    )
                    remaining.set_enabled(not busy)
                elif result.retry_keys:
                    retry = (
                        ui.button(i18n.t("common.retry"), on_click=on_retry)
                        .props("unelevated dense no-caps size=sm")
                        .mark("export-batch-retry")
                    )
                    retry.set_enabled(not busy)
                (
                    ui.button(i18n.t("common.dismiss"), on_click=on_dismiss, color=None)
                    .props("flat dense no-caps size=sm")
                    .mark("export-batch-dismiss")
                )
            with ui.element("div").classes("sf-exportbar-items"):
                for item in result.items:
                    self._item(item)
            if notice.failure is not None:
                failure = notice.failure
                error_details(
                    code=failure.code or errors.INTERNAL_UNEXPECTED,
                    marker="export-batch-error",
                    job_id=failure.job_id,
                    log_path=failure.log_path,
                    diagnostics=diagnostics,
                    clipboard=clipboard,
                    open_path=open_path,
                )

    def _item(self, item: ExportBatchItem) -> None:
        with (
            ui.element("div")
            .classes("sf-exportbar-item")
            .mark("export-batch-item", f"export-batch-item-{item.key}")
        ):
            if item.state is ExportBatchItemState.WRITTEN:
                ui.icon("check", size="xs").classes("sf-exportbar-item-icon is-good")
                detail = item.path.name if item.path is not None else ""
            elif item.state is ExportBatchItemState.FAILED:
                ui.icon("error_outline", size="xs").classes("sf-exportbar-item-icon is-bad")
                code = item.error_code or errors.INTERNAL_UNEXPECTED
                reason = (
                    "export"
                    if code == errors.STORAGE_DISK_FULL
                    else formatting.reason_from_detail(item.error_detail)
                )
                copy = formatting.copy_for(code, reason, context=item.error_context)
                detail = f"{copy.headline} {copy.action}"
            else:
                ui.icon("remove", size="xs").classes("sf-exportbar-item-icon")
                detail = i18n.t("export.not_exported")
            with ui.element("div").classes("sf-exportbar-item-main"):
                ui.label(formatting.label_text(item.label)).classes("sf-exportbar-item-label")
                ui.label(detail).classes("sf-exportbar-item-detail")
                if item.state is ExportBatchItemState.FAILED:
                    ui.label(item.error_code or errors.INTERNAL_UNEXPECTED).classes(
                        "sf-exportbar-code"
                    )
