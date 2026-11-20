"""Native open/save dialog wrappers: strict filters, trusted paths, extension repair.

pywebview's dialog API has no NiceGUI wrapper. This module owns the two dialogs
StemForge needs: the open dialog and the save dialog for Export.

* filter strings follow pywebview's strict grammar (description ``[\\w ]+``,
  patterns ``*.ext`` joined by ``;`` with no spaces; no hyphen or comma);
* only absolute native paths are trusted; browser artefacts such as
  ``C:\\fakepath\\…`` and ``file://`` URLs are never imported or written;
* open extensions are matched case-insensitively and with surrounding
  whitespace ignored, and anything outside the audio set is a coded selection
  error;
* a save name is Windows-safe and always carries the chosen format's extension.
  A known audio extension the user typed is replaced, never used to switch the
  chosen format (ticket 08 §9, ticket 18).

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
from stemforge.pipeline.export_names import export_filename

AUDIO_EXTENSIONS: Final = (".wav", ".mp3", ".flac", ".m4a")
AUDIO_FILTER: Final = "Audio (*.wav;*.mp3;*.flac;*.m4a)"
AUDIO_FILE_TYPES: Final = (AUDIO_FILTER,)

# The three export formats (ticket 05 §7). Descriptions stay within
# pywebview's ``[\w ]+`` grammar: no hyphen, comma, or slash.
SAVE_AUDIO_EXTENSIONS: Final = (".wav", ".flac", ".mp3")
SAVE_FILTERS: Final[dict[str, str]] = {
    ".wav": "WAV 24 bit (*.wav)",
    ".flac": "FLAC 24 bit (*.flac)",
    ".mp3": "MP3 320 kbps (*.mp3)",
}

# Extensions that mean "audio" when a user types one into the save dialog. A
# matching or missing extension is normalised; a *different known* audio
# extension is replaced. Unknown suffixes are kept so ``mix.v2`` stays
# ``mix.v2.wav`` rather than losing the user's dot.
_KNOWN_AUDIO_EXTENSIONS: Final = frozenset(
    {
        *AUDIO_EXTENSIONS,
        *SAVE_AUDIO_EXTENSIONS,
        ".aac",
        ".aif",
        ".aiff",
        ".ogg",
        ".opus",
        ".wma",
    }
)


class DialogBackend(Protocol):
    """The pywebview open seam; tests inject a scripted implementation."""

    async def choose_files(
        self, *, allow_multiple: bool, file_types: tuple[str, ...]
    ) -> Sequence[str] | None: ...


class SaveDialogBackend(Protocol):
    """The pywebview save seam; tests inject a scripted implementation."""

    async def save_file(
        self,
        *,
        default_name: str,
        directory: str | None,
        file_types: tuple[str, ...],
    ) -> Sequence[str] | None: ...


class AudioFileDialog(Protocol):
    """What the page needs from an open dialog."""

    async def choose_audio_files(self) -> tuple[Path, ...]: ...


class SaveAudioFileDialog(Protocol):
    """What the export sheet needs from a save dialog."""

    async def save_audio_file(
        self,
        *,
        default_name: str,
        suffix: str,
        directory: Path | None = None,
    ) -> SaveSelection | None: ...


@dataclass(frozen=True, slots=True)
class SaveName:
    """A Windows-safe filename the save dialog may be seeded with."""

    name: str
    corrected: bool


@dataclass(frozen=True, slots=True)
class SaveSelection:
    """One trusted save destination, plus the extension the user typed."""

    path: Path
    corrected: bool
    typed_name: str


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


class PywebviewSaveBackend:
    """``create_file_dialog(save_dialog=True)`` behind the tiny seam."""

    async def save_file(
        self,
        *,
        default_name: str,
        directory: str | None,
        file_types: tuple[str, ...],
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
            dialog_type=webview.FileDialog.SAVE,
            directory=directory or "",
            save_filename=default_name,
            file_types=file_types,
        )


class NativeSaveDialog:
    """Chooses one export destination through the native save dialog."""

    def __init__(self, backend: SaveDialogBackend | None = None) -> None:
        self._backend = backend if backend is not None else PywebviewSaveBackend()

    async def save_audio_file(
        self,
        *,
        default_name: str,
        suffix: str,
        directory: Path | None = None,
    ) -> SaveSelection | None:
        """Return a trusted destination, or ``None`` when the user cancelled.

        The dialog is seeded with a sanitised default name and the chosen
        format's filter. Whatever the user types comes back through
        :func:`normalize_save_path`, so Linux's missing extension and a typed
        wrong extension are both repaired here rather than in the sheet.
        """
        normalized = normalize_save_name(default_name, suffix)
        target_dir = directory if directory is not None and directory.is_dir() else None
        raw = await self._backend.save_file(
            default_name=normalized.name,
            directory=str(target_dir) if target_dir is not None else None,
            file_types=(save_filter_for(suffix),),
        )
        if not raw:
            return None
        first = next((item for item in raw if str(item).strip()), None)
        if first is None:
            return None
        return normalize_save_path(first, suffix)


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


def save_filter_for(suffix: str) -> str:
    """Return the strict pywebview filter for one export suffix."""
    return SAVE_FILTERS[_normalize_save_suffix(suffix)]


def normalize_save_name(name: str, suffix: str) -> SaveName:
    """Make a save filename Windows-safe and force the chosen format's extension.

    A known audio extension is removed first; when it differs from ``suffix`` the
    result is flagged ``corrected`` so the sheet can tell the user. The chosen
    format is never changed to match what was typed (ticket 08 §9).
    """
    normalized_suffix = _normalize_save_suffix(suffix)
    text = str(name).strip()
    base = text
    typed_suffix = ""
    lowered = text.lower()
    for known in sorted(_KNOWN_AUDIO_EXTENSIONS, key=len, reverse=True):
        if lowered.endswith(known):
            typed_suffix = known
            base = text[: -len(known)]
            break
    filename = export_filename(base, normalized_suffix)
    return SaveName(
        name=filename, corrected=bool(typed_suffix) and typed_suffix != normalized_suffix
    )


def normalize_save_path(raw: object, suffix: str) -> SaveSelection | None:
    """Normalise a dialog result to a trusted absolute path, or ``None``.

    Browser fake paths, URLs, relative names, and empty results are rejected
    exactly like the open dialog rejects them. The returned path always ends in
    the selected format's suffix.
    """
    text = str(raw).strip() if raw is not None else ""
    if not text or not is_native_path(text):
        return None
    try:
        path = Path(text)
    except (OSError, ValueError):
        return None
    if not path.name:
        return None
    typed_name = path.name
    normalized = normalize_save_name(typed_name, suffix)
    if normalized.name != typed_name:
        path = path.with_name(normalized.name)
    return SaveSelection(path=path, corrected=normalized.corrected, typed_name=typed_name)


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
    """Match the audio extensions case-insensitively, ignoring edge whitespace."""
    try:
        suffix = Path(str(path).strip()).suffix.strip().lower()
    except (OSError, ValueError):
        return False
    return suffix in AUDIO_EXTENSIONS


def _normalize_save_suffix(suffix: str) -> str:
    value = str(suffix).strip().lower()
    if not value:
        raise ValueError("save suffix must not be empty")
    if not value.startswith("."):
        value = f".{value}"
    if value not in SAVE_FILTERS:
        raise ValueError(f"unsupported export suffix: {suffix!r}")
    return value
