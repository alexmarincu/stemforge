"""The root page: a placeholder until ticket 8 builds the workspace UI.

Importing this module registers ``/`` with NiceGUI, which is all the frozen
``--smoke-test`` needs. Ticket 8 replaces the body with the real composition and
poll timer; this module must never import engines, workers, pipeline, or jobs.
"""

from __future__ import annotations

from nicegui import ui

TITLE = "StemForge"


@ui.page("/")
def index() -> None:
    """Render the root page."""
    ui.page_title(TITLE)
    with ui.column().classes("absolute-center items-center gap-2"):
        ui.label(TITLE).classes("text-3xl font-bold")
        ui.label("Local stem separation, pitch shifting, and export.").classes("text-base")
