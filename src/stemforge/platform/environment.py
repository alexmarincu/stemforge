"""Pre-import environment setup.

``configure()`` resolves :class:`~stemforge.platform.paths.AppPaths`, creates the
roots (and the tool-cache subdirectories that environment variables point at),
and sets ``NICEGUI_STORAGE_PATH``, ``HF_HOME``, ``DENO_DIR``, and the temp
variables before NiceGUI or Demucs is imported.
"""

from __future__ import annotations

import os
import sys

from .paths import AppPaths

# Tool caches that live inside a root and must exist before the tool is used.
_CACHE_SUBDIRECTORIES = ("tmp", "deno", "yt-dlp")


def _restore_host_gstreamer() -> None:
    """Drop PyInstaller's bundle-only GStreamer paths (ADR 0011).

    PyInstaller's ``pyi_rth_gstreamer`` runtime hook assumes the plugins were
    copied into the bundle. StemForge deliberately leaves GStreamer on the host,
    so its settings would otherwise hide the system plugins and registry.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return
    root = os.path.abspath(bundle)

    def inside_bundle(value: str) -> bool:
        path = os.path.abspath(value)
        return path == root or path.startswith(root + os.sep)

    plugin_path = os.environ.get("GST_PLUGIN_PATH")
    if plugin_path and any(inside_bundle(part) for part in plugin_path.split(os.pathsep) if part):
        os.environ.pop("GST_PLUGIN_PATH", None)
    if os.environ.get("GST_PLUGIN_SYSTEM_PATH") == "":
        os.environ.pop("GST_PLUGIN_SYSTEM_PATH", None)
    registry = os.environ.get("GST_REGISTRY")
    if registry and inside_bundle(registry):
        os.environ.pop("GST_REGISTRY", None)


def configure() -> AppPaths:
    """Create the app directories and set the pre-import environment variables."""
    _restore_host_gstreamer()
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
