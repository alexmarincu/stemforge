"""Pure Windows-safe export names, collision suffixes, and space estimates.

Ticket 05 §7 fixes the suggested names and the sanitizing rules; ticket 17 §1
adds the 255-byte filename cap and deterministic collision suffixes. Everything
here is filesystem-free except :func:`unique_export_path`, whose existence probe
is injectable, so the unit layer can test every branch without FFmpeg or a real
destination.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from stemforge.artifacts import estimate_export_bytes

__all__ = [
    "MAX_BASE_CHARS",
    "MAX_FILENAME_BYTES",
    "RESERVED_DEVICE_NAMES",
    "ROLE_LABELS",
    "export_filename",
    "required_export_space",
    "sanitize_base_name",
    "suggest_base_name",
    "unique_export_path",
]

MAX_BASE_CHARS: Final = 120
MAX_FILENAME_BYTES: Final = 255
COLLISION_LIMIT: Final = 9_999

_INVALID_CHARS: Final = frozenset('<>:"/\\|?*')
_RESERVED_ROOTS: Final = (
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
)
RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(_RESERVED_ROOTS)
_FALLBACK_NAME: Final = "Export"

ROLE_LABELS: Final[Mapping[str, str]] = {
    "original": "Original",
    "vocals": "Vocals",
    "drums": "Drums",
    "bass": "Bass",
    "other": "Other",
    "instrumental": "Instrumental",
}


def sanitize_base_name(name: str) -> str:
    """Return a Windows-safe base name without an extension.

    Illegal characters, control characters, and DEL are replaced with ``_``.
    Leading/trailing dots and spaces are stripped, the base is capped at
    :data:`MAX_BASE_CHARS`, and a reserved device name is prefixed with ``_``
    (so ``CON.txt`` becomes ``_CON.txt`` and stays a legal file).
    """
    cleaned = "".join(
        "_"
        if character in _INVALID_CHARS
        or ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        else character
        for character in str(name)
    )
    cleaned = cleaned.strip(" .")
    cleaned = cleaned[:MAX_BASE_CHARS].rstrip(" .")
    if not cleaned or set(cleaned) <= {"."}:
        cleaned = _FALLBACK_NAME
    if _is_reserved(cleaned):
        cleaned = f"_{cleaned}"[:MAX_BASE_CHARS].rstrip(" .")
    return cleaned


def suggest_base_name(title: str, *, role: str | None = None, semitones: int = 0) -> str:
    """Build the ticket 05 §7 suggestion, without an extension.

    ``title`` for the original; ``title - Vocals`` for an unshifted output;
    ``title +3st`` for a shifted original; ``title - Vocals +3st`` for a shifted
    stem. The title is sanitized first and shortened so the complete base stays
    within :data:`MAX_BASE_CHARS`; the role and semitone suffix is never cut.
    """
    suffix = ""
    if role is not None and role != "original":
        label = ROLE_LABELS.get(role, role.replace("_", " ").title())
        suffix += f" - {label}"
    if semitones:
        suffix += f" {semitones:+d}st"

    budget = MAX_BASE_CHARS - len(suffix)
    base = sanitize_base_name(title)
    if budget < 1:
        base = _FALLBACK_NAME
    elif len(base) > budget:
        base = base[:budget].rstrip(" .") or _FALLBACK_NAME
    return f"{base}{suffix}"


def export_filename(base: str, suffix: str, *, collision: int = 1) -> str:
    """Return the final filename for ``base``, ``suffix``, and a collision number.

    ``collision=1`` is the plain name; ``2`` adds `` (2)``, and so on. The base
    is sanitized and truncated so the UTF-8 filename never exceeds 255 bytes,
    even when the last character is multibyte.
    """
    normalized_suffix = _normalize_suffix(suffix)
    if collision < 1:
        raise ValueError("collision must be positive")
    tail = "" if collision == 1 else f" ({collision})"
    reserved_bytes = len(f"{tail}{normalized_suffix}".encode())
    budget = MAX_FILENAME_BYTES - reserved_bytes
    if budget < 1:
        raise ValueError("suffix and collision suffix leave no room for a name")

    stem = sanitize_base_name(base)
    stem = _truncate_utf8(stem, budget).rstrip(" .")
    if not stem:
        stem = _FALLBACK_NAME
    if _is_reserved(stem):
        stem = f"_{stem}"
    stem = _truncate_utf8(stem, budget).rstrip(" .")
    if not stem:
        raise ValueError("the sanitized base leaves no room for a name")
    return f"{stem}{tail}{normalized_suffix}"


def unique_export_path(
    directory: Path,
    base: str,
    suffix: str,
    *,
    exists: Callable[[Path], bool] | None = None,
) -> Path:
    """Pick the first free ``base``/``base (2)``/``base (3)`` path.

    ``exists`` defaults to :meth:`pathlib.Path.exists`; tests inject a predicate
    so the collision sequence is deterministic without touching the filesystem.
    """
    probe = exists if exists is not None else _path_exists
    for collision in range(1, COLLISION_LIMIT + 1):
        candidate = Path(directory) / export_filename(base, suffix, collision=collision)
        if not probe(candidate):
            return candidate
    raise ValueError(f"no available export name after {COLLISION_LIMIT} collisions")


def required_export_space(duration_s: float) -> int:
    """Free space an export needs: the ticket 07 §8 WAV-24 worst case.

    The estimate already carries the 1.2 job headroom and 64 MiB of slack, so
    export passes ``floor=0`` to :func:`~stemforge.artifacts.require_free_space`
    and does not add the cache's 1 GiB floor.
    """
    if duration_s < 0:
        raise ValueError("duration must not be negative")
    return estimate_export_bytes(duration_s)


def _normalize_suffix(suffix: str) -> str:
    value = suffix.strip()
    if not value:
        raise ValueError("suffix must not be empty")
    if not value.startswith("."):
        value = f".{value}"
    if len(value) < 2 or not value[1:].isalnum():
        raise ValueError(f"invalid filename suffix: {suffix!r}")
    return value.lower()


def _is_reserved(name: str) -> bool:
    root = name.split(".", 1)[0].rstrip(" .").upper()
    return root in RESERVED_DEVICE_NAMES


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _path_exists(path: Path) -> bool:
    return path.exists()
