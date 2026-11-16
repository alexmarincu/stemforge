"""Resolution of the bundled FFmpeg, ffprobe, Rubber Band, and Deno executables.

Vendored binaries live at the Bundle root (``<bundle>/vendor/**``), never inside
``_internal`` (ADR 0010). Resolution order is ``vendor/`` first, then an optional
per-binary environment override, then ``PATH`` in development. A frozen build
has no fallback: a missing binary raises :class:`BundleDamagedError`, whose
``code`` is the ticket 14 ``preflight.bundle_damaged`` exit-8 failure.

The environment overrides (``STEMFORGE_FFMPEG``, ``STEMFORGE_FFPROBE``,
``STEMFORGE_RUBBERBAND``, ``STEMFORGE_DENO``) point local diagnostics and tests
at a system binary without copying it into ``vendor/``.

Foundation code: no ``stemforge`` imports outside ``platform``, so both
``engines`` and ``platform`` consumers can use it.
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

FFMPEG_ENV: Final = "STEMFORGE_FFMPEG"
FFPROBE_ENV: Final = "STEMFORGE_FFPROBE"
RUBBERBAND_ENV: Final = "STEMFORGE_RUBBERBAND"
DENO_ENV: Final = "STEMFORGE_DENO"

# The ticket 4 spelling stays as an alias so both names keep working.
FFMPEG_ENV_VAR: Final = FFMPEG_ENV
FFPROBE_ENV_VAR: Final = FFPROBE_ENV
RUBBERBAND_ENV_VAR: Final = RUBBERBAND_ENV
DENO_ENV_VAR: Final = DENO_ENV

VENDOR_DIRNAME: Final = "vendor"
FFMPEG_DIRNAME: Final = "ffmpeg"


class BundleDamagedError(FileNotFoundError):
    """A required vendor binary is absent from a frozen Bundle."""

    code: str = PREFLIGHT_BUNDLE_DAMAGED

    def __init__(self, relative: Path, *, bundle_root: Path, detail: str | None = None) -> None:
        self.relative = relative
        self.bundle_root = bundle_root
        self.detail = detail
        message = f"{VENDOR_DIRNAME}/{relative.as_posix()} is missing from {bundle_root}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def bundle_root() -> Path:
    """The directory that holds ``vendor/``: the repo root, or the Bundle root."""
    return resources.bundle_root()


def executable_name(name: str) -> str:
    """``name`` with the platform executable suffix."""
    return f"{name}.exe" if sys.platform.startswith("win") else name


def _unavailable(relative: Path, detail: str | None = None) -> Path | None:
    """Return ``None`` in development; a frozen build cannot run without the binary."""
    if resources.is_frozen():
        raise BundleDamagedError(relative, bundle_root=bundle_root(), detail=detail)
    return None


def find_vendor(relative: str | Path, env_var: str | None = None) -> Path | None:
    """Resolve one vendored file relative to ``vendor/``.

    Order: ``vendor/`` in the Bundle root, an explicit environment override, then
    ``PATH`` in development. Returns ``None`` when a development checkout has no
    binary; raises :class:`BundleDamagedError` when a frozen Bundle has none.
    """
    relative_path = Path(relative)

    vendored = bundle_root() / VENDOR_DIRNAME / relative_path
    if vendored.is_file():
        return vendored

    if resources.is_frozen():
        raise BundleDamagedError(relative_path, bundle_root=bundle_root())

    if env_var is not None:
        override = os.environ.get(env_var, "").strip()
        if override:
            candidate = Path(override).expanduser()
            if candidate.is_file():
                return candidate
            raise FileNotFoundError(f"{env_var} does not point to a file: {candidate}")

    found = shutil.which(relative_path.name)
    return Path(found) if found is not None else None


def _required(name: str, env_var: str) -> Path:
    resolved = find_vendor(Path(FFMPEG_DIRNAME) / executable_name(name), env_var)
    if resolved is None:
        raise FileNotFoundError(
            f"{name} is neither vendored under {VENDOR_DIRNAME}/{FFMPEG_DIRNAME} nor on PATH"
        )
    return resolved


def ffmpeg() -> Path:
    """Absolute path to the FFmpeg executable."""
    return _required("ffmpeg", FFMPEG_ENV)


def ffprobe() -> Path:
    """Absolute path to the ffprobe executable."""
    return _required("ffprobe", FFPROBE_ENV)


def rubberband() -> Path | None:
    """The vendored Rubber Band CLI, if present."""
    return find_vendor(Path("rubberband") / executable_name("rubberband"), RUBBERBAND_ENV)


def deno() -> Path | None:
    """The vendored Deno executable, if present."""
    return find_vendor(Path("deno") / executable_name("deno"), DENO_ENV)


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
