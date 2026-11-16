"""Native open-dialog wrapper: strict filters, trusted paths, extension repair.

pywebview's dialog API has no NiceGUI wrapper. This module owns the one open
dialog StemForge needs in v1:

* the filter string follows pywebview's strict grammar (description
  ``[\\w ]+``, patterns ``*.ext`` joined by ``;`` with no spaces);
* only absolute native paths are trusted; browser artefacts such as
  ``C:\\fakepath\\…`` and ``file://`` URLs are never imported;
* extensions are matched case-insensitively and with surrounding whitespace
  ignored, and anything outside the v1 audio set is a coded selection error.

Threading follows ticket 01: ``Window.create_file_dialog`` already hops through
NiceGUI's ``run.io_bound`` and the native window executor, so the wrapper only
awaits it. It must not call ``run_io`` itself, or the dialog would be queued to
a worker twice.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from stemforge import errors
from stemforge.errors import StemForgeError

AUDIO_EXTENSIONS: Final = (".wav", ".mp3", ".flac", ".m4a")
AUDIO_FILTER: Final = "Audio (*.wav;*.mp3;*.flac;*.m4a)"
AUDIO_FILE_TYPES: Final = (AUDIO_FILTER,)


class DialogBackend(Protocol):
    """The pywebview seam; tests inject a scripted implementation."""

    async def choose_files(
        self, *, allow_multiple: bool, file_types: tuple[str, ...]
    ) -> Sequence[str] | None: ...


class AudioFileDialog(Protocol):
    """What the page needs from an open dialog."""

    async def choose_audio_files(self) -> tuple[Path, ...]: ...


@dataclass(frozen=True, slots=True)
class DropSelection:
    """Trusted paths from a native drop, plus how many entries were ignored."""

    accepted: tuple[Path, ...] = ()
    ignored: int = 0


class PywebviewDialogBackend:
    """``app.native.main_window.create_file_dialog`` behind the tiny seam."""

    async def choose_files(
        self, *, allow_multiple: bool, file_types: tuple[str, ...]
    ) -> Sequence[str] | None:
        # Imported lazily so the module stays importable without a window stack
        # (the frozen smoke-test and every UI test import it headlessly).
        import webview
        from nicegui import app

        window = app.native.main_window
        if window is None:
            raise StemForgeError(
                errors.INTERNAL_UNEXPECTED,
                "the native window is not available for a file dialog",
            )
        return await window.create_file_dialog(
            dialog_type=webview.FileDialog.OPEN,
            allow_multiple=allow_multiple,
            file_types=file_types,
        )


class NativeOpenDialog:
    """Chooses one or more audio files through the native open dialog."""

    def __init__(self, backend: DialogBackend | None = None) -> None:
        self._backend = backend if backend is not None else PywebviewDialogBackend()

    async def choose_audio_files(self) -> tuple[Path, ...]:
        """Return trusted selected paths; an empty tuple means the user cancelled."""
        raw = await self._backend.choose_files(
            allow_multiple=True,
            file_types=AUDIO_FILE_TYPES,
        )
        if not raw:
            return ()
        return normalize_selected_paths(raw)


def normalize_selected_paths(raw: Iterable[str]) -> tuple[Path, ...]:
    """Strip whitespace, reject untrusted paths, and enforce the extension set.

    Untrusted entries are ignored (a real dialog never returns them, but the
    defence is cheap). A trusted path with an unsupported extension raises the
    coded selection error, so the page can show the same field-level treatment
    as an unsupported YouTube source without starting a Job.
    """
    accepted: list[Path] = []
    for item in raw:
        text = str(item).strip()
        if not text or not is_native_path(text):
            continue
        if not is_supported_audio_path(text):
            raise StemForgeError(
                errors.IMPORT_UNSUPPORTED_SOURCE,
                "the selected file is not a supported audio format",
                detail="extension",
                context={"path": text},
            )
        accepted.append(Path(text))
    return tuple(accepted)


def filter_drop_paths(paths: Iterable[object]) -> DropSelection:
    """Keep only absolute native paths with a supported audio extension.

    pywebview supplies ``pywebviewFullPath`` for real drops; browser fake paths
    and non-audio files are counted as ignored so the page can show one quiet
    hint instead of an error.
    """
    accepted: list[Path] = []
    ignored = 0
    for raw in paths:
        text = str(raw).strip() if raw is not None else ""
        if not text or not is_native_path(text) or not is_supported_audio_path(text):
            ignored += 1
            continue
        accepted.append(Path(text))
    return DropSelection(accepted=tuple(accepted), ignored=ignored)


def is_native_path(path: str) -> bool:
    """Whether ``path`` looks like a real absolute path from the native window."""
    text = path.strip()
    if not text or "\x00" in text:
        return False
    lowered = text.lower()
    if "fakepath" in lowered:
        return False
    if "://" in text or lowered.startswith(("file:", "http:", "https:")):
        return False
    try:
        return Path(text).is_absolute()
    except (OSError, ValueError):
        return False


def is_supported_audio_path(path: str | Path) -> bool:
    """Match the v1 audio extensions case-insensitively, ignoring edge whitespace."""
    try:
        suffix = Path(str(path).strip()).suffix.strip().lower()
    except (OSError, ValueError):
        return False
    return suffix in AUDIO_EXTENSIONS
