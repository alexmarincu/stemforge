"""Bundled assets for the UI, behind the integration seam (ticket 06 §1, §7).

The UI may not import :mod:`stemforge.platform`; this module is the one place
that resolves the read-only icon and image files for presentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from stemforge.platform import resources

__all__ = ["ABOUT_PNG", "LOGO_SVG", "asset_path"]

LOGO_SVG: Final = resources.APP_LOGO_SVG
ABOUT_PNG: Final = resources.APP_ICON_PNG


def asset_path(name: str) -> Path:
    """Resolve one filename under the bundled ``assets/`` directory."""
    return resources.assets_dir() / name
