"""Pre-import environment setup.

``configure()`` resolves :class:`~stemforge.platform.paths.AppPaths`, creates the
roots (and the tool-cache subdirectories that environment variables point at),
and sets ``NICEGUI_STORAGE_PATH``, ``HF_HOME``, ``DENO_DIR``, and the temp
variables before NiceGUI or Demucs is imported (ticket 06 §6, ticket 07 §9).
"""

from __future__ import annotations

import os

from .paths import AppPaths

# Tool caches that live inside a root and must exist before the tool is used.
_CACHE_SUBDIRECTORIES = ("tmp", "deno")


def configure() -> AppPaths:
    """Create the app directories and set the pre-import environment variables."""
    paths = AppPaths.from_environment().ensure()

    os.environ.update(
        {
            "NICEGUI_STORAGE_PATH": str(paths.config / "nicegui"),
            "HF_HOME": str(paths.models),
            "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DENO_DIR": str(paths.cache / "deno"),
            "TMPDIR": str(paths.cache / "tmp"),
            "TEMP": str(paths.cache / "tmp"),
            "TMP": str(paths.cache / "tmp"),
        }
    )

    (paths.config / "nicegui").mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in _CACHE_SUBDIRECTORIES:
        (paths.cache / name).mkdir(mode=0o700, parents=True, exist_ok=True)

    return paths
