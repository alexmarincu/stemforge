"""The recent-sources feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from pathlib import Path

from stemforge.domain.models import OriginKind
from stemforge.settings import RecentFile, SettingsStore


class RecentService:
    """Owns the persisted recent list and its availability checks."""

    def __init__(self, settings: SettingsStore) -> None:
        self._settings = settings

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._settings.recent_files()

    def find(self, source_id: str) -> RecentFile | None:
        return next((item for item in self.recent_files() if item.source_id == source_id), None)

    def remove_recent(self, source_id: str) -> bool:
        return self._settings.remove_recent(source_id)

    def recent_available(self, source_id: str) -> bool:
        """Whether a recent entry can be opened without asking the user to find it.

        A local path must still be a file. A YouTube URL is not a filesystem path;
        availability is unknowable until a download is attempted, so it stays
        clickable and the importer owns the network outcome.
        """
        entry = self.find(source_id)
        if entry is None:
            return False
        if entry.origin_kind is OriginKind.YOUTUBE:
            return True
        return Path(entry.origin).is_file()

    def clear_recent(self) -> None:
        """Clear the recent-files list; cached audio is untouched (ticket 07)."""
        self._settings.clear_recent()
