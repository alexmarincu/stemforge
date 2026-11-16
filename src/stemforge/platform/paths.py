"""Application data directories.

Every runtime and test read goes through this module. The exact per-OS strings are
provisional until ticket 07 fixes them, so that change stays local.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import platformdirs

HOME_ENV_VAR = "STEMFORGE_HOME"
APP_NAME = "StemForge"


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Every directory StemForge may write to."""

    data: Path
    cache: Path
    logs: Path
    models: Path
    config: Path
    webview: Path

    @classmethod
    def under(cls, root: Path) -> Self:
        """Build the path set rooted at one directory."""
        root = Path(root)
        return cls(
            data=root,
            cache=root / "cache",
            logs=root / "logs",
            models=root / "models",
            config=root / "config",
            webview=root / "webview",
        )

    @classmethod
    def from_environment(cls) -> Self:
        """Resolve the app directory, honouring ``STEMFORGE_HOME``."""
        override = os.environ.get(HOME_ENV_VAR)
        if override:
            return cls.under(Path(override))
        return cls.under(Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)))

    def create(self) -> None:
        """Create every directory that does not exist yet."""
        for directory in (
            self.data,
            self.cache,
            self.logs,
            self.models,
            self.config,
            self.webview,
        ):
            directory.mkdir(parents=True, exist_ok=True)
