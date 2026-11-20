"""Copy-only diagnostics and the shared Job/error Details disclosure.

Ticket 14 §9/§10: every coded failure exposes ``Details`` with the code, the
short Job id, the Job log path, ``Open log``, and ``Copy diagnostics``. The
report is shown in a read-only textarea and copied with the browser clipboard,
with a ``document.execCommand('copy')`` fallback. Nothing is uploaded, zipped,
or written here; the text stays selectable when both copy paths fail.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from nicegui import ui

from stemforge import errors
from stemforge.errors import StemForgeError
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting

DiagnosticsProvider = Callable[[str | None], str]

BROWSER_COPY_JS = """
(async (text) => {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (error) {
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand('copy');
      document.body.removeChild(area);
      return copied;
    } catch (fallbackError) {
      return false;
    }
  }
})(%s)
"""


class ClipboardWriter(Protocol):
    """The clipboard seam; tests inject a recorder, never a browser call."""

    async def write_text(self, text: str) -> bool: ...


class BrowserClipboard:
    """Copy through ``navigator.clipboard``, then ``execCommand('copy')``."""

    async def write_text(self, text: str) -> bool:
        script = BROWSER_COPY_JS % json.dumps(text)
        try:
            result = await ui.run_javascript(script, timeout=5.0)
        except Exception:
            return False
        return result is True


def open_diagnostics_modal(
    text: str,
    clipboard: ClipboardWriter | None = None,
    *,
    title: str = formatting.DIAGNOSTICS_TITLE,
) -> None:
    """Show the report in a read-only textarea and offer a copy button."""
    writer = clipboard if clipboard is not None else BrowserClipboard()
    with (
        ui.dialog() as dialog,
        ui.card().classes("sf-card sf-diagnostics").mark("diagnostics-dialog"),
    ):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(title).classes("sf-title")
            ui.button(icon="close", on_click=dialog.close, color=None).props(
                "flat dense round size=sm"
            ).mark("diagnostics-dismiss")
        ui.label(formatting.DIAGNOSTICS_NOTICE).classes("sf-fineprint").mark("diagnostics-notice")
        ui.textarea(value=text).props("readonly outlined autogrow").classes(
            "sf-diagnostics-text"
        ).mark("diagnostics-text")
        status = ui.label("").classes("sf-fineprint").mark("diagnostics-status")

        async def copy() -> None:
            copied = await writer.write_text(text)
            status.set_text(
                formatting.DIAGNOSTICS_COPIED if copied else formatting.DIAGNOSTICS_COPY_FAILED
            )

        with ui.row().classes("w-full justify-end gap-2"):
            (
                ui.button(formatting.DIAGNOSTICS_COPY, on_click=copy)
                .props("unelevated no-caps")
                .mark("diagnostics-copy")
            )
            (
                ui.button(formatting.DIAGNOSTICS_CLOSE, on_click=dialog.close, color=None)
                .props("flat no-caps")
                .mark("diagnostics-close")
            )
    dialog.open()


def error_details(
    *,
    code: str,
    job_id: str | None = None,
    log_path: Path | None = None,
    diagnostics: DiagnosticsProvider | None = None,
    clipboard: ClipboardWriter | None = None,
    open_path: PathOpener | None = None,
    label: str = formatting.DETAILS_LABEL,
    marker: str = "error-details",
) -> None:
    """Render the ticket 14 §9 Details disclosure for one coded failure."""
    with ui.expansion(label).classes("sf-details").mark(marker):
        line = f"{code} · job {job_id[:8]}" if job_id else code
        ui.label(line).classes("sf-notice-detail").mark(f"{marker}-code")
        if log_path is not None:
            ui.label(str(log_path)).classes("sf-notice-detail").mark(f"{marker}-log")
        with ui.row().classes("sf-details-actions"):
            if log_path is not None and open_path is not None:
                (
                    ui.button(
                        formatting.OPEN_LOG_LABEL,
                        on_click=lambda: _open_log(open_path, log_path),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("open-log")
                )
            if diagnostics is not None:
                (
                    ui.button(
                        formatting.COPY_DIAGNOSTICS_LABEL,
                        on_click=lambda: open_diagnostics_modal(diagnostics(job_id), clipboard),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("copy-diagnostics")
                )


def _open_log(open_path: PathOpener, log_path: Path) -> None:
    if not log_path.is_file():
        copy = formatting.copy_for(errors.LICENSING_OPEN_FAILED, "log")
        ui.notify(copy.headline, type="negative")
        return
    try:
        open_path.open_path(log_path)
    except StemForgeError as error:
        copy = formatting.copy_for(error.code, "log")
        ui.notify(copy.headline, type="negative")
    except OSError:
        copy = formatting.copy_for(errors.LICENSING_OPEN_FAILED, "log")
        ui.notify(copy.headline, type="negative")
