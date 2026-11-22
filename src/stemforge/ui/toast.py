"""The one toast surface: eight seconds and dismissible.

NiceGUI's ``ui.notify`` defaults to Quasar's five-second toast with no close
control. This wrapper fixes the lifetime at eight seconds and makes every toast
dismissible, so components call it instead of ``ui.notify``.
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
