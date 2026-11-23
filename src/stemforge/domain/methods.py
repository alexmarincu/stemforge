"""The Separation method registry (GLOSSARY: Separation method).

A method declares the Stems it produces and whether it needs Model weights. It
is the single source of a method's stable id, label, method version, output
roles in presentation order, and output count; a Separation set is validated
and rendered from its method rather than from a fixed five-role list.

This module stays stdlib-only. The worker process cannot import ``domain``
(import-linter), so :mod:`stemforge.worker_protocol` carries the same
``method id -> output roles`` mapping and a contract test keeps the two
identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

__all__ = [
    "DEFAULT_METHOD_ID",
    "DEMUCS_METHOD",
    "KARAOKE_METHOD",
    "METHODS",
    "SeparationMethod",
    "auto_method_id",
    "method_by_id",
]

DEFAULT_METHOD_ID: Final = "demucs"


@dataclass(frozen=True, slots=True)
class SeparationMethod:
    """One named way of performing a Separation.

    ``stems`` is the method's Stem vocabulary; Instrumental is an output, never
    a Stem (GLOSSARY). ``output_roles`` is every role the method emits in the
    order the UI presents them, so a caller never reconstructs it from a global
    list.
    """

    id: str
    label: str
    version: int
    stems: tuple[str, ...]
    output_roles: tuple[str, ...]
    requires_model: bool
    stereo_only: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise ValueError("a Separation method needs a non-empty id and label")
        if self.version < 1:
            raise ValueError("a Separation method version must be positive")
        if not self.output_roles or len(set(self.output_roles)) != len(self.output_roles):
            raise ValueError("output roles must be a non-empty unique tuple")
        if not set(self.stems) < set(self.output_roles):
            raise ValueError("Stems must be a proper subset of the output roles")

    @property
    def output_count(self) -> int:
        return len(self.output_roles)


DEMUCS_METHOD: Final = SeparationMethod(
    id="demucs",
    label="Demucs",
    version=1,
    stems=("vocals", "drums", "bass", "other"),
    output_roles=("vocals", "instrumental", "drums", "bass", "other"),
    requires_model=True,
)

KARAOKE_METHOD: Final = SeparationMethod(
    id="karaoke",
    label="Karaoke",
    version=1,
    stems=("vocals",),
    output_roles=("vocals", "instrumental"),
    requires_model=False,
    stereo_only=True,
)

METHODS: Final[MappingProxyType[str, SeparationMethod]] = MappingProxyType(
    {
        DEMUCS_METHOD.id: DEMUCS_METHOD,
        KARAOKE_METHOD.id: KARAOKE_METHOD,
    }
)


def method_by_id(method_id: str) -> SeparationMethod | None:
    """Return the registered method, or ``None`` for an unknown id."""
    return METHODS.get(method_id)


def auto_method_id(*, demucs_installed: bool) -> str:
    """Resolve an unset preference: Demucs when installed, otherwise Karaoke."""
    return DEMUCS_METHOD.id if demucs_installed else KARAOKE_METHOD.id
