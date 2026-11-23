"""Stable identities derived from committed Artifact manifests."""

from __future__ import annotations

import hashlib

from stemforge.artifacts import SeparationArtifact


def separation_key(artifact: SeparationArtifact) -> str:
    """A stable, path-safe identity for one method's committed Separation set."""
    digest = hashlib.sha256(
        f"{artifact.source_id}:{artifact.method}:{artifact.created_at.isoformat()}".encode()
    ).hexdigest()
    return f"sep-{digest[:16]}"
