"""The visual root: the prototype palette, Quasar colours, and brand assets.

Quasar ships a blue ``primary`` (``#1976D2``); StemForge must never show it. The
page applies this palette once per client before building the shell, so every
default-coloured control — buttons, sliders, progress bars, switches, focus
rings — inherits the amber accent from ticket 08's prototype.

The token block is the only place the shell declares colour literals. Every
rule in :data:`stemforge.ui.page.SHELL_CSS` consumes a token, which keeps the
prototype's palette and the rendered app from drifting apart.

The mark is ticket 17's *Badge*: an amber disc with four ink bars. Assets are
embedded as data URLs so the rail and About modal do not depend on a static
route being registered by a particular entry point.
"""

from __future__ import annotations

import base64
import mimetypes
from functools import lru_cache
from typing import Final

from nicegui import ui

from stemforge.platform import resources

# prototypes/08-ui-layout.html:26-35
BG: Final = "#0e1014"
RAIL: Final = "#11141a"
PANEL: Final = "#151920"
PANEL2: Final = "#1b2029"
LINE: Final = "#242b36"
LINE2: Final = "#313b49"
LINE_HOVER: Final = "#55647a"
TEXT: Final = "#e6e9ef"
MUTED: Final = "#98a2b3"
DIM: Final = "#6c7686"
ACCENT: Final = "#f2a65a"
ACCENT_SOFT: Final = "rgba(242, 166, 90, 0.12)"
ACCENT_LINE: Final = "rgba(242, 166, 90, 0.4)"
ACCENT_DEEP: Final = "#3a2c14"
GOOD: Final = "#5ec99a"
GOOD_SOFT: Final = "rgba(94, 201, 154, 0.12)"
GOOD_LINE: Final = "#2f4636"
BAD: Final = "#ef7070"
BAD_TEXT: Final = "#f3a5a5"
BAD_ACTION: Final = "#c9a6a6"
BAD_DIM: Final = "#a97f7f"
BAD_SOFT: Final = "rgba(239, 112, 112, 0.1)"
BAD_BG: Final = "#241617"
BAD_LINE: Final = "#5b2c2c"
# ticket 17 §Resolved sub-questions: the bars are ink on the amber disc.
INK: Final = "#1d1204"

# App-specific surfaces derived from the same palette.
WELL: Final = "#12161d"
WARN: Final = "#e4d3a9"
WARN_BG: Final = "#1d1a12"
WARN_LINE: Final = "#40341d"
RADIUS: Final = "12px"
MONO: Final = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

LOGO_SVG: Final = resources.APP_LOGO_SVG
ABOUT_PNG: Final = resources.APP_ICON_PNG

# Quasar's semantic names, plus ``ink`` for text and icons on an amber field.
QUASAR_COLORS: Final[dict[str, str]] = {
    "primary": ACCENT,
    "secondary": MUTED,
    "accent": ACCENT,
    "dark": BG,
    "dark_page": BG,
    "positive": GOOD,
    "negative": BAD,
    "info": MUTED,
    "warning": WARN,
    "ink": INK,
}


def apply() -> ui.colors:
    """Install the palette on the current page before any control renders."""
    return ui.colors(
        primary=QUASAR_COLORS["primary"],
        secondary=QUASAR_COLORS["secondary"],
        accent=QUASAR_COLORS["accent"],
        dark=QUASAR_COLORS["dark"],
        dark_page=QUASAR_COLORS["dark_page"],
        positive=QUASAR_COLORS["positive"],
        negative=QUASAR_COLORS["negative"],
        info=QUASAR_COLORS["info"],
        warning=QUASAR_COLORS["warning"],
        ink=QUASAR_COLORS["ink"],
    )


TOKENS_CSS: Final = f""":root {{
  --bg: {BG};
  --rail: {RAIL};
  --panel: {PANEL};
  --panel2: {PANEL2};
  --line: {LINE};
  --line2: {LINE2};
  --line-hover: {LINE_HOVER};
  --text: {TEXT};
  --muted: {MUTED};
  --dim: {DIM};
  --accent: {ACCENT};
  --accent-soft: {ACCENT_SOFT};
  --accent-line: {ACCENT_LINE};
  --accent-deep: {ACCENT_DEEP};
  --good: {GOOD};
  --good-soft: {GOOD_SOFT};
  --good-line: {GOOD_LINE};
  --bad: {BAD};
  --bad-text: {BAD_TEXT};
  --bad-action: {BAD_ACTION};
  --bad-dim: {BAD_DIM};
  --bad-soft: {BAD_SOFT};
  --bad-bg: {BAD_BG};
  --bad-line: {BAD_LINE};
  --warn: {WARN};
  --warn-bg: {WARN_BG};
  --warn-line: {WARN_LINE};
  --ink: {INK};
  --well: {WELL};
  --radius: {RADIUS};
  --mono: {MONO};
}}
"""


@lru_cache(maxsize=8)
def asset_data_url(name: str) -> str | None:
    """Embed one bundled asset, or ``None`` when the file is unavailable."""
    path = resources.assets_dir() / name
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_html(name: str, *, size: int, css_class: str) -> str | None:
    """A plain ``<img>`` mark; Quasar's QImg collapses inside the shell grid."""
    src = asset_data_url(name)
    if src is None:
        return None
    return (
        f'<img src="{src}" alt="StemForge" width="{size}" height="{size}"'
        f' class="{css_class}" style="display:block">'
    )
