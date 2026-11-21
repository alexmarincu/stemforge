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
        """Run the startup sweep once; the UI shows recovery copy when it deleted work."""
        return self._artifacts.sweep_stale()

    def enforce_cache_cap(self) -> EvictionReport:
        """Run the startup LRU pass after the sweep (ticket 07 §2, §7)."""
        return self._artifacts.enforce_cap()

    def cache_usage_bytes(self) -> int:
        """The cache meter's used size; the UI never reads the store directly."""
        return self._artifacts.usage_bytes()

    def cache_cap_bytes(self) -> int:
        """The configured cache ceiling (ticket 07 §2)."""
        return self._artifacts.cap_bytes

    def youtube_cache_bytes(self) -> int:
        """The size of ``<cache>/yt-dlp/`` shown in Settings & About."""
        return self._artifacts.youtube_cache_bytes()

    def clear_youtube_cache(self) -> int:
        """Delete only ``<cache>/yt-dlp/`` and return the bytes freed."""
        return self._artifacts.clear_youtube_cache()

    def clear_cache(self) -> ClearReport:
        """Delete every non-live cached artifact plus the tool caches (ticket 07)."""
        return self._artifacts.clear_cache()

    def release_source(self, source_id: str) -> bool:
        """Drop one session reference; return whether any reference remains."""
        return self._artifacts.release(source_id)

    def is_live(self, source_id: str) -> bool:
        """Whether any Track still references the Source."""
        return self._artifacts.is_live(source_id)

    def remove_source(self, source_id: str) -> RemoveReport:
        """Delete one Source's cached artifacts and renders."""
        return self._artifacts.remove_source(source_id)
