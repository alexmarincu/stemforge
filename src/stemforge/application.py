"""``WorkspaceService``: the only API the UI calls (ticket 06 §2).

The service owns the Workspace and will orchestrate Jobs on behalf of the UI.
This spine constructs headless: no NiceGUI, no torch, and injectable placeholders
for the ArtifactStore, JobRunner, and engine adapters. Ticket 08 fills in the
import, separation, render, and export methods on this seam.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from stemforge.artifacts import ArtifactStore
from stemforge.domain.workspace import Workspace, WorkspaceSnapshot
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import RecentFile, SettingsStore


@dataclass(frozen=True, slots=True)
class ServiceEngines:
    """The engine adapters a WorkspaceService may call; tests inject fakes."""

    separation: object | None = None
    pitch: object | None = None
    media: object | None = None
    youtube: object | None = None


class WorkspaceService:
    """Owns the Workspace and orchestrates Jobs on behalf of the UI."""

    def __init__(
        self,
        settings: SettingsStore,
        *,
        paths: AppPaths | None = None,
        artifacts: ArtifactStore | None = None,
        runner: JobRunner | None = None,
        engines: ServiceEngines | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths if paths is not None else settings.paths
        self._artifacts = (
            artifacts
            if artifacts is not None
            else ArtifactStore(self._paths, cap_bytes=self._settings.settings.cache_cap_bytes)
        )
        self._runner = runner if runner is not None else JobRunner()
        self._engines = engines if engines is not None else ServiceEngines()
        self._workspace = Workspace()
        self._lock = threading.Lock()

    @property
    def settings(self) -> SettingsStore:
        return self._settings

    @property
    def paths(self) -> AppPaths:
        return self._paths

    @property
    def artifacts(self) -> ArtifactStore:
        return self._artifacts

    @property
    def runner(self) -> JobRunner:
        return self._runner

    @property
    def engines(self) -> ServiceEngines:
        return self._engines

    def snapshot(self) -> WorkspaceSnapshot:
        """Return an immutable view of the Workspace for the UI."""
        with self._lock:
            return self._workspace.snapshot()

    def recent_files(self) -> tuple[RecentFile, ...]:
        return self._settings.recent_files()

    def remove_recent(self, source_id: str) -> bool:
        return self._settings.remove_recent(source_id)
