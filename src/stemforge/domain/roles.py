"""The one vocabulary for a Separation's outputs (GLOSSARY, ticket 05 §3).

Instrumental is a derived Artifact, not a Stem, so it is not in
:data:`STEM_ROLES`. Consumers that need "everything a Separation emits" use
:data:`OUTPUT_ROLES`. The four Stem names have one source of truth in
:mod:`stemforge.worker_protocol`, which the worker is allowed to import and
which keeps the parent and worker from drifting apart.
"""

from __future__ import annotations

from typing import Final

from stemforge.worker_protocol import STEM_NAMES

__all__ = [
    "INSTRUMENTAL_ROLE",
    "NON_VOCAL_ROLES",
    "OUTPUT_ROLES",
    "STEM_ROLES",
]

STEM_ROLES: Final[tuple[str, ...]] = STEM_NAMES
NON_VOCAL_ROLES: Final[tuple[str, ...]] = ("drums", "bass", "other")
INSTRUMENTAL_ROLE: Final = "instrumental"
OUTPUT_ROLES: Final[tuple[str, ...]] = (*STEM_ROLES, INSTRUMENTAL_ROLE)
