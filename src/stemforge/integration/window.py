"""Native window configuration: title, size floor, private mode, downloads.

All ``app.native.*`` writes live at module scope or in a function called before
``ui.run`` so the spawned window process receives picklable values. The window
has a native OS frame, a 1280x820 default, and a 1024x700 minimum; there is no
close veto or close prompt.
"""

from __future__ import annotations

import os
import sys
from typing import Final

from nicegui import app

from stemforge.platform import resources
from stemforge.platform.paths import AppPaths

MIN_WINDOW_SIZE: Final = (1024, 700)
DEFAULT_WINDOW_SIZE: Final = (1280, 820)
WINDOWS_BACKEND: Final = "edgechromium"
# WebKitGTK 2.52+ can fail to allocate a GBM buffer on NVIDIA/X11 and paint the
# whole web view white while the GTK chrome renders. The shared-memory DMABUF
# renderer avoids that path; `setdefault` keeps an explicit user override.
WEBKIT_SHM_ENV: Final = "WEBKIT_DMABUF_RENDERER_FORCE_SHM"


def configure_window(paths: AppPaths) -> None:
    """Apply the one window shape before NiceGUI spawns the window process."""
    if sys.platform.startswith("linux"):
        os.environ.setdefault(WEBKIT_SHM_ENV, "1")
    window_args: dict[str, object] = {
        "resizable": True,
        "min_size": MIN_WINDOW_SIZE,
    }
    # pywebview 6 takes the window icon on ``webview.start()``, not
    # ``create_window()``; GTK/QT use the raster mark, Windows the ICO.
    icon = resources.app_icon()
    if icon.is_file():
        app.native.start_args["icon"] = str(icon)
    app.native.window_args.update(window_args)
    app.native.start_args["private_mode"] = False
    app.native.start_args["storage_path"] = str(paths.webview)
    # Windows has exactly one supported web engine. The startup
    # preflight has already exited 5 when WebView2 is absent, so this is the
    # explicit assertion rather than pywebview's silent legacy fallback.
    if sys.platform.startswith("win"):
        app.native.start_args["gui"] = WINDOWS_BACKEND
    app.native.settings["ALLOW_DOWNLOADS"] = False
