"""The Settings & About modal.

Three sections, in this order:

* **App** — version, GPL-3.0-only, the bundled notices and source offer, and the
  ``LICENSES/`` folder. Paths resolve from the Bundle root through
  :meth:`WorkspaceService.licence_files`, never from the current working
  directory. Documents open in a scrollable plain-text dialog; the folder opens
  through an injected OS seam, so no test launches a file manager.
* **Storage** — the 8 GiB cache meter, Clear cached audio (live tracks stay),
  Clear recent files, the YouTube tool cache, and Open log folder.
* **Behaviour** — the persisted YouTube import toggle, the close/shutdown
  contract, and the force-quit export-partial note.

A missing licence tree is a coded ``licensing.files_missing`` message, not a
traceback; a file manager that cannot start is ``licensing.open_failed``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nicegui import ui
from nicegui.events import ValueChangeEventArguments

from stemforge import __version__, errors, i18n
from stemforge.application import LicenceFiles, WorkspaceService
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting, theme
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    open_diagnostics_modal,
)
from stemforge.ui.toast import toast


class SettingsDialog:
    """One modal per open; reads and writes the service, never storage directly."""

    def __init__(
        self,
        service: WorkspaceService,
        *,
        on_youtube_toggle: Callable[[], None] | None = None,
        open_path: PathOpener | None = None,
        diagnostics: DiagnosticsProvider | None = None,
        clipboard: ClipboardWriter | None = None,
    ) -> None:
        self._service = service
        self._on_youtube_toggle = on_youtube_toggle
        self._open_path = open_path
        self._diagnostics = diagnostics
        self._clipboard = clipboard
        self._cache_size: ui.label | None = None
        self._recent_size: ui.label | None = None
        self._youtube_cache_size: ui.label | None = None
        self._clear_cache_button: ui.button | None = None
        self._clear_youtube_button: ui.button | None = None
        self._licence_error: ui.label | None = None
        self._licence_error_code: ui.label | None = None

    def open(self) -> None:
        files = self._service.licence_files()
        with (
            ui.dialog() as dialog,
            ui.card().classes("sf-card sf-settings").mark("settings-dialog"),
        ):
            with ui.row().classes("w-full items-center justify-between"):
                with ui.row().classes("items-center gap-3"):
                    mark = theme.image_html(theme.ABOUT_PNG, size=40, css_class="sf-settings-mark")
                    if mark is not None:
                        ui.html(mark, sanitize=False).mark("about-app-mark")
                    ui.label(i18n.t("settings.title")).classes("sf-title")
                (
                    ui.button(icon="close", on_click=dialog.close, color=None)
                    .props("flat dense round size=sm")
                    .mark("settings-dismiss")
                )
            self._app_section(files)
            self._storage_section()
            self._behaviour_section()
            self._sync_job_actions()
            timer = ui.timer(0.4, self._sync_job_actions)
            dialog.on_value_change(
                lambda event: timer.activate() if event.value else timer.deactivate()
            )
            with ui.row().classes("w-full justify-end"):
                (
                    ui.button(i18n.t("common.close"), on_click=dialog.close, color=None)
                    .props("flat no-caps")
                    .mark("settings-close")
                )
        dialog.open()

    # -- App ---------------------------------------------------------------

    def _app_section(self, files: LicenceFiles) -> None:
        ui.label(i18n.t("settings.app")).classes("sf-card-title")
        self._row(
            i18n.t("settings.version_label"),
            i18n.t("settings.version_value", app=i18n.t("app.name"), version=__version__),
            marker="about-version",
        )
        versions = self._service.youtube_tool_versions()
        ui.label(i18n.t("settings.bundled_tools")).classes("sf-fineprint")
        self._row(
            i18n.t("settings.ytdlp"),
            versions.get("yt_dlp", i18n.t("settings.unknown_version")),
            marker="about-ytdlp-version",
        )
        self._row(
            i18n.t("settings.ytdlp_ejs"),
            versions.get("yt_dlp_ejs", i18n.t("settings.unknown_version")),
            marker="about-ytdlp-ejs-version",
        )
        self._row(
            i18n.t("settings.deno"),
            versions.get("deno", i18n.t("settings.unknown_version")),
            marker="about-deno-version",
        )
        self._document_row(
            i18n.t("settings.licence"),
            "GPL-3.0-only",
            files.license,
            title=i18n.t("settings.licence_document"),
            marker="about-licence",
            button_marker="about-view-licence",
        )
        self._document_row(
            i18n.t("settings.notices"),
            "THIRD_PARTY_NOTICES.md",
            files.notices,
            title=i18n.t("settings.notices"),
            marker="about-notices",
            button_marker="about-view-notices",
        )
        self._document_row(
            i18n.t("settings.source"),
            "SOURCE_OFFER.md",
            files.source_offer,
            title=i18n.t("settings.source"),
            marker="about-source-offer",
            button_marker="about-view-source",
        )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.licence_files")).classes("sf-setting-label")
                ui.label(str(files.licenses_dir)).classes("sf-fineprint")
            (
                ui.button(
                    i18n.t("settings.open_licences"), on_click=self._open_licences, color=None
                )
                .props("flat no-caps")
                .mark("about-open-licences")
            )
        ui.label(i18n.t("settings.weights_note")).classes("sf-fineprint").mark("about-weights-note")
        self._licence_error = (
            ui.label("").classes("sf-fineprint sf-field-error").mark("about-files-error")
        )
        self._licence_error.visible = False
        self._licence_error_code = (
            ui.label("").classes("sf-notice-detail").mark("about-files-error-code")
        )
        self._licence_error_code.visible = False
        if files.missing:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}", code=errors.LICENSING_FILES_MISSING)

    def _copy_diagnostics(self) -> None:
        if self._diagnostics is None:
            return
        open_diagnostics_modal(self._diagnostics(None), self._clipboard)

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
                    i18n.t("settings.view"),
                    on_click=lambda: self._show_document(title, path),
                    color=None,
                )
                .props("flat no-caps")
                .mark(button_marker)
            )

    def _show_document(self, title: str, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}", code=errors.LICENSING_FILES_MISSING)
            return
        with (
            ui.dialog() as dialog,
            ui.card().classes("sf-card sf-settings about-document").mark("about-document"),
        ):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(title).classes("sf-title")
                ui.button(icon="close", on_click=dialog.close, color=None).props(
                    "flat dense round size=sm"
                ).mark("about-document-dismiss")
            with ui.element("pre").classes("sf-licence-text").mark("about-document-text"):
                ui.label(text).classes("whitespace-pre-wrap")
            with ui.row().classes("w-full justify-end"):
                (
                    ui.button(i18n.t("common.close"), on_click=dialog.close, color=None)
                    .props("flat no-caps")
                    .mark("about-document-close")
                )
        dialog.open()

    def _open_licences(self) -> None:
        files = self._service.licence_files()
        if not files.complete:
            copy = formatting.copy_for(errors.LICENSING_FILES_MISSING)
            self._show_error(f"{copy.headline} {copy.action}", code=errors.LICENSING_FILES_MISSING)
            return
        self._open_folder(files.licenses_dir, failed_code=errors.LICENSING_OPEN_FAILED)

    def _open_logs(self) -> None:
        self._open_folder(self._service.logs_dir(), failed_code=errors.LOGGING_OPEN_FAILED)

    def _open_folder(self, path: Path, *, failed_code: str) -> None:
        opener = self._open_path
        if opener is None:
            # Production always supplies SystemPathOpener. A missing seam is a
            # coded failure rather than an accidental OS launch in a test.
            copy = formatting.copy_for(failed_code)
            self._show_error(f"{copy.headline} {copy.action}", code=failed_code)
            return
        try:
            opener.open_path(path)
        except (OSError, StemForgeError):
            copy = formatting.copy_for(failed_code)
            self._show_error(f"{copy.headline} {copy.action}", code=failed_code)

    def _show_error(self, message: str, *, code: str) -> None:
        if self._licence_error is None:
            return
        self._licence_error.set_text(message)
        self._licence_error.visible = True
        if self._licence_error_code is not None:
            self._licence_error_code.set_text(code)
            self._licence_error_code.visible = True

    # -- Storage -----------------------------------------------------------

    def _storage_section(self) -> None:
        ui.label(i18n.t("settings.storage")).classes("sf-card-title")
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.cache_label")).classes("sf-setting-label")
                self._cache_size = (
                    ui.label(self._size_text()).classes("sf-fineprint").mark("about-cache-size")
                )
            self._clear_cache_button = (
                ui.button(i18n.t("settings.cache.clear"), on_click=self._clear_cache, color=None)
                .props("flat no-caps")
                .mark("clear-cached-audio")
            )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.recent_clear")).classes("sf-setting-label")
                self._recent_size = (
                    ui.label(self._recent_text()).classes("sf-fineprint").mark("about-recent-size")
                )
            (
                ui.button(i18n.t("settings.recent_clear"), on_click=self._clear_recent, color=None)
                .props("flat no-caps")
                .mark("clear-recent")
            )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.youtube_cache_label")).classes("sf-setting-label")
                ui.label(i18n.t("settings.youtube_cache_hint")).classes("sf-fineprint")
            with ui.row().classes("items-center gap-3"):
                self._youtube_cache_size = (
                    ui.label(self._youtube_size_text())
                    .classes("sf-fineprint")
                    .mark("youtube-cache-size")
                )
                self._clear_youtube_button = (
                    ui.button(
                        i18n.t("settings.youtube_cache_clear"),
                        on_click=self._clear_youtube_cache,
                        color=None,
                    )
                    .props("flat no-caps")
                    .mark("clear-youtube-cache")
                )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.log_label")).classes("sf-setting-label")
                ui.label(i18n.t("settings.log_hint")).classes("sf-fineprint")
            (
                ui.button(i18n.t("settings.open_log_folder"), on_click=self._open_logs, color=None)
                .props("flat no-caps")
                .mark("open-log-folder")
            )
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("diagnostics.copy_action")).classes("sf-setting-label")
                ui.label(i18n.t("diagnostics.notice")).classes("sf-fineprint")
            button = (
                ui.button(
                    i18n.t("diagnostics.copy_action"), on_click=self._copy_diagnostics, color=None
                )
                .props("flat no-caps")
                .mark("about-copy-diagnostics")
            )
            button.set_enabled(self._diagnostics is not None)

    def _any_job_active(self) -> bool:
        return any(snapshot.active for snapshot in self._service.jobs())

    def _sync_job_actions(self) -> None:
        """The two cache clears are disabled while any Job runs."""
        busy = self._any_job_active()
        for button in (self._clear_cache_button, self._clear_youtube_button):
            if button is not None:
                button.set_enabled(not busy)

    def _clear_cache(self) -> None:
        if self._any_job_active():
            return
        report = self._service.clear_cache()
        self._refresh_storage()
        toast(
            i18n.t("page.cache_cleared", size=formatting.format_bytes(report.freed_bytes)),
            type="positive",
        )

    def _clear_recent(self) -> None:
        self._service.clear_recent()
        self._refresh_storage()
        toast(i18n.t("settings.recent_cleared"), type="positive")

    def _clear_youtube_cache(self) -> None:
        if self._any_job_active():
            return
        freed = self._service.clear_youtube_cache()
        if self._youtube_cache_size is not None:
            self._youtube_cache_size.set_text(self._youtube_size_text())
        toast(
            i18n.t("settings.youtube_cache_cleared", size=formatting.format_bytes(freed)),
            type="positive",
        )

    def _refresh_storage(self) -> None:
        if self._cache_size is not None:
            self._cache_size.set_text(self._size_text())
        if self._recent_size is not None:
            self._recent_size.set_text(self._recent_text())

    def _size_text(self) -> str:
        return i18n.t(
            "page.cache_usage",
            used=formatting.format_bytes(self._service.cache_usage_bytes()),
            cap=formatting.format_bytes(self._service.cache_cap_bytes()),
        )

    def _recent_text(self) -> str:
        return formatting.file_count_label(len(self._service.recent_files()))

    def _youtube_size_text(self) -> str:
        return formatting.format_bytes(self._service.youtube_cache_bytes())

    # -- Behaviour ---------------------------------------------------------

    def _behaviour_section(self) -> None:
        ui.label(i18n.t("settings.behaviour")).classes("sf-card-title")
        with ui.element("div").classes("sf-setting-row"):
            with ui.element("div").classes("sf-setting-main"):
                ui.label(i18n.t("settings.youtube_label")).classes("sf-setting-label")
                ui.label(i18n.t("settings.youtube_hint")).classes("sf-fineprint")
            toggle = (
                ui.switch(value=self._service.youtube_enabled())
                .mark("youtube-toggle")
                .props("dense")
            )
            toggle.on_value_change(self._apply_youtube_toggle)
        ui.label(i18n.t("settings.youtube_note")).classes("sf-fineprint").mark("about-youtube-note")
        ui.label(i18n.t("settings.close_note")).classes("sf-fineprint").mark("about-close-note")
        ui.label(i18n.t("settings.partial_note")).classes("sf-fineprint").mark("about-partial-note")

    def _apply_youtube_toggle(self, event: ValueChangeEventArguments[bool | None]) -> None:
        self._service.set_youtube_enabled(bool(event.value))
        if self._on_youtube_toggle is not None:
            self._on_youtube_toggle()
