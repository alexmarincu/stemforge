"""Presentation order and labels for Separation outputs.

Instrumental is a stored Artifact, not a Stem (GLOSSARY), so the map is
deliberately not named for stems.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from stemforge.domain.roles import INSTRUMENTAL_ROLE

SEPARATION_ROLE_LABELS: Final[Mapping[str, str]] = {
    "vocals": "Vocals",
    INSTRUMENTAL_ROLE: "Instrumental",
    "drums": "Drums",
    "bass": "Bass",
    "other": "Other",
}

# Display order shared by the service preview API and the Outputs card.
SEPARATION_OUTPUT_ROLES: Final[tuple[str, ...]] = (
    "vocals",
    INSTRUMENTAL_ROLE,
    "drums",
    "bass",
    "other",
)
