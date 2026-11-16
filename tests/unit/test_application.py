"""``WorkspaceService``: the headless application seam over settings and domain."""

from __future__ import annotations

from pathlib import Path

import pytest

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import OriginKind
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore


def _service(tmp_path: Path) -> tuple[WorkspaceService, SettingsStore, AppPaths]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    return WorkspaceService(settings, paths=paths), settings, paths


@pytest.mark.unit
def test_service_starts_with_an_empty_workspace(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    snapshot = service.snapshot()

    assert snapshot.tracks == ()
    assert len(snapshot) == 0


@pytest.mark.unit
def test_service_exposes_the_settings_store_and_paths(tmp_path: Path) -> None:
    service, settings, paths = _service(tmp_path)

    assert service.settings is settings
    assert service.paths == paths


@pytest.mark.unit
def test_paths_default_to_the_settings_store_paths(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)

    service = WorkspaceService(settings)

    assert service.paths == paths


@pytest.mark.unit
def test_recent_files_are_delegated_to_settings(tmp_path: Path) -> None:
    service, settings, _ = _service(tmp_path)
    settings.record_recent(
        source_id="src-1",
        title="Song",
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
    )

    assert [entry.source_id for entry in service.recent_files()] == ["src-1"]
    assert service.remove_recent("src-1") is True
    assert service.recent_files() == ()
    assert service.remove_recent("src-1") is False


@pytest.mark.unit
def test_placeholders_and_engine_fakes_are_injectable(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    artifacts = ArtifactStore()
    runner = JobRunner()
    separation = object()
    pitch = object()
    media = object()
    youtube = object()
    engines = ServiceEngines(
        separation=separation,
        pitch=pitch,
        media=media,
        youtube=youtube,
    )

    service = WorkspaceService(
        settings,
        paths=paths,
        artifacts=artifacts,
        runner=runner,
        engines=engines,
    )

    assert service.artifacts is artifacts
    assert service.runner is runner
    assert service.engines is engines
    assert service.engines.separation is separation
    assert service.engines.pitch is pitch
    assert service.engines.media is media
    assert service.engines.youtube is youtube


@pytest.mark.unit
def test_service_constructs_default_placeholders(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    assert isinstance(service.artifacts, ArtifactStore)
    assert isinstance(service.runner, JobRunner)
    assert isinstance(service.engines, ServiceEngines)
    assert service.engines.separation is None
