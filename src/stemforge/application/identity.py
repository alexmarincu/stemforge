"""Stable identities derived from committed Artifact manifests."""

from __future__ import annotations

from stemforge.artifacts import SeparationArtifact


def separation_key(artifact: SeparationArtifact) -> str:
    """A stable, path-safe identity for one method's committed Separation set."""
    return artifact.set_key
