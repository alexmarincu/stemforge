"""The Separation UI's application seams: sets, previews, export rows, gate.

The UI never derives a set key, guesses a stem role, or reads a manifest
itself; these tests pin the service contract it uses instead. Everything is
headless: the artifact store is real under ``tmp_path`` and no engine runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from stemforge import errors
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
    store: ArtifactStore,
    source_id: str = SOURCE_ID,
    *,
    version: str = "v1",
    method: str = "demucs",
) -> None:
    roles = (
        ("vocals", "instrumental")
        if method == "karaoke"
        else ("vocals", "instrumental", "drums", "bass", "other")
    )
    staging = store.stage(f"job-stems-{source_id}-{method}-{version}")
    files: list[Mapping[str, object]] = []
    for role in roles:
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
    store.commit_separation(staging, source_id, manifest, method=method)


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


class _UnusedModelStore(ModelStore):
    """A model store that fails the test if a Karaoke request reads it."""

    def require_verified(self) -> object:
        raise AssertionError("Karaoke read the model store")

    def status(self) -> object:
        raise AssertionError("Karaoke read the model store")

    def is_installed(self) -> bool:
        raise AssertionError("Karaoke read the model store")

    @property
    def spec(self) -> object:
        raise AssertionError("Karaoke read the model store")


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
    assert item.created_at.tzinfo is not None
    assert item.label == "Demucs"
    assert item.method == "demucs"
    assert item.method_version == 1
    assert item.output_roles == ("vocals", "instrumental", "drums", "bass", "other")
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
    assert vocals.url == f"/media/sources/{SOURCE_ID}/separations/demucs/vocals_preview.mp3"
    assert vocals.key != instrumental.key
    assert instrumental is not None
    assert instrumental.url.endswith("/instrumental_preview.mp3")
    assert drums is not None
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


@pytest.mark.unit
def test_a_legacy_stems_set_is_listed_as_the_demucs_set(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    legacy = store.sources_dir / SOURCE_ID / "stems"
    legacy.mkdir()
    vocals = b"legacy-vocals"
    preview = b"legacy-preview"
    (legacy / "vocals.wav").write_bytes(vocals)
    (legacy / "vocals_preview.mp3").write_bytes(preview)
    (legacy / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "separation",
                "source_id": SOURCE_ID,
                "created_at": "2026-11-16T12:00:00+00:00",
                "files": [
                    _file_entry("vocals.wav", "vocals", vocals),
                    _file_entry("vocals_preview.mp3", "vocals_preview", preview),
                ],
            }
        ),
        encoding="utf-8",
    )

    (item,) = service.separation_sets(_track())

    assert item.method == "demucs"
    assert item.output_roles == ("vocals", "instrumental", "drums", "bass", "other")
    target = service.separation_preview_target(_track(), item.set_key, "vocals")
    assert target is not None
    assert target.url.endswith("/stems/vocals_preview.mp3")


@pytest.mark.unit
def test_both_method_sets_are_listed_oldest_first_with_their_roles(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, version="demucs-v1")
    _commit_separation(store, version="karaoke-v1", method="karaoke")
    track = _track()

    demucs, karaoke = service.separation_sets(track)

    assert demucs.method == "demucs"
    assert demucs.output_roles == ("vocals", "instrumental", "drums", "bass", "other")
    assert karaoke.method == "karaoke"
    assert karaoke.method_version == 1
    assert karaoke.label == "Karaoke"
    assert karaoke.output_roles == ("vocals", "instrumental")
    assert karaoke.stem_roles == ("vocals",)
    assert demucs.set_key != karaoke.set_key
    assert service.has_separation(track) is True

    vocals = service.separation_preview_target(track, karaoke.set_key, "vocals")
    instrumental = service.separation_preview_target(track, karaoke.set_key, "instrumental")
    assert vocals is not None
    assert vocals.url.endswith("/separations/karaoke/vocals_preview.mp3")
    assert instrumental is not None
    assert service.separation_preview_target(track, karaoke.set_key, "drums") is None

    # A Demucs set key still resolves its own previews across the other method.
    assert service.separation_preview_target(track, demucs.set_key, "drums") is not None
    assert service.separation_preview_target(track, demucs.set_key, "vocals") is not None


@pytest.mark.unit
def test_re_running_one_method_leaves_the_other_set_and_key_intact(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, version="demucs-v1")
    _commit_separation(store, version="karaoke-v1", method="karaoke")
    demucs_before, karaoke_before = service.separation_sets(_track())

    _commit_separation(store, version="karaoke-v2", method="karaoke")
    demucs_after, karaoke_after = service.separation_sets(_track())

    assert demucs_after == demucs_before
    assert karaoke_after.method == "karaoke"
    assert karaoke_after.set_key != karaoke_before.set_key
    assert service.separation_preview_target(_track(), karaoke_before.set_key, "vocals") is None
    assert service.separation_preview_target(_track(), demucs_before.set_key, "vocals") is not None


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
    assert {target.role: target.label for target in targets} == {
        "vocals": "Vocals",
        "instrumental": "Instrumental",
        "drums": "Drums",
        "bass": "Bass",
        "other": "Other",
    }
    assert all(target.source_id == SOURCE_ID for target in targets)
    assert len({target.key for target in targets}) == 5
    assert item.set_key  # set enumeration and export enumeration agree on the source


# ---------------------------------------------------------------------------
# Method selection and persistence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_separation_methods_are_exposed_as_selector_descriptors(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)

    methods = service.separation_methods()

    assert [(item.id, item.label, item.requires_model) for item in methods] == [
        ("demucs", "Demucs", True),
        ("karaoke", "Karaoke", False),
    ]


@pytest.mark.unit
def test_without_sets_or_a_preference_auto_follows_the_model(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    models = ModelStore(paths, spec=_tiny_spec())
    models.install_bytes(PAYLOAD)
    service, store = _service(tmp_path, models=models)
    _commit_source(store)
    track = _track()

    assert service.separation_method(track) == "demucs"
    assert service.selected_separation_set(track) is None
    assert service.has_separation(track) is False


@pytest.mark.unit
def test_an_auto_karaoke_selection_survives_a_later_model_install(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    assert service.separation_method(track) == "karaoke"

    service.models.install_bytes(PAYLOAD)

    assert service.separation_method(track) == "karaoke"


@pytest.mark.unit
def test_the_persisted_preference_is_used_only_without_a_set(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    models = ModelStore(paths, spec=_tiny_spec())
    models.install_bytes(PAYLOAD)
    service, store = _service(tmp_path, models=models)
    _commit_source(store)
    track = _track()
    service.settings.set_separation_method("karaoke")

    assert service.separation_method(track) == "karaoke"

    _commit_separation(store)

    assert service.separation_method(track) == "demucs"


@pytest.mark.unit
def test_the_newest_set_wins_until_the_user_selects_another_method(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, version="demucs-v1")
    _commit_separation(store, version="karaoke-v1", method="karaoke")
    track = _track()

    assert service.separation_method(track) == "karaoke"
    assert service.selected_separation_set(track).method == "karaoke"

    service.select_separation_method(track, "demucs")

    assert service.separation_method(track) == "demucs"
    assert service.selected_separation_set(track).method == "demucs"
    assert service.has_separation(track) is True

    service.select_separation_method(track, "karaoke")

    assert service.selected_separation_set(track).method == "karaoke"


@pytest.mark.unit
def test_selecting_a_method_is_view_only_and_does_not_persist_or_submit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()
    service.settings.set_separation_method("demucs")
    jobs_before = service.runner.jobs()

    service.select_separation_method(track, "karaoke")

    assert service.separation_method(track) == "karaoke"
    assert service.settings.separation_method() == "demucs"
    assert service.runner.jobs() == jobs_before

    service.select_separation_method(track, "repet-sim")

    assert service.separation_method(track) == "demucs"
    assert service.settings.separation_method() == "demucs"
    assert "unknown Separation method" in caplog.text


@pytest.mark.unit
def test_an_explicit_selection_with_no_set_hides_the_other_methods_set(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="karaoke")
    track = _track()

    service.select_separation_method(track, "demucs")

    assert service.separation_method(track) == "demucs"
    assert service.selected_separation_set(track) is None
    assert service.has_separation(track) is False
    assert service.separation_sets(track)[0].method == "karaoke"


@pytest.mark.unit
def test_separate_resolves_the_selection_and_persists_it_before_submitting(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()
    service.select_separation_method(track, "karaoke")

    job_id = service.separate(track)

    assert [job.job_id for job in service.runner.jobs()] == [job_id]
    assert service.settings.separation_method() == "karaoke"


@pytest.mark.unit
def test_an_explicit_demucs_request_persists_before_the_model_gate(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError) as raised:
        service.separate(track, method="demucs")

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert service.settings.separation_method() == "demucs"
    assert service.runner.jobs() == []


@pytest.mark.unit
def test_installing_demucs_does_not_change_the_selected_method(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    models = ModelStore(paths, spec=_tiny_spec())
    service, store = _service(tmp_path, models=models)
    _commit_source(store)
    track = _track()
    service.select_separation_method(track, "karaoke")

    models.install_bytes(PAYLOAD)

    assert service.separation_method(track) == "karaoke"


@pytest.mark.unit
def test_removing_a_track_clears_its_view_selection(tmp_path: Path) -> None:
    workspace = Workspace()
    service, store = _service(tmp_path, workspace=workspace)
    _commit_source(store)
    track = _track()
    workspace.add_track(track)
    service.select_separation_method(track, "demucs")
    assert service.separation_method(track) == "demucs"

    service.remove_track(track.track_id)

    assert service.separation_method(track) == "karaoke"


# ---------------------------------------------------------------------------
# Separation submission and the once-only post-install resubmit
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_separate_requires_the_model_before_submitting_and_latches_once(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError) as raised:
        service.separate(track, method="demucs")

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
def test_the_latch_remembers_the_explicit_method_and_resubmits_it(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError):
        service.separate(track, method="demucs")

    assert service.pending_separation() == (TRACK_ID, "demucs")

    service.models.install_bytes(PAYLOAD)
    assert service.resubmit_pending_separation(track) is not None

    assert service.pending_separation() is None
    assert service.separation_method(track) == "demucs"


@pytest.mark.unit
def test_an_explicit_karaoke_request_clears_a_pending_demucs_latch(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError):
        service.separate(track, method="demucs")
    assert service.pending_separation() == (TRACK_ID, "demucs")

    service.separate(track, method="karaoke")

    assert service.pending_separation() is None

    service.models.install_bytes(PAYLOAD)
    assert service.resubmit_pending_separation(track) is None
    assert len(service.runner.jobs()) == 1


@pytest.mark.unit
def test_viewing_another_method_does_not_clear_the_pending_latch(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    track = _track()

    with pytest.raises(StemForgeError):
        service.separate(track, method="demucs")

    service.select_separation_method(track, "karaoke")

    assert service.pending_separation() == (TRACK_ID, "demucs")

    service.models.install_bytes(PAYLOAD)
    assert service.resubmit_pending_separation(track) is not None


@pytest.mark.unit
def test_resubmit_only_consumes_the_track_that_is_pending(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    pending = _track()
    other = _track(track_id=OTHER_TRACK_ID, source_id="other-source")

    with pytest.raises(StemForgeError):
        service.separate(pending, method="demucs")

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

    job_id = service.separate(track, method="demucs")

    jobs = service.runner.jobs()
    assert [job.job_id for job in jobs] == [job_id]
    assert jobs[0].kind.value == "separate"
    assert service.pending_separation_track_id() is None
    assert service.separate(track, method="demucs") == job_id


@pytest.mark.unit
def test_auto_without_the_model_chooses_karaoke_and_never_latches(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)

    job_id = service.separate(_track())

    assert [job.job_id for job in service.runner.jobs()] == [job_id]
    assert service.pending_separation_track_id() is None


@pytest.mark.unit
def test_karaoke_never_reads_the_model_store_or_latches(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    models = _UnusedModelStore(paths, spec=_tiny_spec())
    service, store = _service(tmp_path, models=models)
    _commit_source(store)

    job_id = service.separate(_track(), method="karaoke")

    assert [job.job_id for job in service.runner.jobs()] == [job_id]
    assert service.pending_separation_track_id() is None


@pytest.mark.unit
def test_unknown_methods_fall_back_to_auto_without_raising(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)

    job_id = service.separate(_track(), method="repet-sim")

    assert [job.job_id for job in service.runner.jobs()] == [job_id]


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
