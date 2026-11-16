"""``ArtifactStore``: the cache layout, manifests, staging, and promotion.

Ticket 07 owns the implementation and is the only writer of artifact files.
"""

from __future__ import annotations


class ArtifactStore:
    """Owns the on-disk artifact layout and validates every manifest."""
