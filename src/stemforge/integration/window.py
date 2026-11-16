"""Native window configuration: title, size floor, private mode, downloads.

All ``app.native.*`` writes live at module scope or in a function called before
``ui.run`` so the spawned window process receives picklable values (ticket 06
§7, research 01). The window has a native OS frame, a 1280x820 default, and a
1024x700 minimum; there is no close veto or close prompt (ticket 08 §12).
"""

from __future__ import annotations

from typing import Final

from nicegui import app

from stemforge.platform.paths import AppPaths

MIN_WINDOW_SIZE: Final = (1024, 700)
DEFAULT_WINDOW_SIZE: Final = (1280, 820)


def configure_window(paths: AppPaths) -> None:
    """Apply the one v0.1.0 window shape before NiceGUI spawns the window process."""
    app.native.window_args.update(
        {
            "resizable": True,
            "min_size": MIN_WINDOW_SIZE,
        }
    )
    app.native.start_args["private_mode"] = False
    app.native.start_args["storage_path"] = str(paths.webview)
    app.native.settings["ALLOW_DOWNLOADS"] = False
