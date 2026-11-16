"""Resolution of the bundled FFmpeg and ffprobe executables.

Order: ``<Bundle root>/vendor/ffmpeg/`` first (ADR 0010), then
``$STEMFORGE_FFMPEG`` / ``$STEMFORGE_FFPROBE``, then ``PATH``. Rubber Band, Deno,
and the worker command belong to the frozen-build ticket and are added there.

Foundation code: no ``stemforge`` imports, so both ``engines`` and ``platform``
consumers can use it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Final

FFMPEG_ENV: Final = "STEMFORGE_FFMPEG"
FFPROBE_ENV: Final = "STEMFORGE_FFPROBE"
VENDOR_DIRNAME: Final = "vendor"
FFMPEG_DIRNAME: Final = "ffmpeg"


def bundle_root() -> Path:
    """The directory that holds ``vendor/``: the repo root, or the Bundle root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def executable_name(name: str) -> str:
    """``name`` with the platform executable suffix."""
    return f"{name}.exe" if os.name == "nt" else name


def ffmpeg() -> Path:
    """Absolute path to the FFmpeg executable."""
    return _resolve("ffmpeg", FFMPEG_ENV)


def ffprobe() -> Path:
    """Absolute path to the ffprobe executable."""
    return _resolve("ffprobe", FFPROBE_ENV)


def _resolve(name: str, variable: str) -> Path:
    vendored = bundle_root() / VENDOR_DIRNAME / FFMPEG_DIRNAME / executable_name(name)
    if vendored.is_file():
        return vendored

    override = os.environ.get(variable, "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"{variable} does not point to a file: {candidate}")
        return candidate

    found = shutil.which(name)
    if found is None:
        raise FileNotFoundError(
            f"{name} is neither vendored under {VENDOR_DIRNAME}/{FFMPEG_DIRNAME} nor on PATH"
        )
    return Path(found)
