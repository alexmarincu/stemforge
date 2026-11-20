"""The one toast surface: eight seconds and dismissible (ticket 14 §2).

NiceGUI's ``ui.notify`` defaults to Quasar's five-second toast with no close
control. Ticket 14 fixes the lifetime at eight seconds and requires every toast
to be dismissible, so components call this wrapper instead of ``ui.notify``.
"""

from __future__ import annotations

from typing import Final, Literal

from nicegui import ui

ToastType = Literal["positive", "negative", "warning", "info", "ongoing"]

TOAST_TIMEOUT_MS: Final = 8_000


def toast(message: str, *, type: ToastType | None = None) -> None:
    """Show one dismissible eight-second notification."""
    ui.notify(
        message,
        type=type,
        timeout=TOAST_TIMEOUT_MS,
        close_button=True,
    )
