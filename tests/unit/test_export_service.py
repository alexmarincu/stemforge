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


def _commit_separation(store: ArtifactStore, *, gain_db: float = -2.0) -> None:
    staging = store.stage("separation-job")
    files = []
    for role in ("vocals", "drums", "bass", "other", "instrumental"):
        path = staging / f"{role}.wav"
        path.write_bytes(f"fake {role}".encode())
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
) -> str:
    staging = store.stage(f"render-job-{key}")
    files = []
    for role in roles:
        path = staging / f"{role}.wav"
        path.write_bytes(f"fake render {role}".encode())
        files.append(_entry(path, role))
    store.commit_render(
        staging,
        key,
        {
            "schema_version": 1,
            "kind": "render",
            "source_id": SOURCE_ID,
            "render_key": key,
            "created_at": datetime(2026, 11, 16, 10, minute, tzinfo=UTC).isoformat(),
            "scope": scope,
            "semitones": semitones,
            "gain_db": gain_db,
            "files": files,
        },
    )
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
    service, store, _, _workspace = _service(tmp_path)
    _commit_source(store, gain_db=-1.5)
    track = _add_track(store, _workspace)

    targets = service.export_targets(track)

    assert [target.key for target in targets] == ["master"]
    master = targets[0]
    assert isinstance(master, ExportJobTarget)
    assert master.label == "Original"
    assert master.base_name == TITLE
    assert master.gain_db == pytest.approx(-1.5)
    assert master.path == store.sources_dir / SOURCE_ID / "canonical.wav"


def test_export_targets_adds_unshifted_stems_and_the_instrumental(tmp_path: Path) -> None:
    service, store, _, _workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store, gain_db=-2.0)
    track = _add_track(store, _workspace)

    targets = service.export_targets(track)

    assert [target.key for target in targets] == [
        "master",
        "stem:vocals",
        "stem:drums",
        "stem:bass",
        "stem:other",
        "instrumental",
    ]
    stems = {target.key: target for target in targets}
    assert stems["stem:vocals"].label == "Vocals"
    assert stems["stem:vocals"].base_name == f"{TITLE} - Vocals"
    assert stems["instrumental"].base_name == f"{TITLE} - Instrumental"
    assert stems["instrumental"].gain_db == pytest.approx(-2.0)


def test_the_instrumental_is_an_output_not_a_stem_key(tmp_path: Path) -> None:
    service, store, _, _workspace = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    track = _add_track(store, _workspace)

    by_key = {target.key: target for target in service.export_targets(track)}

    assert by_key["instrumental"].role == "instrumental"
    assert resolve_export_target(store, source_id=SOURCE_ID, key="instrumental") is not None
    assert resolve_export_target(store, source_id=SOURCE_ID, key="stem:instrumental") is None


def test_export_targets_adds_every_member_of_a_pitch_set(tmp_path: Path) -> None:
    service, store, _, _workspace = _service(tmp_path)
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
    track = _add_track(store, _workspace)

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
        "render:render-stems:drums",
        "render:render-stems:bass",
        "render:render-stems:other",
        "render:render-stems:instrumental",
    ]


def test_export_targets_ignores_an_invalid_or_missing_render(tmp_path: Path) -> None:
    service, store, _, _workspace = _service(tmp_path)
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
    track = _add_track(store, _workspace)

    assert [target.key for target in service.export_targets(track)] == ["master"]


def test_export_targets_for_an_unknown_track_is_empty(tmp_path: Path) -> None:
    service, _, _, _workspace = _service(tmp_path)

    assert service.export_targets("missing-track") == ()


def test_resolve_export_target_refuses_a_role_that_the_set_does_not_have(
    tmp_path: Path,
) -> None:
    service, store, _, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)

    assert resolve_export_target(store, source_id=SOURCE_ID, key="stem:vocals") is None
    assert service.export_targets(track)[0].key == "master"


def test_submit_export_queues_one_export_job_with_the_selection_identity(
    tmp_path: Path,
) -> None:
    service, store, runner, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)
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
    service, store, runner, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)
    destination = tmp_path / "exports"
    destination.mkdir()

    service.submit_export(track, "master", "mp3_320", destination)

    assert runner.submitted[0].identity[2] == "master"
    assert runner.submitted[0].identity[3] == "mp3_320"


def test_submit_export_carries_the_dialog_chosen_name(tmp_path: Path) -> None:
    service, store, runner, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)
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
    service, store, runner, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)
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
    service, store, runner, _workspace = _service(tmp_path, with_media=False)
    _commit_source(store)
    track = _add_track(store, _workspace)

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
    service, store, _, _workspace = _service(tmp_path)
    _commit_source(store)
    track = _add_track(store, _workspace)
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
