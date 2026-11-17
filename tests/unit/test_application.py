"""``WorkspaceService``: the headless application seam over settings and domain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import OriginKind
from stemforge.errors import StemForgeError
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


@pytest.mark.unit
def test_artifact_store_uses_the_cache_cap_from_settings(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    (paths.config / "settings.json").write_text(
        json.dumps({"schema_version": 1, "cache_cap_bytes": 12345}),
        encoding="utf-8",
    )
    settings = SettingsStore(paths)

    service = WorkspaceService(settings, paths=paths)

    assert service.artifacts.cap_bytes == 12345


@pytest.mark.unit
def test_model_gate_refuses_without_consent_before_submitting(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(StemForgeError) as raised:
        service.install_model()

    assert raised.value.code == "separation.model_missing"
    assert raised.value.detail == "reason=consent"
    assert service.runner.jobs() == []
    assert service.model_status().needs_download is True


@pytest.mark.unit
def test_accepting_the_model_licence_records_the_pinned_version(tmp_path: Path) -> None:
    service, settings, _ = _service(tmp_path)

    consent = service.accept_model_licence()

    assert consent.version == service.model_spec().licence_notice_version
    assert consent.acknowledged is True
    assert service.model_consent_is_current() is True
    assert settings.model_consent() == consent
    assert service.model_status().installed is False


@pytest.mark.unit
def test_youtube_toggle_and_notice_are_delegated_to_settings(tmp_path: Path) -> None:
    service, _, paths = _service(tmp_path)

    assert service.youtube_enabled() is True
    assert service.youtube_notice_acknowledged() is False

    service.set_youtube_enabled(False)
    service.accept_youtube_notice()

    assert service.youtube_enabled() is False
    assert service.youtube_notice_acknowledged() is True
    assert SettingsStore(paths).youtube_enabled is False
    assert SettingsStore(paths).youtube_notice_is_current() is True


@pytest.mark.unit
def test_submit_youtube_validates_then_submits_one_import_job(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    runner = JobRunner(paths=paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        runner=runner,
        engines=ServiceEngines(media=object(), youtube=object()),  # type: ignore[arg-type]
    )

    job_id = service.submit_youtube("youtu.be/dQw4w9WgXcQ?t=5")

    jobs = runner.jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job_id
    assert jobs[0].kind.value == "import_youtube"
    assert jobs[0].state.value == "queued"
    assert jobs[0].title == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.unit
def test_a_second_youtube_submit_reuses_the_active_job(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    runner = JobRunner(paths=paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        runner=runner,
        engines=ServiceEngines(media=object(), youtube=object()),  # type: ignore[arg-type]
    )

    first = service.submit_youtube("youtu.be/dQw4w9WgXcQ")
    second = service.submit_youtube("https://www.youtube.com/shorts/abcdefghijk")

    assert second == first
    assert len(runner.jobs()) == 1


@pytest.mark.unit
def test_opening_a_youtube_recent_entry_resubmits_its_import(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    runner = JobRunner(paths=paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        runner=runner,
        engines=ServiceEngines(media=object(), youtube=object()),  # type: ignore[arg-type]
    )
    settings.record_recent(
        source_id="yt-dQw4w9WgXcQ",
        title="YouTube Song",
        origin="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        origin_kind=OriginKind.YOUTUBE,
    )

    job_id = service.open_recent("yt-dQw4w9WgXcQ")

    assert job_id is not None
    snapshot = runner.snapshot(job_id)
    assert snapshot.kind.value == "import_youtube"
    assert snapshot.state.value == "queued"


@pytest.mark.unit
def test_submit_youtube_refuses_a_playlist_before_any_job(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)

    with pytest.raises(StemForgeError) as raised:
        service.submit_youtube("https://www.youtube.com/playlist?list=PL123")

    assert raised.value.code == "import.youtube_playlist"
    assert service.runner.jobs() == []
