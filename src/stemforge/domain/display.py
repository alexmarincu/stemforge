"""Stable display-label codes shared by the application, pipeline, and UI.

A view carries a catalog message ID and a context instead of only a frozen
English label. The UI resolves the message at render time; the legacy ``label``
string stays as the expand-phase fallback. Filename role labels live separately
in ``pipeline.export_names`` and never use these codes.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "DISPLAY_CODES",
    "EXPORT_SHIFTED_LABEL_KEY",
    "ORIGINAL_LABEL_KEY",
    "RENDER_LABEL_KEY",
    "RENDER_MASTER_LABEL_KEY",
    "RENDER_STEMS_LABEL_KEY",
    "SEPARATION_LABEL_KEY",
    "render_label_key",
    "role_label_key",
]

ORIGINAL_LABEL_KEY: Final = "common.original"
RENDER_LABEL_KEY: Final = "pitch.render.shift"
RENDER_MASTER_LABEL_KEY: Final = "pitch.render.master"
RENDER_STEMS_LABEL_KEY: Final = "pitch.render.stems"
EXPORT_SHIFTED_LABEL_KEY: Final = "export.target.shifted"
SEPARATION_LABEL_KEY: Final = "separation.title"
_ROLE_PREFIX: Final = "role."


def role_label_key(role: str) -> str:
    """The display code for one role; ``original`` is the shared Original label."""
    return ORIGINAL_LABEL_KEY if role == "original" else f"{_ROLE_PREFIX}{role}"


def render_label_key(*, scope: str, collision: bool) -> str:
    """The display code for a Render tab: bare, master, or stems."""
    if not collision:
        return RENDER_LABEL_KEY
    return RENDER_MASTER_LABEL_KEY if scope == "original" else RENDER_STEMS_LABEL_KEY


DISPLAY_CODES: Final[frozenset[str]] = frozenset(
    {
        ORIGINAL_LABEL_KEY,
        RENDER_LABEL_KEY,
        RENDER_MASTER_LABEL_KEY,
        RENDER_STEMS_LABEL_KEY,
        EXPORT_SHIFTED_LABEL_KEY,
        SEPARATION_LABEL_KEY,
    }
)
