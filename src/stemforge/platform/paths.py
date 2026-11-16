"""Application data directories.

Every runtime and test write goes through this module. The exact per-OS strings
are fixed by ticket 07 §1: platform-idiomatic per-user roots, or one
``STEMFORGE_HOME`` root for portable and CI use. Construction never touches the
filesystem; only :meth:`AppPaths.ensure` creates directories.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

import platformdirs

HOME_ENV_VAR: Final = "STEMFORGE_HOME"

# Linux follows the lowercase XDG convention; Windows uses the display casing.
UNIX_APP_NAME: Final = "stemforge"
WINDOWS_APP_NAME: Final = "StemForge"


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
    def from_roots(cls, *, data: Path, config: Path, cache: Path, logs: Path) -> Self:
        """Build the path set from the four per-OS roots; models/webview live under data."""
        data = Path(data)
        return cls(
            data=data,
            cache=Path(cache),
            logs=Path(logs),
            models=data / "models",
            config=Path(config),
            webview=data / "webview",
        )

    @classmethod
    def for_home(cls, home: Path) -> Self:
        """Build the ``STEMFORGE_HOME`` layout rooted at one portable directory."""
        home = Path(home).expanduser().absolute()
        return cls.from_roots(
            data=home / "data",
            config=home / "config",
            cache=home / "cache",
            logs=home / "logs",
        )

    @classmethod
    def from_environment(cls, *, platform: str | None = None) -> Self:
        """Resolve the per-user roots, honouring ``STEMFORGE_HOME``."""
        override = os.environ.get(HOME_ENV_VAR)
        if override:
            return cls.for_home(Path(override))

        system = sys.platform if platform is None else platform
        app_name = WINDOWS_APP_NAME if system.startswith("win") else UNIX_APP_NAME
        return cls.from_roots(
            data=Path(platformdirs.user_data_dir(app_name, appauthor=False)),
            config=Path(platformdirs.user_config_dir(app_name, appauthor=False)),
            cache=Path(platformdirs.user_cache_dir(app_name, appauthor=False)),
            logs=Path(platformdirs.user_log_dir(app_name, appauthor=False)),
        )

    @property
    def roots(self) -> tuple[Path, Path, Path, Path, Path, Path]:
        """Every root, in creation order."""
        return (self.data, self.cache, self.logs, self.models, self.config, self.webview)

    def ensure(self) -> AppPaths:
        """Create every root with private permissions and return the resolved set.

        Symlinks are resolved *after* creation so the ``/media`` route compares
        real paths (ticket 07 §1).
        """
        for directory in self.roots:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return AppPaths.from_roots(
            data=self.data.resolve(),
            config=self.config.resolve(),
            cache=self.cache.resolve(),
            logs=self.logs.resolve(),
        )
