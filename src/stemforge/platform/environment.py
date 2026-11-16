"""Pre-import environment setup.

``configure()`` will compute :class:`~stemforge.platform.paths.AppPaths`, create the
directories, and set ``NICEGUI_STORAGE_PATH`` and ``HF_HOME`` before NiceGUI or
Demucs is imported (ticket 06 §6, ticket 10).
"""

from __future__ import annotations

from .paths import AppPaths


def configure() -> AppPaths:
    """Create the app directories and set the pre-import environment variables."""
    raise NotImplementedError("platform.environment.configure lands in ticket 7")
