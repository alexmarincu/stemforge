"""The Settings & About modal (ticket 08 §12, ticket 09 §7, ticket 22).

Three sections, in the order ticket 08 fixes:

* **App** — version, GPL-3.0-only, the bundled notices and source offer, and the
  ``LICENSES/`` folder. Paths resolve from the Bundle root through
  :meth:`WorkspaceService.licence_files`, never from the current working
  directory. Documents open in a scrollable plain-text dialog; the folder opens
  through an injected OS seam, so no test launches a file manager.
* **Storage** — the 8 GiB cache meter, Clear cached audio (live tracks stay),
  Clear recent files, and the YouTube tool cache.
* **Behaviour** — the persisted YouTube import toggle.

A missing licence tree is a coded ``licensing.files_missing`` message, not a
traceback; a file manager that cannot start is ``licensing.open_failed``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from stemforge import __version__, errors
from stemforge.application import LicenceFiles, WorkspaceService
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting


class SettingsDialog:
    """One modal per open; reads and writes the service, never storage directly."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_youtube_toggle: Callable[[], None] | None = None,
        open_path: PathOpener | None = None,
    ) -> None:
        self._service = service
        self._on_youtube_toggle = on_youtube_toggle
        self._open_path = open_path
        self._cache_size: ui.label | None = None
        self._recent_size: ui.label | None = None
        self._youtube_cache_size: ui.label | None = None
        self._licence_error: ui.label | None = None

    def open(self) -> None:
        files = self._service.licence_files()
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
            self._app_section(files)
            self._storage_section()
            self._behaviour_section()
            with ui.row().classes("w-full justify-end"):
                (
                    ui.button(formatting.SETTINGS_CLOSE, on_click=dialog.close)
                    .props("flat no-caps")
                    .mark("settings-close")
                )
        dialog.open()

    # -- App ---------------------------------------------------------------

    def _app_section(self, files: LicenceFiles) -> None:
        ui.label(formatting.SETTINGS_APP).classes("sf-card-title")
        self._row(
            formatting.SETTINGS_VERSION_LABEL,
            f"StemForge {__version__}",
            marker="about-version",
        )
        self._document_row(
            formatting.SETTINGS_LICENCE_LABEL,
            "GPL-3.0-only",
            files.license,
            title="StemForge licence",
            marker="about-licence",
            button_marker="about-view-licence",
        )
        self._document_row(
            formatting.SETTINGS_NOTICES_LABEL,
            "THIRD_PARTY_NOTICES.md",
            files.notices,
            title="Third-party notices",
            marker="about-notices",
            button_marker="about-view-notices",
        )
        self._document_row(
            formatting.SETTINGS_SOURCE_LABEL,
            "SOURCE_OFFER.md",
            files.source_offer,
            title="Corresponding source",
            marker="about-source-offer",
            button_marker="about-view-source",
        )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(formatting.SETTINGS_LICENCES_LABEL).classes("sf-setting-label")
                ui.label(str(files.licenses_dir)).classes("sf-fineprint")
            (
                ui.button(
                    formatting.SETTINGS_OPEN_LICENCES,
                    on_click=self._open_licences,
                )
                .props("flat no-caps")
                .mark("about-open-licences")
            )
        ui.label(formatting.SETTINGS_WEIGHTS_NOTE).classes("sf-fineprint").mark(
            "about-weights-note"
        )
        self._licence_error = (
            ui.label("").classes("sf-fineprint sf-field-error").mark("about-files-error")
        )
        self._licence_error.visible = False
        if files.missing:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}")

    def _row(self, label: str, value: str, *, marker: str) -> None:
        with (
            ui.element("div").classes("sf-setting-row"),
            ui.element("div").classes("sf-setting-main"),
        ):
            ui.label(label).classes("sf-setting-label")
            ui.label(value).classes("sf-fineprint").mark(marker)

    def _document_row(
        self,
        label: str,
        value: str,
        path: Path,
        *,
        title: str,
        marker: str,
        button_marker: str,
    ) -> None:
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(label).classes("sf-setting-label")
                ui.label(value).classes("sf-fineprint").mark(marker)
            (
                ui.button(
                    formatting.SETTINGS_VIEW,
                    on_click=lambda: self._show_document(title, path),
                )
                .props("flat no-caps")
                .mark(button_marker)
            )

    def _show_document(self, title: str, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}")
            return
        with (
            ui.dialog() as dialog,
            ui.card().classes("sf-card sf-settings about-document").mark("about-document"),
        ):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(title).classes("sf-title")
                ui.button(icon="close", on_click=dialog.close).props(
                    "flat dense round size=sm"
                ).mark("about-document-dismiss")
            with ui.element("pre").classes("sf-licence-text").mark("about-document-text"):
                ui.label(text).classes("whitespace-pre-wrap")
            with ui.row().classes("w-full justify-end"):
                (
                    ui.button(formatting.SETTINGS_CLOSE, on_click=dialog.close)
                    .props("flat no-caps")
                    .mark("about-document-close")
                )
        dialog.open()

    def _open_licences(self) -> None:
        files = self._service.licence_files()
        if not files.complete:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}")
            return
        opener = self._open_path
        if opener is None:
            # Production always supplies SystemPathOpener. A missing seam is a
            # coded failure rather than an accidental OS launch in a test.
            copy = formatting.copy_for(errors.LICENSING_OPEN_FAILED)
            self._show_error(f"{copy.headline} {copy.action}")
            return
        try:
            opener.open_path(files.licenses_dir)
        except StemForgeError as error:
            copy = formatting.copy_for(error.code)
            self._show_error(f"{copy.headline} {copy.action}")
        except OSError:
            copy = formatting.copy_for(errors.LICENSING_OPEN_FAILED)
            self._show_error(f"{copy.headline} {copy.action}")

    def _show_error(self, message: str) -> None:
        if self._licence_error is None:
            return
        self._licence_error.set_text(message)
        self._licence_error.visible = True

    # -- Storage -----------------------------------------------------------

    def _storage_section(self) -> None:
        ui.label(formatting.SETTINGS_STORAGE).classes("sf-card-title")
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(formatting.SETTINGS_CACHE_LABEL).classes("sf-setting-label")
                self._cache_size = (
                    ui.label(self._size_text()).classes("sf-fineprint").mark("about-cache-size")
                )
            (
                ui.button(formatting.SETTINGS_CACHE_CLEAR, on_click=self._clear_cache)
                .props("flat no-caps")
                .mark("clear-cached-audio")
            )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(formatting.SETTINGS_RECENT_CLEAR).classes("sf-setting-label")
                self._recent_size = (
                    ui.label(self._recent_text()).classes("sf-fineprint").mark("about-recent-size")
                )
            (
                ui.button(formatting.SETTINGS_RECENT_CLEAR, on_click=self._clear_recent)
                .props("flat no-caps")
                .mark("clear-recent")
            )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(formatting.YOUTUBE_CACHE_LABEL).classes("sf-setting-label")
                ui.label(formatting.YOUTUBE_CACHE_HINT).classes("sf-fineprint")
            with ui.row().classes("items-center gap-3"):
                self._youtube_cache_size = (
                    ui.label(self._youtube_size_text())
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

    def _clear_cache(self) -> None:
        report = self._service.clear_cache()
        self._refresh_storage()
        ui.notify(
            formatting.SETTINGS_CACHE_CLEARED.format(
                size=formatting.format_bytes(report.freed_bytes)
            ),
            type="positive",
        )

    def _clear_recent(self) -> None:
        self._service.clear_recent()
        self._refresh_storage()
        ui.notify(formatting.SETTINGS_RECENT_CLEARED, type="positive")

    def _clear_youtube_cache(self) -> None:
        freed = self._service.clear_youtube_cache()
        if self._youtube_cache_size is not None:
            self._youtube_cache_size.set_text(self._youtube_size_text())
        ui.notify(
            f"YouTube cache cleared ({formatting.format_bytes(freed)}).",
            type="positive",
        )

    def _refresh_storage(self) -> None:
        if self._cache_size is not None:
            self._cache_size.set_text(self._size_text())
        if self._recent_size is not None:
            self._recent_size.set_text(self._recent_text())

    def _size_text(self) -> str:
        return (
            f"{formatting.format_bytes(self._service.artifacts.usage_bytes())} / "
            f"{formatting.format_bytes(self._service.artifacts.cap_bytes)}"
        )

    def _recent_text(self) -> str:
        count = len(self._service.recent_files())
        return f"{count} file" if count == 1 else f"{count} files"

    def _youtube_size_text(self) -> str:
        return formatting.format_bytes(self._service.youtube_cache_bytes())

    # -- Behaviour ---------------------------------------------------------

    def _behaviour_section(self) -> None:
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

    def _apply_youtube_toggle(self, event: ValueChangeEventArguments[bool | None]) -> None:
        self._service.set_youtube_enabled(bool(event.value))
        if self._on_youtube_toggle is not None:
            self._on_youtube_toggle()
