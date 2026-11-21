"""The recent-sources feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from pathlib import Path

from stemforge import errors
from stemforge.application.collaborators.importing import ImportService
from stemforge.application.collaborators.youtube import YouTubeService
from stemforge.domain.models import OriginKind
from stemforge.errors import StemForgeError
from stemforge.settings import RecentFile, SettingsStore


class RecentService:
    """Owns the persisted recent list and re-opening one of its Sources."""

    def __init__(
        self,
        settings: SettingsStore,
        imports: ImportService,
        youtube: YouTubeService,
    ) -> None:
        self._settings = settings
        self._imports = imports
        self._youtube = youtube

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._settings.recent_files()

    def remove_recent(self, source_id: str) -> bool:
        return self._settings.remove_recent(source_id)

    def recent_available(self, source_id: str) -> bool:
        """Whether a recent entry can be opened without asking the user to find it.

        A local path must still be a file. A YouTube URL is not a filesystem path;
        availability is unknowable until a download is attempted, so it stays
        clickable and the importer owns the network outcome.
        """
        entry = next((item for item in self.recent_files() if item.source_id == source_id), None)
        if entry is None:
            return False
        if entry.origin_kind is OriginKind.YOUTUBE:
            return True
        return Path(entry.origin).is_file()

    def open_recent(self, source_id: str) -> str | None:
        """Re-import one recent entry and return its Job id.

        A missing local file raises the coded ``import.file_unavailable`` before
        any Job starts (ticket 07 §5: nothing starts until the user acts). A
        YouTube entry goes back through ``submit_youtube``, so a cached video
        opens without a network call and a missing one re-downloads.
        """
        entry = next((item for item in self.recent_files() if item.source_id == source_id), None)
        if entry is None:
            raise KeyError(source_id)
        if entry.origin_kind is OriginKind.YOUTUBE:
            return self._youtube.submit_youtube(entry.origin)
        path = Path(entry.origin)
        if not path.is_file():
            raise StemForgeError(
                errors.IMPORT_FILE_UNAVAILABLE,
                "the recent file is no longer readable",
                detail="missing",
                context={"path": str(path)},
            )
        return self._imports.import_local(path)

    def clear_recent(self) -> None:
        """Clear the recent-files list; cached audio is untouched (ticket 07)."""
        self._settings.clear_recent()
