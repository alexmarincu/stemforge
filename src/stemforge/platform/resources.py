"""Bundled resources: the data root and the Bundle root (ticket 10 §3, ADR 0010).

Frozen builds have two roots:

* the **data root** is ``sys._MEIPASS`` — the onedir ``_internal`` directory that
  holds ``assets/``, ``models/models.lock.json``, and NiceGUI's package data;
* the **Bundle root** is ``Path(sys.executable).resolve().parent`` — the directory
  that holds ``vendor/**`` and the licence tree, beside the launcher.

In development the data root is ``src/stemforge`` (the package directory that
holds the same ``assets/`` and ``models/`` children PyInstaller collects into
``_internal``) and the Bundle root is the repository root. ``__file__`` traversal
is only ever used to find those development roots; frozen resolution never
touches the source tree.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

ASSETS_DIRNAME: Final = "assets"
MODELS_DIRNAME: Final = "models"
MODELS_LOCK_FILENAME: Final = "models.lock.json"
VENDOR_DIRNAME: Final = "vendor"
LICENSES_DIRNAME: Final = "LICENSES"
LICENSE_FILENAME: Final = "LICENSE"
NOTICES_FILENAME: Final = "THIRD_PARTY_NOTICES.md"
SOURCE_OFFER_FILENAME: Final = "SOURCE_OFFER.md"


def is_frozen() -> bool:
    """Whether this interpreter is a PyInstaller frozen build."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def _development_repo_root() -> Path:
    """The repository root in a checkout (``src/stemforge/platform/resources.py``)."""
    return Path(__file__).resolve().parents[3]


def data_root() -> Path:
    """The root for code data: ``assets/``, ``models/``, and NiceGUI package data."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """The root for ``vendor/`` and the licence tree, beside the launcher."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _development_repo_root()


def assets_dir() -> Path:
    """The read-only icon and image assets."""
    return data_root() / ASSETS_DIRNAME


def models_lock_path() -> Path:
    """The bundled model pin record (never the downloaded weights)."""
    return data_root() / MODELS_DIRNAME / MODELS_LOCK_FILENAME


def vendor_dir() -> Path:
    """The vendored FFmpeg, Rubber Band, and Deno binaries."""
    return bundle_root() / VENDOR_DIRNAME


def licenses_dir() -> Path:
    """The Bundle-root licence tree."""
    return bundle_root() / LICENSES_DIRNAME


def license_path() -> Path:
    """The project's GPL-3.0-only licence file at the Bundle root."""
    return bundle_root() / LICENSE_FILENAME


def notices_path() -> Path:
    """The generated third-party notices at the Bundle root."""
    return bundle_root() / NOTICES_FILENAME


def source_offer_path() -> Path:
    """The generated GPL source offer at the Bundle root."""
    return bundle_root() / SOURCE_OFFER_FILENAME
