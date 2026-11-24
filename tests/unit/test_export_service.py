"""The Export service seams: target enumeration, submission, and result reads."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.application import ExportedFile, ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import (
    JobKind,
    JobState,
    OriginKind,
    Source,
    SourceMetadata,
    Track,
)
from stemforge.domain.workspace import Workspace
from stemforge.engines.media import ExportFormat, MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.jobs.models import JobSnapshot
from stemforge.pipeline.export_job import ExportTarget as ExportJobTarget
from stemforge.pipeline.export_job import resolve_export_target, run_export_job
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit

LOG = logging.getLogger("test.export-service")

SOURCE_ID = "src-export"
TITLE = "Song"
DEMUCS_ROLES = ("vocals", "instrumental", "drums", "bass", "other")
KARAOKE_ROLES = ("vocals", "instrumental")


def _entry(path: Path, role: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _commit_source(
    store: ArtifactStore,
    *,
    title: str = TITLE,
    gain_db: float = 0.0,
) -> str:
    staging = store.stage("source-job")
    canonical = staging / "canonical.wav"
    canonical.write_bytes(b"fake canonical audio")
    store.commit_source(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": SOURCE_ID,
            "created_at": datetime(2026, 11, 16, 9, 0, tzinfo=UTC).isoformat(),
            "gain_db": gain_db,
            "metadata": {
                "title": title,
                "artist": "Artist",
                "album": "Album",
                "track": "1",
                "date": "2026",
                "comment": "Comment",
                "duration_s": 120.0,
                "sample_rate": 44_100,
                "channels": 2,
            },
            "files": [_entry(canonical, "canonical")],
        },
    )
    return SOURCE_ID


def _commit_separation(
    store: ArtifactStore,
    *,
    method: str = "demucs",
    gain_db: float = -2.0,
) -> None:
    roles = KARAOKE_ROLES if method == "karaoke" else DEMUCS_ROLES
    staging = store.stage(f"separation-job-{method}")
    files = []
    for role in roles:
        path = staging / f"{role}.wav"
        path.write_bytes(f"fake {method} {role}".encode())
        files.append(_entry(path, role))
    store.commit_separation(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "separation",
            "source_id": SOURCE_ID,
            "created_at": datetime(2026, 11, 16, 9, 5, tzinfo=UTC).isoformat(),
            "gain_db": gain_db,
            "files": files,
        },
        method=method,
    )


def _commit_render(
    store: ArtifactStore,
    key: str,
    *,
    scope: str,
    semitones: int,
    roles: tuple[str, ...],
    gain_db: float,
    minute: int,
    separation_method: str | None = None,
    separation_set_key: str | None = None,
) -> str:
    staging = store.stage(f"render-job-{key}")
    files = []
    for role in roles:
        path = staging / f"{role}.wav"
        path.write_bytes(f"fake render {key} {role}".encode())
        files.append(_entry(path, role))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "render",
        "source_id": SOURCE_ID,
        "render_key": key,
        "created_at": datetime(2026, 11, 16, 10, minute, tzinfo=UTC).isoformat(),
        "scope": scope,
        "semitones": semitones,
        "gain_db": gain_db,
        "files": files,
    }
    if separation_method is not None:
        manifest["separation_method"] = separation_method
    if separation_set_key is not None:
        manifest["separation_set_key"] = separation_set_key
    store.commit_render(staging, key, manifest)
    return key


class RecordingRunner:
    """A submit/snapshot double; no Job ever executes in these tests."""

    def __init__(self) -> None:
        self.submitted = []
        self.snapshot_value: JobSnapshot | None = None
        self.next_id = "job-export"

    def submit(self, job: object) -> str:
        self.submitted.append(job)
        return self.next_id

    def snapshot(self, job_id: str) -> JobSnapshot:
        if self.snapshot_value is None or self.snapshot_value.job_id != job_id:
            raise KeyError(job_id)
        return self.snapshot_value


def _service(
    tmp_path: Path,
    *,
    with_media: bool = True,
) -> tuple[WorkspaceService, ArtifactStore, RecordingRunner, Workspace]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    runner = RecordingRunner()
    workspace = Workspace()
    media = object() if with_media else None
    service = WorkspaceService(
        SettingsStore(paths),
        paths=paths,
        artifacts=store,
        runner=runner,  # type: ignore[arg-type]
        engines=ServiceEngines(media=media),  # type: ignore[arg-type]
        workspace=workspace,
    )
    return service, store, runner, workspace


def _add_track(
    store: ArtifactStore,
    workspace: Workspace,
    *,
    title: str = TITLE,
) -> Track:
    artifact = store.find_source(SOURCE_ID)
    assert artifact is not None
    source = Source(
        source_id=SOURCE_ID,
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(title=title, artist="Artist", duration_s=120.0),
        canonical_path=artifact.canonical_path,
        created_at=artifact.created_at,
    )
    track = Track(track_id="track-export", source=source, title=title)
    workspace.add_track(track)
    return track


def test_export_targets_lists_only_the_master_for_a_plain_source(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store, gain_db=-1.5)
    track = _add_track(store, workspace)

    targets = service.export_targets(track)

    assert [target.key for target in targets] == ["master"]
    master = targets[0]
    assert isinstance(master, ExportJobTarget)
    assert master.label == "Original"
    assert master.base_name == TITLE
    assert master.gain_db == pytest.approx(-1.5)
    assert master.path == store.sources_dir / SOURCE_ID / "canonical.wav"


def test_export_targets_adds_unshifted_stems_and_the_instrumental(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, gain_db=-2.0)
    track = _add_track(store, workspace)

    targets = service.export_targets(track)

    assert [target.key for target in targets] == [
        "master",
        *(f"separation:demucs:{role}" for role in DEMUCS_ROLES),
    ]
    stems = {target.key: target for target in targets}
    assert stems["separation:demucs:vocals"].label == "Vocals"
    assert stems["separation:demucs:vocals"].base_name == f"{TITLE} - Vocals"
    assert stems["separation:demucs:instrumental"].base_name == f"{TITLE} - Instrumental"
    assert stems["separation:demucs:instrumental"].gain_db == pytest.approx(-2.0)
    assert stems["separation:demucs:vocals"].separation_method == "demucs"


def test_the_instrumental_is_an_output_not_a_stem_key(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    track = _add_track(store, workspace)

    by_key = {target.key: target for target in service.export_targets(track)}

    assert by_key["separation:demucs:instrumental"].role == "instrumental"
    assert (
        resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:instrumental")
        is not None
    )
    assert (
        resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:original") is None
    )
    assert resolve_export_target(store, source_id=SOURCE_ID, key="stem:instrumental") is None


def test_separation_keys_name_the_method_and_legacy_keys_resolve_as_demucs(
    tmp_path: Path,
) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs", gain_db=-2.0)
    _commit_separation(store, method="karaoke", gain_db=-4.0)
    track = _add_track(store, workspace)

    demucs = resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:vocals")
    karaoke = resolve_export_target(store, source_id=SOURCE_ID, key="separation:karaoke:vocals")

    assert demucs is not None
    assert karaoke is not None
    assert demucs.separation_method == "demucs"
    assert karaoke.separation_method == "karaoke"
    assert demucs.path.read_bytes() == b"fake demucs vocals"
    assert karaoke.path.read_bytes() == b"fake karaoke vocals"
    sets = {item.method: item.set_key for item in service.separation_sets(track)}
    assert demucs.set_key == sets["demucs"]
    assert karaoke.set_key == sets["karaoke"]
    assert demucs.set_key != karaoke.set_key

    assert resolve_export_target(store, source_id=SOURCE_ID, key="separation:karaoke:drums") is None
    assert (
        resolve_export_target(store, source_id=SOURCE_ID, key="separation:unknown:vocals") is None
    )
    assert resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs") is None
    assert (
        resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:vocals:extra")
        is None
    )

    legacy_stem = resolve_export_target(store, source_id=SOURCE_ID, key="stem:vocals")
    legacy_instrumental = resolve_export_target(store, source_id=SOURCE_ID, key="instrumental")
    assert legacy_stem is not None
    assert legacy_instrumental is not None
    assert legacy_stem.separation_method == "demucs"
    assert legacy_stem.path.read_bytes() == b"fake demucs vocals"
    assert legacy_instrumental.role == "instrumental"
    assert legacy_instrumental.path.read_bytes() == b"fake demucs instrumental"


def test_export_targets_list_only_the_selected_methods_separation_set(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs")
    _commit_separation(store, method="karaoke")
    track = _add_track(store, workspace)

    service.select_separation_method(track, "karaoke")
    karaoke = [target for target in service.export_targets(track) if target.scope == "separation"]
    assert [target.key for target in karaoke] == [
        "separation:karaoke:vocals",
        "separation:karaoke:instrumental",
    ]
    assert {target.separation_method for target in karaoke} == {"karaoke"}
    assert all(target.path.read_bytes().startswith(b"fake karaoke") for target in karaoke)

    service.select_separation_method(track, "demucs")
    demucs = [target for target in service.export_targets(track) if target.scope == "separation"]
    assert [target.key for target in demucs] == [
        *(f"separation:demucs:{role}" for role in DEMUCS_ROLES)
    ]
    assert {target.separation_method for target in demucs} == {"demucs"}
    assert all(target.path.read_bytes().startswith(b"fake demucs") for target in demucs)


def test_another_methods_render_sets_stay_listed_with_collision_only_qualifiers(
    tmp_path: Path,
) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs")
    _commit_separation(store, method="karaoke")
    demucs_render = _commit_render(
        store,
        "render-demucs-stems",
        scope="stems",
        semitones=-2,
        roles=DEMUCS_ROLES,
        gain_db=-3.0,
        minute=1,
    )
    karaoke_render = _commit_render(
        store,
        "render-karaoke-stems",
        scope="stems",
        semitones=-2,
        roles=KARAOKE_ROLES,
        gain_db=-4.0,
        minute=2,
        separation_method="karaoke",
    )
    original_render = _commit_render(
        store,
        "render-original",
        scope="original",
        semitones=3,
        roles=("original",),
        gain_db=-1.0,
        minute=3,
    )
    track = _add_track(store, workspace)
    service.select_separation_method(track, "karaoke")

    targets = service.export_targets(track)
    by_key = {target.key: target for target in targets}

    assert "separation:karaoke:vocals" in by_key
    assert not any(key.startswith("separation:demucs") for key in by_key)
    assert f"render:{demucs_render}:drums" in by_key
    assert f"render:{karaoke_render}:vocals" in by_key
    assert f"render:{original_render}:original" in by_key

    assert by_key[f"render:{demucs_render}:vocals"].separation_method == "demucs"
    assert by_key[f"render:{karaoke_render}:vocals"].separation_method == "karaoke"
    assert by_key[f"render:{demucs_render}:vocals"].label == "Vocals -2 st (Demucs)"
    assert by_key[f"render:{karaoke_render}:vocals"].label == "Vocals -2 st (Karaoke)"
    assert by_key[f"render:{demucs_render}:drums"].label == "Drums -2 st"
    labels = [target.label for target in targets]
    assert len(labels) == len(set(labels))

    assert by_key[f"render:{demucs_render}:vocals"].base_name == (f"{TITLE} - Vocals (Demucs) -2st")
    assert by_key[f"render:{karaoke_render}:vocals"].base_name == (
        f"{TITLE} - Vocals (Karaoke) -2st"
    )
    assert by_key[f"render:{original_render}:original"].base_name == f"{TITLE} +3st"
    assert by_key["separation:karaoke:vocals"].base_name == f"{TITLE} - Vocals (Karaoke)"
    assert by_key["separation:karaoke:instrumental"].base_name == (
        f"{TITLE} - Instrumental (Karaoke)"
    )


def test_an_unknown_render_method_is_read_as_demucs(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs")
    render = _commit_render(
        store,
        "render-unknown-method",
        scope="stems",
        semitones=2,
        roles=DEMUCS_ROLES,
        gain_db=-3.0,
        minute=4,
        separation_method="repet-sim",
    )
    track = _add_track(store, workspace)

    targets = service.export_targets(track)
    rendered = [target for target in targets if target.scope == "stems"]

    assert [target.key for target in rendered] == [
        f"render:{render}:{role}" for role in DEMUCS_ROLES
    ]
    assert {target.separation_method for target in rendered} == {"demucs"}


@pytest.mark.parametrize("method", ["demucs", "karaoke"])
def test_one_separation_set_keeps_unqualified_labels_and_names(tmp_path: Path, method: str) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method=method)
    track = _add_track(store, workspace)

    targets = service.export_targets(track)

    assert all("(" not in target.label for target in targets)
    assert all("(" not in target.base_name for target in targets)
    vocals = next(target for target in targets if target.key == f"separation:{method}:vocals")
    assert vocals.label == "Vocals"
    assert vocals.base_name == f"{TITLE} - Vocals"


def test_export_targets_adds_every_member_of_a_pitch_set(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_render(
        store,
        "render-original",
        scope="original",
        semitones=3,
        roles=("original",),
        gain_db=-1.0,
        minute=1,
    )
    _commit_render(
        store,
        "render-stems",
        scope="stems",
        semitones=-2,
        roles=("vocals", "drums", "bass", "other", "instrumental"),
        gain_db=-3.0,
        minute=2,
    )
    track = _add_track(store, workspace)

    targets = service.export_targets(track)

    by_key = {target.key: target for target in targets}
    assert set(by_key) == {
        "master",
        "render:render-original:original",
        "render:render-stems:vocals",
        "render:render-stems:drums",
        "render:render-stems:bass",
        "render:render-stems:other",
        "render:render-stems:instrumental",
    }
    assert by_key["render:render-original:original"].base_name == f"{TITLE} +3st"
    assert by_key["render:render-stems:vocals"].base_name == f"{TITLE} - Vocals -2st"
    assert by_key["render:render-stems:vocals"].semitones == -2
    assert by_key["render:render-stems:vocals"].gain_db == pytest.approx(-3.0)
    # Newest render set first, after the live master.
    render_keys = [target.key for target in targets if target.key.startswith("render:")]
    assert render_keys[:5] == [
        "render:render-stems:vocals",
        "render:render-stems:instrumental",
        "render:render-stems:drums",
        "render:render-stems:bass",
        "render:render-stems:other",
    ]


def test_export_targets_ignores_an_invalid_or_missing_render(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_render(
        store,
        "render-original",
        scope="original",
        semitones=1,
        roles=("original",),
        gain_db=0.0,
        minute=1,
    )
    (store.renders_dir / "render-original" / "original.wav").unlink()
    track = _add_track(store, workspace)

    assert [target.key for target in service.export_targets(track)] == ["master"]


def test_export_targets_for_an_unknown_track_is_empty(tmp_path: Path) -> None:
    service, _, _, _workspace = _service(tmp_path)

    assert service.export_targets("missing-track") == ()


def test_resolve_export_target_refuses_a_role_that_the_set_does_not_have(
    tmp_path: Path,
) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)

    assert resolve_export_target(store, source_id=SOURCE_ID, key="stem:vocals") is None
    assert resolve_export_target(store, source_id=SOURCE_ID, key="instrumental") is None
    assert resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:vocals") is None
    assert service.export_targets(track)[0].key == "master"


def test_submit_export_queues_one_export_job_with_the_selection_identity(
    tmp_path: Path,
) -> None:
    service, store, runner, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)
    target = service.export_targets(track)[0]
    destination = tmp_path / "exports"
    destination.mkdir()

    job_id = service.submit_export(track, target, ExportFormat.WAV24, destination)

    assert job_id == "job-export"
    assert len(runner.submitted) == 1
    job = runner.submitted[0]
    assert job.kind is JobKind.EXPORT
    assert job.title == TITLE
    assert job.identity == (
        JobKind.EXPORT.value,
        track.track_id,  # type: ignore[attr-defined]
        "master",
        ExportFormat.WAV24.value,
        str(destination),
        "-",
    )


def test_submit_export_accepts_a_target_key_and_string_format(tmp_path: Path) -> None:
    service, store, runner, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)
    destination = tmp_path / "exports"
    destination.mkdir()

    service.submit_export(track, "master", "mp3_320", destination)

    assert runner.submitted[0].identity[2] == "master"
    assert runner.submitted[0].identity[3] == "mp3_320"


def test_submit_export_queues_the_method_scoped_key_for_the_selected_set(
    tmp_path: Path,
) -> None:
    service, store, runner, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs")
    _commit_separation(store, method="karaoke")
    track = _add_track(store, workspace)
    service.select_separation_method(track, "karaoke")
    destination = tmp_path / "exports"
    destination.mkdir()

    service.submit_export(track, "separation:karaoke:vocals", ExportFormat.FLAC24, destination)

    assert runner.submitted[0].identity[2] == "separation:karaoke:vocals"


def test_preview_export_uses_the_method_qualified_name_when_two_sets_exist(
    tmp_path: Path,
) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, method="demucs")
    _commit_separation(store, method="karaoke")
    track = _add_track(store, workspace)
    destination = tmp_path / "exports"
    destination.mkdir()

    preview = service.preview_export(track, "separation:karaoke:vocals", "wav24", destination)

    assert preview == destination / f"{TITLE} - Vocals (Karaoke).wav"
    assert not preview.exists()


def test_submit_export_carries_the_dialog_chosen_name(tmp_path: Path) -> None:
    service, store, runner, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)
    destination = tmp_path / "exports"
    destination.mkdir()

    service.submit_export(
        track,
        "master",
        ExportFormat.FLAC24,
        destination,
        destination_name="My Mix - Vocals.flac",
    )

    assert runner.submitted[0].identity == (
        JobKind.EXPORT.value,
        track.track_id,  # type: ignore[attr-defined]
        "master",
        ExportFormat.FLAC24.value,
        str(destination),
        "My Mix - Vocals.flac",
    )


def test_submit_export_refuses_a_target_that_does_not_belong_to_the_track(
    tmp_path: Path,
) -> None:
    service, store, runner, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)
    target = resolve_export_target(store, source_id=SOURCE_ID, key="master")
    assert target is not None
    foreign = ExportJobTarget(
        source_id="another-source",
        key="master",
        label="Original",
        role="original",
        path=target.path,
        gain_db=0.0,
        semitones=0,
        scope="master",
        render_key=None,
        title=TITLE,
    )

    with pytest.raises(StemForgeError) as raised:
        service.submit_export(
            track,
            foreign,
            ExportFormat.WAV24,
            tmp_path / "exports",
        )

    assert raised.value.code == errors.MEDIA_PROBE_FAILED
    assert runner.submitted == []


def test_submit_export_needs_a_media_engine(tmp_path: Path) -> None:
    service, store, runner, workspace = _service(tmp_path, with_media=False)
    _commit_source(store)
    track = _add_track(store, workspace)

    with pytest.raises(StemForgeError) as raised:
        service.submit_export(track, "master", ExportFormat.WAV24, tmp_path / "exports")

    assert raised.value.code == errors.INTERNAL_UNEXPECTED
    assert runner.submitted == []


def test_export_result_reads_the_finished_job_keys(tmp_path: Path) -> None:
    service, _, runner, _workspace = _service(tmp_path)
    destination = tmp_path / "exports"
    destination.mkdir()
    output = destination / "Song.wav"
    output.write_bytes(b"exported")
    runner.snapshot_value = JobSnapshot(
        job_id="job-export",
        kind=JobKind.EXPORT,
        state=JobState.SUCCEEDED,
        progress=1.0,
        stage="Writing file",
        result_keys={
            "path": str(output),
            "format": "wav24",
            "size": str(len(b"exported")),
            "duration_s": "120.0",
            "gain_db": "-1.5",
        },
    )

    result = service.export_result("job-export")

    assert isinstance(result, ExportedFile)
    assert result.path == output
    assert result.format is ExportFormat.WAV24
    assert result.size_bytes == 8
    assert result.duration_s == pytest.approx(120.0)
    assert result.gain_db == pytest.approx(-1.5)


def test_export_result_is_none_while_the_job_is_unfinished(tmp_path: Path) -> None:
    service, _, runner, _workspace = _service(tmp_path)
    runner.snapshot_value = JobSnapshot(
        job_id="job-export",
        kind=JobKind.EXPORT,
        state=JobState.RUNNING,
    )

    assert service.export_result("job-export") is None


def test_export_result_returns_none_for_an_unknown_job(tmp_path: Path) -> None:
    service, _, _, _workspace = _service(tmp_path)

    assert service.export_result("missing") is None


def test_preview_export_returns_the_next_free_name_without_writing(tmp_path: Path) -> None:
    service, store, _, workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, workspace)
    destination = tmp_path / "exports"
    destination.mkdir()
    (destination / f"{TITLE}.wav").write_bytes(b"already here")

    preview = service.preview_export(track, "master", "wav24", destination)

    assert preview == destination / f"{TITLE} (2).wav"
    assert not preview.exists()
    assert list(destination.iterdir()) == [destination / f"{TITLE}.wav"]


class _TaglessMedia:
    """A fake adapter whose encoded output loses every record tag."""

    def probe(self, path: Path) -> MediaInfo:
        encoded = path.suffix == ".flac"
        return MediaInfo(
            codec="flac" if encoded else "pcm_f32le",
            sample_rate=44_100,
            channels=2,
            duration_s=2.0,
            format="flac" if encoded else "wav",
            tags={},
            bit_depth=24 if encoded else None,
        )

    def encode_export(self, src: Path, dst: Path, **kwargs: object) -> None:
        dst.write_bytes(b"encoded without tags")


def test_a_tag_verification_failure_is_coded_and_leaves_no_file(tmp_path: Path) -> None:
    store = ArtifactStore(AppPaths.for_home(tmp_path / "home").ensure())
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()

    def update(fraction: float, stage: str | None, detail: str | None) -> None:
        pass

    context = JobContext(
        job_id="job-tags",
        kind=JobKind.EXPORT,
        cancel=CancelToken(),
        log=LOG,
        update=update,
    )

    with pytest.raises(StemForgeError) as raised:
        run_export_job(
            context,
            artifacts=store,
            media=_TaglessMedia(),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            target="master",
            fmt=ExportFormat.FLAC24,
            destination_dir=destination,
        )

    assert raised.value.code == errors.MEDIA_VALIDATION_FAILED
    assert raised.value.detail == "tags"
    assert list(destination.iterdir()) == []
    assert not (store.staging_dir / "job-tags").exists()


def test_mp3_verification_covers_every_tag_field_the_writer_writes(
    tmp_path: Path,
) -> None:
    from stemforge.engines.media import CanonicalMetadata, MediaInfo
    from stemforge.pipeline import export_job

    metadata = CanonicalMetadata(
        title="Title",
        artist="Artist",
        album="Album",
        album_artist="Album Artist",
        track="1",
        disc="1",
        date="2026",
        genre="Genre",
        composer="Composer",
        copyright="Copyright",
    )
    written = dict(metadata.tags())
    reported = {
        key: value
        for key, value in written.items()
        if key not in {"album_artist", "composer", "disc"}
    }
    info = MediaInfo(
        codec="mp3",
        sample_rate=44_100,
        channels=2,
        duration_s=1.0,
        format="mp3",
        tags=reported,
    )

    class FakeMedia:
        def probe(self, path: Path) -> MediaInfo:
            return info

    with pytest.raises(StemForgeError) as raised:
        export_job._verify_output(
            FakeMedia(),  # type: ignore[arg-type]
            tmp_path / "out.mp3",
            export_format=ExportFormat.MP3_320,
            metadata=metadata,
        )

    assert raised.value.code == errors.MEDIA_VALIDATION_FAILED
    assert set(raised.value.context["fields"]) >= {"album_artist", "composer", "disc"}
