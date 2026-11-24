"""The Demucs role vocabulary (GLOSSARY: Stem, Instrumental).

Instrumental is a derived Artifact, not a Stem, so it is not in
:data:`STEM_ROLES`. These tuples describe the Demucs method and the legacy
manifests that predate methods; method-aware code takes its roles from the
:mod:`stemforge.domain.methods` descriptor instead. This module stays
stdlib-only; the worker process has its own copy in
:mod:`stemforge.worker_protocol`, and the contract test keeps the two tuples
identical.
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
