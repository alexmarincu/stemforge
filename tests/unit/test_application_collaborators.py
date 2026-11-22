"""The application collaborators are usable without the facade (ADR 0013).

These tests pin the seam: each feature service receives its own dependencies, so
a fake job runner or engine is enough to exercise it.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from stemforge.application.collaborators.importing import ImportService
from stemforge.application.collaborators.youtube import YouTubeService
from stemforge.application.state import JobLedger, WorkspaceState
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, OriginKind, Source, SourceMetadata, Track
from stemforge.engines.media import MediaTool
from stemforge.engines.youtube import YouTubeTool
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit

SOURCE_ID = "src-collaborator"
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _deps(tmp_path: Path) -> tuple[SettingsStore, ArtifactStore, JobLedger, WorkspaceState]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    artifacts = ArtifactStore(paths)
    workspace = WorkspaceState()
    ledger = JobLedger(JobRunner(), workspace.lock)
    return settings, artifacts, ledger, workspace


def _source() -> Source:
    return Source(
        source_id=SOURCE_ID,
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(title="Song", duration_s=120.0),
    )


def test_import_service_accepts_a_source_without_the_facade(tmp_path: Path) -> None:
    settings, artifacts, ledger, workspace = _deps(tmp_path)
    service = ImportService(settings, artifacts, ledger, workspace, media=None)

    track = service.accept_source(_source())

    assert workspace.find_by_source(SOURCE_ID) == track
    assert settings.recent_files()[0].source_id == SOURCE_ID


def test_import_service_submits_a_local_job_with_a_fake_media_engine(tmp_path: Path) -> None:
    settings, artifacts, ledger, workspace = _deps(tmp_path)
    service = ImportService(
        settings,
        artifacts,
        ledger,
        workspace,
        media=cast("MediaTool", object()),
    )

    job_id = service.import_local(tmp_path / "song.wav")

    jobs = ledger.jobs()
    assert jobs[0].job_id == job_id
    assert jobs[0].kind is JobKind.IMPORT_LOCAL


def test_youtube_service_submits_a_job_with_fake_engines(tmp_path: Path) -> None:
    settings, artifacts, ledger, _workspace = _deps(tmp_path)
    service = YouTubeService(
        settings,
        settings.paths,
        artifacts,
        ledger,
        accept_source=lambda source: Track(
            track_id="track-accepted",
            source=source,
            title=source.title,
        ),
        youtube=cast("YouTubeTool", object()),
        media=cast("MediaTool", object()),
        delay=lambda _: None,
    )

    job_id = service.submit_youtube(VIDEO_URL)

    jobs = ledger.jobs()
    assert jobs[0].job_id == job_id
    assert jobs[0].kind is JobKind.IMPORT_YOUTUBE
