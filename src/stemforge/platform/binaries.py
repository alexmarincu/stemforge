"""Resolution of ffmpeg, ffprobe, Rubber Band, Deno, and the worker command.

Vendored binaries live at the Bundle root (``<bundle>/vendor/**``), never inside
``_internal`` (ADR 0010). Development prefers ``vendor/`` in the checkout, then
falls back to ``PATH``. A frozen build has no fallback: a missing binary raises
:class:`BundleDamagedError`, whose ``code`` is the ticket 14
``preflight.bundle_damaged`` exit-8 failure.

Optional per-binary environment overrides (``STEMFORGE_FFMPEG``,
``STEMFORGE_FFPROBE``, ``STEMFORGE_RUBBERBAND``, ``STEMFORGE_DENO``) point local
diagnostics and tests at a system binary without copying it into ``vendor/``.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from . import resources

# Ticket 06 §1 keeps `platform` stdlib-only, so the ticket 14 taxonomy member is
# repeated here rather than imported from `stemforge.errors` (the import
# contracts put `stemforge.errors` on the same foundation layer).
PREFLIGHT_BUNDLE_DAMAGED: Final = "preflight.bundle_damaged"

FFMPEG_ENV_VAR: Final = "STEMFORGE_FFMPEG"
FFPROBE_ENV_VAR: Final = "STEMFORGE_FFPROBE"
RUBBERBAND_ENV_VAR: Final = "STEMFORGE_RUBBERBAND"
DENO_ENV_VAR: Final = "STEMFORGE_DENO"


class BundleDamagedError(FileNotFoundError):
    """A required vendor binary is absent from a frozen Bundle."""

    code: str = PREFLIGHT_BUNDLE_DAMAGED

    def __init__(self, relative: Path, *, bundle_root: Path, detail: str | None = None) -> None:
        self.relative = relative
        self.bundle_root = bundle_root
        self.detail = detail
        message = f"{resources.VENDOR_DIRNAME}/{relative.as_posix()} is missing from {bundle_root}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def _executable_name(name: str) -> str:
    return f"{name}.exe" if sys.platform.startswith("win") else name


def _unavailable(relative: Path, detail: str | None = None) -> Path | None:
    """Return ``None`` in development; a frozen build cannot run without the binary."""
    if resources.is_frozen():
        raise BundleDamagedError(relative, bundle_root=resources.bundle_root(), detail=detail)
    return None


def find_vendor(relative: str | Path, env_var: str | None = None) -> Path | None:
    """Resolve one vendored file relative to ``vendor/``.

    Order: an explicit environment override, the Bundle root, then ``PATH`` in
    development. Returns ``None`` when a development checkout has no binary;
    raises :class:`BundleDamagedError` when a frozen Bundle has none.
    """
    relative_path = Path(relative)

    if env_var is not None:
        override = os.environ.get(env_var)
        if override:
            candidate = Path(override).expanduser()
            if candidate.is_file():
                return candidate
            return _unavailable(relative_path, f"{env_var}={override} is not a file")

    vendored = resources.vendor_dir() / relative_path
    if vendored.is_file():
        return vendored

    if resources.is_frozen():
        raise BundleDamagedError(relative_path, bundle_root=resources.bundle_root())

    found = shutil.which(relative_path.name)
    return Path(found) if found is not None else None


def ffmpeg() -> Path | None:
    """The vendored FFmpeg executable, if present."""
    return find_vendor(Path("ffmpeg") / _executable_name("ffmpeg"), FFMPEG_ENV_VAR)


def ffprobe() -> Path | None:
    """The vendored ffprobe executable, if present."""
    return find_vendor(Path("ffmpeg") / _executable_name("ffprobe"), FFPROBE_ENV_VAR)


def rubberband() -> Path | None:
    """The vendored Rubber Band CLI, if present."""
    return find_vendor(Path("rubberband") / _executable_name("rubberband"), RUBBERBAND_ENV_VAR)


def deno() -> Path | None:
    """The vendored Deno executable, if present."""
    return find_vendor(Path("deno") / _executable_name("deno"), DENO_ENV_VAR)


def worker_command(argv: Sequence[str]) -> list[str]:
    """Build the Separation worker command from ``argv`` whose first item is the name.

    Frozen builds re-invoke the same executable with ``--worker <name>``; a
    development checkout runs ``python -m stemforge.workers.<name>`` (ticket 06 §4).
    """
    if not argv:
        raise ValueError("worker_command needs a worker name as argv[0]")
    name, *rest = argv
    if resources.is_frozen():
        return [sys.executable, "--worker", name, *rest]
    return [sys.executable, "-m", f"stemforge.workers.{name}", *rest]
