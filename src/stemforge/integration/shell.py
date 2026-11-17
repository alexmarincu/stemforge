"""Native OS shell actions: open a folder in the platform file manager.

The About modal needs one OS action that is not a dialog: reveal the bundle's
``LICENSES/`` directory. Like :mod:`stemforge.integration.dialogs`, the real
implementation sits behind a tiny protocol so every unit and UI test injects a
scripted opener and never launches a file manager.

Resolution is Linux/Windows only (the v0.1.0 target matrix). A missing path is a
coded ``licensing.files_missing`` failure, not a traceback; a launcher that
cannot start is ``licensing.open_failed``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final, Protocol

from stemforge import errors
from stemforge.errors import StemForgeError

_DEVNULL: Final = subprocess.DEVNULL


class PathOpener(Protocol):
    """The OS file-manager seam; tests inject a scripted implementation."""

    def open_path(self, path: Path) -> None: ...


class SystemPathOpener:
    """Open a directory with ``xdg-open`` (Linux) or ``os.startfile`` (Windows)."""

    def open_path(self, path: Path) -> None:
        target = Path(path)
        if not target.exists():
            raise StemForgeError(
                errors.LICENSING_FILES_MISSING,
                "the licence tree is missing",
                detail="missing",
                context={"path": str(target)},
            )
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(target))  # type: ignore[attr-defined]
                return
            command = (
                ["open", str(target)] if sys.platform == "darwin" else ["xdg-open", str(target)]
            )
            subprocess.Popen(
                command,
                stdin=_DEVNULL,
                stdout=_DEVNULL,
                stderr=_DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise StemForgeError(
                errors.LICENSING_OPEN_FAILED,
                "the platform file manager could not be started",
                detail=type(exc).__name__,
                context={"path": str(target)},
            ) from exc
