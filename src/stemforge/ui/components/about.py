"""The Settings & About modal's Behaviour and Storage sections (ticket 08 §12, 09 §7).

Ticket 22 owns the full About content (licences, notices, source offer). This
module is the merge point for settings: the persisted YouTube import toggle and
the "Clear YouTube cache" action, which deletes only ``<cache>/yt-dlp/``.
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
        self._cache_size: ui.label | None = None

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
            ui.label(formatting.SETTINGS_STORAGE).classes("sf-card-title")
            with ui.element("div").classes("sf-setting-row"):
                with ui.element("div").classes("sf-setting-main"):
                    ui.label(formatting.YOUTUBE_CACHE_LABEL).classes("sf-setting-label")
                    ui.label(formatting.YOUTUBE_CACHE_HINT).classes("sf-fineprint")
                with ui.row().classes("items-center gap-3"):
                    self._cache_size = (
                        ui.label(self._size_text())
                        .classes("sf-fineprint")
                        .mark("youtube-cache-size")
                    )
                    (
                        ui.button(
                            formatting.YOUTUBE_CACHE_CLEAR,
                            on_click=self._clear_youtube_cache,
                        )
                        .props("flat no-caps")
                        .mark("clear-youtube-cache")
                    )
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

    def _clear_youtube_cache(self) -> None:
        freed = self._service.clear_youtube_cache()
        if self._cache_size is not None:
            self._cache_size.set_text(self._size_text())
        ui.notify(
            f"YouTube cache cleared ({formatting.format_bytes(freed)}).",
            type="positive",
        )

    def _size_text(self) -> str:
        return formatting.format_bytes(self._service.youtube_cache_bytes())
