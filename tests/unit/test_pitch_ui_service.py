"""The Pitch UI's application seams: tab order, scope lookup, set selection.

The UI never derives a render key or reads a manifest itself (ticket 11); these
tests pin the service contract it uses instead. Everything here is headless:
the artifact store is real under ``tmp_path`` and no engine runs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import (
    OriginKind,
    RenderScope,
    Source,
    SourceMetadata,
    Track,
)
from stemforge.domain.workspace import Workspace
from stemforge.engines.media import MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.runner import JobRunner
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

SOURCE_ID = "source-pitch-ui"
TRACK_ID = "track-pitch-ui"


# ---------------------------------------------------------------------------
# Artifact helpers
# ---------------------------------------------------------------------------


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
) -> dict[str, str]:
    """Commit four stems and return their role -> sha256 map."""
    _commit_source(store, source_id)
    staging = store.stage(f"job-stems-{source_id}-{version}")
    hashes: dict[str, str] = {}
    files: list[Mapping[str, object]] = []
    for role in ("vocals", "drums", "bass", "other"):
        data = f"{version}-{role}-audio".encode()
        name = f"{role}.wav"
        (staging / name).write_bytes(data)
        hashes[role] = hashlib.sha256(data).hexdigest()
        files.append(_file_entry(name, role, data))
        preview = f"{role}-preview-{version}".encode()
        preview_name = f"{role}_preview.mp3"
        (staging / preview_name).write_bytes(preview)
        files.append(_file_entry(preview_name, f"{role}_preview", preview))
    manifest = {
        "schema_version": 1,
        "kind": "separation",
        "source_id": source_id,
        "files": files,
    }
    store.commit_separation(staging, source_id, manifest)
    return hashes


def _commit_render(
    store: ArtifactStore,
    source_id: str,
    *,
    semitones: int,
    scope: str,
    created_at: datetime | None = None,
    inputs: Mapping[str, str] | None = None,
    tag: str | None = None,
) -> str:
    key = f"render-{scope}-{semitones:+d}".replace("+", "p").replace("-", "m")
    if tag is not None:
        key += f"-{tag}"
    staging = store.stage(f"job-render-{key}")
    files: list[Mapping[str, object]] = []
    if scope == "original":
        data = f"render-master-{semitones}".encode()
        (staging / "original.wav").write_bytes(data)
        files.append(_file_entry("original.wav", "original", data))
        preview = f"render-master-preview-{semitones}".encode()
        (staging / "original_preview.mp3").write_bytes(preview)
        files.append(_file_entry("original_preview.mp3", "original_preview", preview))
    else:
        for role in ("vocals", "drums", "bass", "other", "instrumental"):
            data = f"render-stems-{semitones}-{role}".encode()
            (staging / f"{role}.wav").write_bytes(data)
            files.append(_file_entry(f"{role}.wav", role, data))
            preview = f"render-stems-preview-{semitones}-{role}".encode()
            (staging / f"{role}_preview.mp3").write_bytes(preview)
            files.append(_file_entry(f"{role}_preview.mp3", f"{role}_preview", preview))
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "render",
        "render_key": key,
        "source_id": source_id,
        "scope": scope,
        "semitones": semitones,
        "gain_db": 0.0,
        "files": files,
    }
    if created_at is not None:
        manifest["created_at"] = created_at.isoformat()
        manifest["semitones"] = semitones
    if inputs is not None:
        manifest["inputs"] = [
            {"role": role, "artifact": f"stems/{role}.wav", "sha256": digest}
            for role, digest in inputs.items()
        ]
    store.commit_render(staging, key, manifest)
    return key


def _track(source_id: str = SOURCE_ID) -> Track:
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
    return Track(track_id=TRACK_ID, source=source, title="Song")


def _service(
    tmp_path: Path,
    *,
    engines: ServiceEngines | None = None,
    workspace: Workspace | None = None,
) -> tuple[WorkspaceService, ArtifactStore]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    settings = SettingsStore(paths)
    store = ArtifactStore(paths)
    service = WorkspaceService(
        settings,
        paths=paths,
        artifacts=store,
        runner=JobRunner(paths=paths),
        engines=engines if engines is not None else ServiceEngines(),
        workspace=workspace,
    )
    return service, store


# ---------------------------------------------------------------------------
# Tab order and labels
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pitch_sets_follow_creation_order_oldest_first(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    start = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    newest = _commit_render(
        store, SOURCE_ID, semitones=-2, scope="original", created_at=start + timedelta(minutes=2)
    )
    oldest = _commit_render(store, SOURCE_ID, semitones=3, scope="original", created_at=start)
    middle = _commit_render(
        store, SOURCE_ID, semitones=5, scope="original", created_at=start + timedelta(minutes=1)
    )

    sets = service.pitch_sets(_track())

    assert [item.render_key for item in sets] == [oldest, middle, newest]
    assert [item.semitones for item in sets] == [3, 5, -2]


@pytest.mark.unit
def test_a_single_scope_label_stays_bare_and_a_collision_adds_master_and_stems(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    _commit_render(store, SOURCE_ID, semitones=3, scope="original")

    assert [item.label for item in service.pitch_sets(_track())] == ["+3"]

    _commit_render(store, SOURCE_ID, semitones=3, scope="stems")

    labels = [item.label for item in service.pitch_sets(_track())]
    assert labels == ["+3 master", "+3 stems"]
    assert len(set(labels)) == 2


@pytest.mark.unit
def test_a_non_colliding_stems_set_is_not_relabelled(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    _commit_render(store, SOURCE_ID, semitones=4, scope="stems")

    (item,) = service.pitch_sets(_track())

    assert item.label == "+4"
    assert item.scope is RenderScope.STEMS


# ---------------------------------------------------------------------------
# Scope lookup and set selection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_scope_lookup_never_returns_the_other_scope_at_the_same_semitone(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    master = _commit_render(store, SOURCE_ID, semitones=3, scope="original")
    stems = _commit_render(store, SOURCE_ID, semitones=3, scope="stems")

    master_set = service.pitch_set(_track(), 3, RenderScope.ORIGINAL)
    stems_set = service.pitch_set(_track(), 3, "stems")

    assert master_set is not None
    assert master_set.render_key == master
    assert stems_set is not None
    assert stems_set.render_key == stems
    assert master_set.render_key != stems_set.render_key
    assert service.pitch_set(_track(), 4, RenderScope.ORIGINAL) is None


@pytest.mark.unit
def test_stems_availability_is_reported_for_the_scope_action(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)

    assert service.has_separation(_track()) is False
    _commit_separation(store)
    assert service.has_separation(_track()) is True


# ---------------------------------------------------------------------------
# Preview targets
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_master_set_previews_its_original_and_a_stems_set_its_instrumental(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_separation(store)
    master = _commit_render(store, SOURCE_ID, semitones=3, scope="original")
    stems = _commit_render(store, SOURCE_ID, semitones=3, scope="stems")
    track = _track()

    master_target = service.pitch_preview_target(track, master)
    stems_target = service.pitch_preview_target(track, stems)

    assert master_target is not None
    assert master_target.url == f"/media/renders/{master}/original_preview.mp3"
    assert stems_target is not None
    assert stems_target.url == f"/media/renders/{stems}/instrumental_preview.mp3"
    assert master_target.key != stems_target.key
    assert service.pitch_preview_target(track, "missing-render") is None


# ---------------------------------------------------------------------------
# Re-separation staleness
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_old_stems_sets_stay_listed_but_are_marked_and_not_offered_for_the_new_stems(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    first_hashes = _commit_separation(store, version="v1")
    old = _commit_render(
        store,
        SOURCE_ID,
        semitones=3,
        scope="stems",
        inputs=first_hashes,
    )
    fresh_before = service.pitch_set(_track(), 3, RenderScope.STEMS)
    assert fresh_before is not None
    assert fresh_before.render_key == old

    second_hashes = _commit_separation(store, version="v2")
    after_reseparation = service.pitch_sets(_track())
    assert [item.render_key for item in after_reseparation] == [old]
    assert after_reseparation[0].based_on_earlier_separation is True
    # The old set is no longer a cache hit for the current Stems, so Render is
    # offered instead of Open (ticket 05 §2, ticket 08 §5).
    assert service.pitch_set(_track(), 3, RenderScope.STEMS) is None

    new = _commit_render(
        store,
        SOURCE_ID,
        semitones=3,
        scope="stems",
        inputs=second_hashes,
        tag="v2",
    )
    sets = service.pitch_sets(_track())

    assert [item.render_key for item in sets] == [old, new]
    assert [item.based_on_earlier_separation for item in sets] == [True, False]
    selected = service.pitch_set(_track(), 3, RenderScope.STEMS)
    assert selected is not None
    assert selected.render_key == new


@pytest.mark.unit
def test_a_master_set_is_never_based_on_an_earlier_separation(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    _commit_render(store, SOURCE_ID, semitones=3, scope="original")
    _commit_separation(store, version="v1")

    (item,) = service.pitch_sets(_track())

    assert item.based_on_earlier_separation is False


# ---------------------------------------------------------------------------
# Render submission
# ---------------------------------------------------------------------------


class _FakePitch:
    version = "4.0.0-test"


class _FakeMedia:
    """Only used as an injected placeholder; no Job runs in these tests."""


@pytest.mark.unit
def test_render_pitch_submits_one_render_job_with_the_requested_scope(
    tmp_path: Path,
) -> None:
    service, store = _service(
        tmp_path, engines=ServiceEngines(pitch=_FakePitch(), media=_FakeMedia())
    )
    _commit_source(store)
    _commit_separation(store)

    job_id = service.render_pitch(_track(), 3, RenderScope.STEMS)

    jobs = service.runner.jobs()
    assert [job.job_id for job in jobs] == [job_id]
    assert jobs[0].kind.value == "render_pitch"
    assert jobs[0].state.value == "queued"
    assert jobs[0].title == "Song"


@pytest.mark.unit
def test_render_pitch_rejects_zero_and_stems_without_a_separation_before_submitting(
    tmp_path: Path,
) -> None:
    service, store = _service(
        tmp_path, engines=ServiceEngines(pitch=_FakePitch(), media=_FakeMedia())
    )
    _commit_source(store)

    with pytest.raises(ValueError, match="0 semitones"):
        service.render_pitch(_track(), 0, RenderScope.ORIGINAL)
    with pytest.raises(ValueError, match="stems are not available"):
        service.render_pitch(_track(), 3, RenderScope.STEMS)

    assert service.runner.jobs() == []


@pytest.mark.unit
def test_render_pitch_requires_a_configured_engine(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)

    with pytest.raises(StemForgeError, match="not configured") as raised:
        service.render_pitch(_track(), 3, RenderScope.ORIGINAL)

    assert raised.value.code == "internal.unexpected"
    assert service.runner.jobs() == []


class _BlockingPitch:
    """A Render that stays in ``shift`` until the Track removal cancels it."""

    version = "4.0.0"
    started = __import__("threading").Event()

    def shift(self, src, dst, *, semitones, cancel, log, progress=None):  # type: ignore[no-untyped-def]
        del src, dst, semitones, log, progress
        self.started.set()
        while True:
            cancel.check()
            __import__("time").sleep(0.005)

    def sum_stems(self, srcs, dst, *, cancel, log):  # type: ignore[no-untyped-def]
        raise AssertionError("not reached")

    def peak_dbfs(self, path, *, cancel, log):  # type: ignore[no-untyped-def]
        raise AssertionError("not reached")


class _ProbeMedia:
    def probe(self, path: Path) -> MediaInfo:
        del path
        return MediaInfo("pcm_f32le", 44_100, 2, 1.0, "wav", {})


@pytest.mark.unit
def test_removing_the_track_cancels_its_active_job_before_deleting_the_cache(
    tmp_path: Path,
) -> None:
    pitch = _BlockingPitch()
    workspace = Workspace()
    service, store = _service(
        tmp_path,
        engines=ServiceEngines(pitch=pitch, media=_ProbeMedia()),  # type: ignore[arg-type]
        workspace=workspace,
    )
    _commit_source(store)
    track = _track()
    workspace.add_track(track)
    service.runner.start()
    try:
        job_id = service.render_pitch(track, 3, RenderScope.ORIGINAL)
        assert pitch.started.wait(5)

        outcome = service.remove_track(track.track_id)

        assert outcome.track.track_id == track.track_id
        assert service.runner.snapshot(job_id).state.value == "cancelled"
        assert service.snapshot().tracks == ()
        assert not (store.sources_dir / SOURCE_ID).exists()
    finally:
        service.runner.stop(timeout=5)
