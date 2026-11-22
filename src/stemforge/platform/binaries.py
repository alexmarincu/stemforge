"""Resolution of the bundled FFmpeg, ffprobe, Rubber Band, and Deno executables.

Vendored binaries live at the Bundle root (``<bundle>/vendor/**``), never inside
``_internal`` (ADR 0010). Resolution order is ``vendor/`` first, then an optional
per-binary environment override, then ``PATH`` in development. A frozen build
has no fallback: a missing binary raises :class:`BundleDamagedError`, whose
``code`` is the ``preflight.bundle_damaged`` exit-8 failure.

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
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from . import resources

# `platform` stays stdlib-only, so the taxonomy member is repeated here rather
# than imported from `stemforge.errors` (the import contracts put
# `stemforge.errors` on the same foundation layer).
PREFLIGHT_BUNDLE_DAMAGED: Final = "preflight.bundle_damaged"

FFMPEG_ENV: Final = "STEMFORGE_FFMPEG"
FFPROBE_ENV: Final = "STEMFORGE_FFPROBE"
RUBBERBAND_ENV: Final = "STEMFORGE_RUBBERBAND"
DENO_ENV: Final = "STEMFORGE_DENO"

# The ``*_ENV_VAR`` spelling stays as an alias so both names keep working.
FFMPEG_ENV_VAR: Final = FFMPEG_ENV
FFPROBE_ENV_VAR: Final = FFPROBE_ENV
RUBBERBAND_ENV_VAR: Final = RUBBERBAND_ENV
DENO_ENV_VAR: Final = DENO_ENV

VENDOR_DIRNAME: Final = "vendor"

# The vendor tree, without the platform executable suffix.
VENDOR_PATHS: Final = {
    "ffmpeg": Path("ffmpeg") / "ffmpeg",
    "ffprobe": Path("ffmpeg") / "ffprobe",
    "rubberband": Path("rubberband") / "rubberband",
    "deno": Path("deno") / "deno",
}

# Rubber Band links libsndfile dynamically; the loader has to find it beside the
# CLI (ADR 0010). FFmpeg and Deno are static builds.
SHARED_LIBRARIES: Final = {
    "rubberband": {"linux": "libsndfile.so.1", "win32": "sndfile.dll"},
}


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


def _is_windows(system: str | None) -> bool:
    return (sys.platform if system is None else system).startswith("win")


def vendor_path(tool: str, *, system: str | None = None) -> Path:
    """The ``vendor/``-relative path assigned to ``tool``.

    ``ffmpeg`` and ``ffprobe`` share ``ffmpeg/``; Rubber Band and Deno get their
    own directory. Windows adds ``.exe``; the vendor tree contains no suffix-less
    tool on Windows (the preflight checker enforces the same rule).
    """
    try:
        relative = VENDOR_PATHS[tool]
    except KeyError as error:
        raise ValueError(f"unknown vendor tool: {tool}") from error
    if _is_windows(system):
        return relative.with_name(f"{relative.name}.exe")
    return relative


def shared_library_name(tool: str, *, system: str | None = None) -> str | None:
    """The co-located shared library ``tool`` needs, if any."""
    libraries = SHARED_LIBRARIES.get(tool)
    if libraries is None:
        return None
    return libraries["win32" if _is_windows(system) else "linux"]


def shared_libraries(tool: str) -> tuple[Path, ...]:
    """Resolve every shared library that must sit beside ``tool``.

    Development may have none (a system binary uses system libraries); a frozen
    Bundle must have every one, because a missing library makes the CLI fail at
    exec time with no useful error (exit-8 contract).
    """
    name = shared_library_name(tool)
    if name is None:
        return ()
    relative = Path(tool) / name
    path = bundle_root() / VENDOR_DIRNAME / relative
    if path.is_file():
        return (path,)
    if resources.is_frozen():
        raise BundleDamagedError(relative, bundle_root=bundle_root())
    return ()


def tool_environment(
    executable: Path,
    *,
    base: Mapping[str, str] | None = None,
    system: str | None = None,
) -> dict[str, str]:
    """A child process environment that finds co-located shared libraries.

    Linux uses ``LD_LIBRARY_PATH``; the build also sets an ``$ORIGIN`` rpath, so
    this is belt-and-braces for a rebuilt or replaced CLI. Windows' loader
    already searches the executable's own directory.
    """
    environment = dict(os.environ if base is None else base)
    if _is_windows(system):
        return environment
    library_dir = str(executable.parent)
    existing = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = f"{library_dir}:{existing}" if existing else library_dir
    return environment


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


def _required(tool: str, env_var: str) -> Path:
    resolved = find_vendor(vendor_path(tool), env_var)
    if resolved is None:
        raise FileNotFoundError(f"{tool} is neither vendored under {VENDOR_DIRNAME}/ nor on PATH")
    return resolved


def ffmpeg() -> Path:
    """Absolute path to the FFmpeg executable."""
    return _required("ffmpeg", FFMPEG_ENV)


def ffprobe() -> Path:
    """Absolute path to the ffprobe executable."""
    return _required("ffprobe", FFPROBE_ENV)


def rubberband() -> Path | None:
    """The vendored Rubber Band CLI, if present.

    A frozen Bundle also requires the co-located ``libsndfile.so.1`` /
    ``sndfile.dll``; the CLI is unusable without it.
    """
    resolved = find_vendor(vendor_path("rubberband"), RUBBERBAND_ENV)
    if resolved is not None and resolved.parent == bundle_root() / VENDOR_DIRNAME / "rubberband":
        shared_libraries("rubberband")
    return resolved


def deno() -> Path | None:
    """The vendored Deno executable, if present."""
    return find_vendor(vendor_path("deno"), DENO_ENV)


def worker_command(argv: Sequence[str]) -> list[str]:
    """Build the Separation worker command from ``argv`` whose first item is the name.

    Frozen builds re-invoke the same executable with ``--worker <name>``; a
    development checkout runs ``python -m stemforge.workers.<name>``.
    """
    if not argv:
        raise ValueError("worker_command needs a worker name as argv[0]")
    name, *rest = argv
    if resources.is_frozen():
        return [sys.executable, "--worker", name, *rest]
    return [sys.executable, "-m", f"stemforge.workers.{name}", *rest]
