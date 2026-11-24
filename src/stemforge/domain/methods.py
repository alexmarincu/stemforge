"""The Separation method registry (GLOSSARY: Separation method).

A method declares the Stems it produces and whether it needs Model weights. It
is the single source of a method's stable id, label, description key, method
version, output roles in presentation order, and output count; a Separation set
is validated and rendered from its method rather than from a fixed five-role
list.

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
    "method_id_or_default",
]

DEFAULT_METHOD_ID: Final = "demucs"


@dataclass(frozen=True, slots=True)
class SeparationMethod:
    """One named way of performing a Separation.

    ``stems`` is the method's Stem vocabulary; Instrumental is an output, never
    a Stem (GLOSSARY). ``output_roles`` is every role the method emits in the
    order the UI presents them, so a caller never reconstructs it from a global
    list. ``instrumental_is_sum`` records whether the Instrumental is derived by
    summing the method's non-vocal Stems or stored directly as its own output;
    the two shapes take different Render paths. ``stereo_only`` records that the
    method needs a genuine stereo image.
    """

    id: str
    label: str
    description_key: str
    version: int
    stems: tuple[str, ...]
    output_roles: tuple[str, ...]
    requires_model: bool
    instrumental_is_sum: bool
    stereo_only: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise ValueError("a Separation method needs a non-empty id and label")
        if not self.description_key:
            raise ValueError("a Separation method needs a non-empty description key")
        if self.version < 1:
            raise ValueError("a Separation method version must be positive")
        if not self.output_roles or len(set(self.output_roles)) != len(self.output_roles):
            raise ValueError("output roles must be a non-empty unique tuple")
        if not set(self.stems) < set(self.output_roles):
            raise ValueError("Stems must be a proper subset of the output roles")
        if not isinstance(self.instrumental_is_sum, bool):
            raise ValueError("instrumental_is_sum must be a bool")
        if "instrumental" not in self.output_roles:
            raise ValueError("every method emits an instrumental output")
        if self.instrumental_is_sum and not self.non_vocal_stems:
            raise ValueError("a summed Instrumental needs non-vocal Stems")

    @property
    def output_count(self) -> int:
        return len(self.output_roles)

    @property
    def non_vocal_stems(self) -> tuple[str, ...]:
        return tuple(role for role in self.stems if role != "vocals")

    @property
    def render_input_roles(self) -> tuple[str, ...]:
        """The stored roles a stems Render reads for this method.

        A summed Instrumental is derived from the Stems, so it is not read; a
        stored Instrumental is shifted directly and is an input like any other
        output.
        """
        return self.stems if self.instrumental_is_sum else self.output_roles


DEMUCS_METHOD: Final = SeparationMethod(
    id="demucs",
    label="Demucs",
    description_key="separation-method.demucs",
    version=1,
    stems=("vocals", "drums", "bass", "other"),
    output_roles=("vocals", "instrumental", "drums", "bass", "other"),
    requires_model=True,
    instrumental_is_sum=True,
)

KARAOKE_METHOD: Final = SeparationMethod(
    id="karaoke",
    label="Karaoke",
    description_key="separation-method.karaoke",
    version=1,
    stems=("vocals",),
    output_roles=("vocals", "instrumental"),
    requires_model=False,
    instrumental_is_sum=False,
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


def method_id_or_default(value: object) -> str:
    """A manifest's recorded method, or Demucs for an absent or unknown value.

    Manifests written before methods existed have no field, and every one of
    them is a Demucs set (ADR 0015). An unknown id is treated the same way so a
    stray value cannot invent a method the registry does not know.
    """
    if isinstance(value, str) and method_by_id(value) is not None:
        return value
    return DEFAULT_METHOD_ID
