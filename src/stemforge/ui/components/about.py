"""The Settings & About modal's Behaviour section (ticket 08 §12, ticket 09 §7).

Ticket 22 owns the full About content (licences, notices, source offer). This
module is the merge point for settings: for now it carries the persisted
YouTube import toggle and nothing else, so disabling hides only the empty-state
YouTube row.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from stemforge.application import WorkspaceService
from stemforge.ui import formatting


class SettingsDialog:
    """One modal per open; reads and writes the service, never storage directly."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_youtube_toggle: Callable[[], None] | None = None,
    ) -> None:
        self._service = service
        self._on_youtube_toggle = on_youtube_toggle

    def open(self) -> None:
        with (
            ui.dialog() as dialog,
            ui.card().classes("sf-card sf-settings").mark("settings-dialog"),
        ):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(formatting.SETTINGS_TITLE).classes("sf-title")
                (
                    ui.button(icon="close", on_click=dialog.close)
                    .props("flat dense round size=sm")
                    .mark("settings-dismiss")
                )
            ui.label(formatting.SETTINGS_BEHAVIOUR).classes("sf-card-title")
            with ui.element("div").classes("sf-setting-row"):
                with ui.element("div").classes("sf-setting-main"):
                    ui.label(formatting.YOUTUBE_SETTING_LABEL).classes("sf-setting-label")
                    ui.label(formatting.YOUTUBE_SETTING_HINT).classes("sf-fineprint")
                toggle = (
                    ui.switch(value=self._service.youtube_enabled())
                    .mark("youtube-toggle")
                    .props("dense")
                )
                toggle.on_value_change(self._apply_youtube_toggle)
            with ui.row().classes("w-full justify-end"):
                (
                    ui.button(formatting.SETTINGS_CLOSE, on_click=dialog.close)
                    .props("flat no-caps")
                    .mark("settings-close")
                )
        dialog.open()

    def _apply_youtube_toggle(self, event: ValueChangeEventArguments[bool | None]) -> None:
        self._service.set_youtube_enabled(bool(event.value))
        if self._on_youtube_toggle is not None:
            self._on_youtube_toggle()
