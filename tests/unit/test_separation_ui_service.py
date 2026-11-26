"""The Separation UI's application seams: sets, previews, export rows, gate.

The UI never derives a set key, guesses a stem role, or reads a manifest
itself; these tests pin the service contract it uses instead. Everything is
headless: the artifact store is real under ``tmp_path`` and no engine runs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from stemforge import errors, i18n
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import (
    OriginKind,
    Source,
    SourceMetadata,
    Track,
)
from stemforge.domain.workspace import Workspace
from stemforge.errors import StemForgeError
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelSpec, ModelStore
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

SOURCE_ID = "source-separation-ui"
TRACK_ID = "track-separation-ui"
OTHER_TRACK_ID = "track-other"

PAYLOAD = bytes(range(256)) * 16
PAYLOAD_SHA256 = "c8f5d0341d54d951a71b136e6e2afcb14d11ed8489a7ae126a8fee0df6ecf193"


def _tiny_spec() -> ModelSpec:
    return ModelSpec(
        name="htdemucs",
        repo_id="example/HTDemucs",
        revision="test-revision",
        filename="test-weights.safetensors",
        size_bytes=len(PAYLOAD),
        sha256=PAYLOAD_SHA256,
        download_url="https://example.invalid/models/test-weights.safetensors",
        source_url="https://example.invalid/models/test-weights.safetensors",
        licence="test licence",
        licence_notice="test notice",
        licence_notice_version=1,
    )


def _file_entry(name: str, role: str, data: bytes) -> Mapping[str, object]:
    return {
        "path": name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _commit_source(store: ArtifactStore, source_id: str = SOURCE_ID) -> None:
    staging = store.stage(f"job-source-{source_id}")
    data = b"canonical-audio"
    (staging / "canonical.wav").write_bytes(data)
    (staging / "original_preview.mp3").write_bytes(b"original-preview")
    manifest = {
        "schema_version": 1,
        "kind": "source",
        "source_id": source_id,
        "metadata": {"title": "Song", "artist": "Artist"},
        "files": [
            _file_entry("canonical.wav", "canonical", data),
            _file_entry("original_preview.mp3", "preview", b"original-preview"),
        ],
    }
    store.commit_source(staging, source_id, manifest)


def _commit_separation(
    store: ArtifactStore, source_id: str = SOURCE_ID, *, version: str = "v1"
) -> None:
    staging = store.stage(f"job-stems-{source_id}-{version}")
    files: list[Mapping[str, object]] = []
    for role in ("vocals", "drums", "bass", "other", "instrumental"):
        data = f"{version}-{role}-audio".encode()
        (staging / f"{role}.wav").write_bytes(data)
        files.append(_file_entry(f"{role}.wav", role, data))
        preview = f"{role}-preview-{version}".encode()
        (staging / f"{role}_preview.mp3").write_bytes(preview)
        files.append(_file_entry(f"{role}_preview.mp3", f"{role}_preview", preview))
    manifest = {
        "schema_version": 1,
        "kind": "separation",
        "source_id": source_id,
        "files": files,
    }
    store.commit_separation(staging, source_id, manifest)


def _track(track_id: str = TRACK_ID, source_id: str = SOURCE_ID) -> Track:
    source = Source(
        source_id=source_id,
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(
            title="Song",
            duration_s=180.0,
            sample_rate=44_100,
            channels=2,
        ),
    )
    return Track(track_id=track_id, source=source, title="Song")


class _FakeSeparation:
    """Only used as an injected placeholder; no Job runs in these tests."""


class _FakeMedia:
    """Only used as an injected placeholder; no Job runs in these tests."""


def _service(
    tmp_path: Path,
    *,
    models: ModelStore | None = None,
    workspace: Workspace | None = None,
) -> tuple[WorkspaceService, ArtifactStore]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    store = ArtifactStore(paths)
    model_store = models if models is not None else ModelStore(paths, spec=_tiny_spec())
    service = WorkspaceService(
        settings,
        paths=paths,
        artifacts=store,
        runner=JobRunner(paths=paths),
        engines=ServiceEngines(separation=_FakeSeparation(), media=_FakeMedia()),
        models=model_store,
        workspace=workspace,
    )
    return service, store


# ---------------------------------------------------------------------------
# Set enumeration and preview lookup
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_separation_sets_list_the_committed_set_with_ordered_stem_roles(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    track = _track()

    (item,) = service.separation_sets(track)

    assert item.set_key
    assert item.label.key == "separation.title"
    assert item.created_at.tzinfo is not None
    assert item.stem_roles == ("vocals", "drums", "bass", "other")
    assert item.based_on_earlier_separation is False


@pytest.mark.unit
def test_no_separation_yields_no_sets_and_no_preview(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)

    assert service.separation_sets(_track()) == ()
    assert service.separation_preview_target(_track(), "missing", "vocals") is None


@pytest.mark.unit
def test_each_stem_and_the_instrumental_have_their_own_preview_target(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    track = _track()
    (item,) = service.separation_sets(track)

    vocals = service.separation_preview_target(track, item.set_key, "vocals")
    instrumental = service.separation_preview_target(track, item.set_key, "instrumental")
    drums = service.separation_preview_target(track, item.set_key, "drums")

    assert vocals is not None
    assert vocals.url == f"/media/sources/{SOURCE_ID}/stems/vocals_preview.mp3"
    assert vocals.label.key == "role.vocals"
    assert vocals.key != instrumental.key
    assert instrumental is not None
    assert instrumental.url.endswith("/instrumental_preview.mp3")
    assert instrumental.label.key == "role.instrumental"
    assert drums is not None
    assert drums.label.key == "role.drums"
    assert service.separation_preview_target(track, item.set_key, "banjo") is None


@pytest.mark.unit
def test_re_separation_replaces_the_set_and_its_key(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, version="v1")
    (first,) = service.separation_sets(_track())

    _commit_separation(store, version="v2")
    (second,) = service.separation_sets(_track())

    assert second.set_key != first.set_key
    assert service.separation_preview_target(_track(), first.set_key, "vocals") is None
    assert service.separation_preview_target(_track(), second.set_key, "vocals") is not None


# ---------------------------------------------------------------------------
# Export row enumeration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_export_targets_cover_vocals_instrumental_and_the_three_secondary_stems(
    tmp_path: Path,
) -> None:
    workspace = Workspace()
    service, store = _service(tmp_path, workspace=workspace)
    _commit_source(store)
    _commit_separation(store)
    track = _track()
    workspace.add_track(track)
    (item,) = service.separation_sets(track)

    targets = [target for target in service.export_targets(track) if target.scope == "separation"]

    assert {target.role for target in targets} == {
        "vocals",
        "instrumental",
        "drums",
        "bass",
        "other",
    }
    assert {target.role: i18n.t(target.label.key) for target in targets} == {
        "vocals": "Vocals",
        "instrumental": "Instrumental",
        "drums": "Drums",
        "bass": "Bass",
        "other": "Other",
    }
    assert {target.role: target.label.key for target in targets} == {
        "vocals": "role.vocals",
        "instrumental": "role.instrumental",
        "drums": "role.drums",
        "bass": "role.bass",
        "other": "role.other",
    }
    assert all(target.source_id == SOURCE_ID for target in targets)
    assert len({target.key for target in targets}) == 5
    assert item.set_key  # set enumeration and export enumeration agree on the source


# ---------------------------------------------------------------------------
# Separation submission and the once-only post-install resubmit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_separate_requires_the_model_before_submitting_and_latches_once(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError) as raised:
        service.separate(track)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.detail == "reason=absent"
    assert service.runner.jobs() == []
    assert service.pending_separation_track_id() == TRACK_ID

    service.models.install_bytes(PAYLOAD)
    job_id = service.resubmit_pending_separation(track)

    assert job_id is not None
    jobs = service.runner.jobs()
    assert [job.job_id for job in jobs] == [job_id]
    assert jobs[0].kind.value == "separate"
    assert service.pending_separation_track_id() is None
    assert service.resubmit_pending_separation(track) is None


@pytest.mark.unit
def test_resubmit_only_consumes_the_track_that_is_pending(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    pending = _track()
    other = _track(track_id=OTHER_TRACK_ID, source_id="other-source")

    with pytest.raises(StemForgeError):
        service.separate(pending)

    assert service.resubmit_pending_separation(other) is None
    assert service.pending_separation_track_id() == pending.track_id


@pytest.mark.unit
def test_separate_with_the_model_installed_submits_one_job_and_never_latches(
    tmp_path: Path,
) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    models = ModelStore(paths, spec=_tiny_spec())
    models.install_bytes(PAYLOAD)
    service, store = _service(tmp_path, models=models)
    _commit_source(store)
    track = _track()

    job_id = service.separate(track)

    jobs = service.runner.jobs()
    assert [job.job_id for job in jobs] == [job_id]
    assert jobs[0].kind.value == "separate"
    assert service.pending_separation_track_id() is None
    assert service.separate(track) == job_id


@pytest.mark.unit
def test_a_job_level_model_missing_can_latch_the_pending_resubmit(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    service.remember_pending_separation(track.track_id)

    assert service.pending_separation_track_id() == track.track_id
    with pytest.raises(StemForgeError) as raised:
        service.resubmit_pending_separation(track)
    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert service.pending_separation_track_id() is None
    assert service.runner.jobs() == []


@pytest.mark.unit
def test_a_missing_engine_fails_without_latching_or_submitting(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    service = WorkspaceService(
        SettingsStore(paths),
        paths=paths,
        artifacts=ArtifactStore(paths),
        runner=JobRunner(paths=paths),
        engines=ServiceEngines(),
    )

    with pytest.raises(StemForgeError) as raised:
        service.separate(_track())

    assert raised.value.code == errors.INTERNAL_UNEXPECTED
    assert service.runner.jobs() == []
    assert service.pending_separation_track_id() is None
