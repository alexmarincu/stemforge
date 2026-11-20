"""The one vocabulary for a Separation's outputs (GLOSSARY, ticket 05 §3).

Instrumental is a derived Artifact, not a Stem, so it is not in
:data:`STEM_ROLES`. Consumers that need "everything a Separation emits" use
:data:`OUTPUT_ROLES`. This module stays stdlib-only (ticket 06 §1); the worker
process has its own copy in :mod:`stemforge.worker_protocol`, and the contract
test keeps the two tuples identical.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "INSTRUMENTAL_ROLE",
    "NON_VOCAL_ROLES",
    "OUTPUT_ROLES",
    "STEM_ROLES",
]

STEM_ROLES: Final[tuple[str, ...]] = ("vocals", "drums", "bass", "other")
NON_VOCAL_ROLES: Final[tuple[str, ...]] = ("drums", "bass", "other")
INSTRUMENTAL_ROLE: Final = "instrumental"
OUTPUT_ROLES: Final[tuple[str, ...]] = (*STEM_ROLES, INSTRUMENTAL_ROLE)

