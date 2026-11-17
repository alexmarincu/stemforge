"""The empty-state YouTube row and its one-time consent modal (ticket 15).

The row validates synchronously with the pure parser before the application seam
is called, so a playlist, channel, search, or foreign host is only a field error.
Ticket 16 wires the seam to the ``import_youtube`` Job: a valid submit reports in
the one Job bar, and the page refreshes the row when the Job ends.

All prose lives in ``ui/formatting.py``. The only state this component owns is
view state (the error line, the pending paste, and the declined flag); the
acknowledgment and the enable/disable setting are persisted by ``SettingsStore``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    error_details,
)

if TYPE_CHECKING:
    from stemforge.application import WorkspaceService


class YouTubeRow:
    """The URL field, Import action, field error, and consent gate."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        busy: bool = False,
        on_submitted: Callable[[str], None] | None = None,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._busy = busy
        self._on_submitted = on_submitted
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._open_path = open_path
        self._declined = False
        self._pending_url: str | None = None
        self._dialog: ui.dialog | None = None
        self._input: ui.input | None = None
        self._button: ui.button | None = None
        self._error: ui.label | None = None
        self._error_details: ui.column | None = None
        self._status: ui.label | None = None

    def build(self) -> None:
        if not self._service.youtube_enabled():
            return
        with ui.element("div").classes("sf-yt").mark("youtube-row"):
            with ui.element("div").classes("sf-yt-row"):
                self._input = (
                    ui.input(placeholder="https://www.youtube.com/watch?v=…")
                    .props("dense outlined")
                    .classes("sf-yt-input")
                    .mark("youtube-url")
                )
                self._input.on("keydown.enter", lambda _=None: self.handle_submit())
                self._button = (
                    ui.button(formatting.YOUTUBE_IMPORT_LABEL, on_click=self.handle_submit)
                    .props("unelevated no-caps")
                    .classes("sf-yt-button")
                    .mark("youtube-import")
                )
            self._error = ui.label("").classes("sf-yt-error").mark("youtube-field-error")
            self._error.set_visibility(False)
            self._error_details = ui.column().classes("w-full").mark("youtube-error-details")
            self._error_details.set_visibility(False)
            self._status = ui.label("").classes("sf-yt-status").mark("youtube-status")
            self._status.set_visibility(False)
            ui.label(formatting.YOUTUBE_DISCLAIMER).classes("sf-yt-disclaimer").mark(
                "youtube-disclaimer"
            )
        if self._busy:
            self.set_busy(True)

    def set_busy(self, busy: bool) -> None:
        """Disable the controls while any Job runs (ticket 08)."""
        self._busy = busy
        if self._declined:
            return
        if self._input is not None:
            self._input.set_enabled(not busy)
        if self._button is not None:
            self._button.set_enabled(not busy)

    def handle_submit(self) -> None:
        """Validate at submit, then either ask for consent or call the seam."""
        if self._declined or self._input is None:
            return
        raw = (self._input.value or "").strip()
        try:
            self._service.validate_youtube(raw)
        except StemForgeError as error:
            self._show_field_error(error)
            return
        except Exception:  # pragma: no cover - defensive; no raw trace in the UI
            self._show_field_error(
                StemForgeError(errors.INTERNAL_UNEXPECTED, "youtube validation failed")
            )
            return
        self._clear_field_error()
        if not self._service.youtube_notice_acknowledged():
            self._pending_url = raw
            self._open_consent()
            return
        self._submit(raw)

    def _submit(self, raw: str) -> None:
        try:
            job_id = self._service.submit_youtube(raw)
        except StemForgeError as error:
            self._show_field_error(error)
            return
        except Exception:  # pragma: no cover - defensive; no raw trace in the UI
            self._show_field_error(
                StemForgeError(errors.INTERNAL_UNEXPECTED, "youtube import seam failed")
            )
            return
        if self._status is not None:
            self._status.set_visibility(False)
        if self._on_submitted is not None:
            self._on_submitted(job_id)

    def _open_consent(self) -> None:
        if self._dialog is not None:
            return
        dialog = ui.dialog()
        self._dialog = dialog
        with dialog, ui.card().classes("sf-card sf-consent").mark("youtube-consent"):
            ui.label(formatting.YOUTUBE_CONSENT_TITLE).classes("sf-title")
            ui.label(formatting.YOUTUBE_CONSENT_BODY).classes("sf-lede")
            with ui.row().classes("w-full justify-end gap-2"):
                (
                    ui.button(formatting.YOUTUBE_CONSENT_CANCEL, on_click=self._decline)
                    .props("flat no-caps")
                    .mark("youtube-consent-cancel")
                )
                (
                    ui.button(formatting.YOUTUBE_CONSENT_ACCEPT, on_click=self._accept)
                    .props("unelevated no-caps")
                    .mark("youtube-consent-accept")
                )
        dialog.open()

    def _accept(self) -> None:
        self._service.accept_youtube_notice()
        self._close_dialog()
        pending = self._pending_url
        self._pending_url = None
        if pending is not None:
            self._submit(pending)

    def _decline(self) -> None:
        self._pending_url = None
        self._close_dialog()
        self._declined = True
        if self._input is not None:
            self._input.set_enabled(False)
        if self._button is not None:
            self._button.set_enabled(False)
        if self._status is not None:
            self._status.set_text(formatting.YOUTUBE_DECLINED)
            self._status.set_visibility(True)

    def _close_dialog(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.delete()
            self._dialog = None

    def _show_field_error(self, error: StemForgeError) -> None:
        copy = formatting.copy_for(error.code, formatting.reason_from_detail(error.detail))
        if self._error is not None:
            self._error.set_text(copy.headline)
            self._error.set_visibility(True)
        if self._error_details is not None:
            self._error_details.clear()
            with self._error_details:
                error_details(
                    code=error.code,
                    marker="youtube-error",
                    diagnostics=self._diagnostics,
                    clipboard=self._clipboard,
                    open_path=self._open_path,
                )
            self._error_details.set_visibility(self._diagnostics is not None)
        if self._status is not None:
            self._status.set_visibility(False)

    def _clear_field_error(self) -> None:
        if self._error is not None:
            self._error.set_visibility(False)
        if self._error_details is not None:
            self._error_details.set_visibility(False)
