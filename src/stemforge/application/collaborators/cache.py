"""The cache-maintenance feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from stemforge.artifacts import (
    ArtifactStore,
    ClearReport,
    EvictionReport,
    RemoveReport,
    SweepReport,
)


class CacheService:
    """Owns the rebuildable cache surfaces: startup passes, meters, and clears."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    def sweep_stale(self) -> SweepReport:
        return self._artifacts.sweep_stale()

    def enforce_cache_cap(self) -> EvictionReport:
        return self._artifacts.enforce_cap()

    def cache_usage_bytes(self) -> int:
        return self._artifacts.usage_bytes()

    def cache_cap_bytes(self) -> int:
        return self._artifacts.cap_bytes

    def youtube_cache_bytes(self) -> int:
        return self._artifacts.youtube_cache_bytes()

    def clear_youtube_cache(self) -> int:
        return self._artifacts.clear_youtube_cache()

    def clear_cache(self) -> ClearReport:
        return self._artifacts.clear_cache()

    def release_reference(self, source_id: str) -> bool:
        """Drop one session reference; return whether a reference was dropped."""
        return self._artifacts.release(source_id)

    def is_live(self, source_id: str) -> bool:
        """Whether any Track still references the Source."""
        return self._artifacts.is_live(source_id)

    def remove_source(self, source_id: str) -> RemoveReport:
        """Delete one Source's cached artifacts and renders."""
        return self._artifacts.remove_source(source_id)
