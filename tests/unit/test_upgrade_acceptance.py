"""The v0.1.0 cache upgrade invariant (ADR 0015).

A seeded legacy ``sources/<id>/stems/`` Demucs set and a legacy stems Render
must keep listing, previewing, and exporting. A Karaoke set coexists beside
them, and re-running one method replaces only that method's set; promoting a new
Demucs set is the only thing that removes the legacy directory.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import OriginKind, Source, SourceMetadata, Track
from stemforge.domain.workspace import Workspace
from stemforge.jobs.runner import JobRunner
from stemforge.pipeline.export_job import resolve_export_target
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.unit

SOURCE_ID = "legacy-source"
TRACK_ID = "legacy-track"
DEMUCS_ROLES = ("vocals", "instrumental", "drums", "bass", "other")
KARAOKE_ROLES = ("vocals", "instrumental")
BASE_TIME = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def _entry(path: Path, role: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _manifest(directory: Path, *, kind: str, identity_field: str, identity: str, **extra: object):
    files = [_entry(path, path.name.split(".")[0]) for path in sorted(directory.glob("*"))]
    return {
        "schema_version": 1,
        "kind": kind,
        identity_field: identity,
        "created_at": BASE_TIME.isoformat(),
        "files": files,
        **extra,
    }


def _commit_source(store: ArtifactStore) -> None:
    staging = store.stage("source-job")
    (staging / "canonical.wav").write_bytes(b"legacy canonical audio")
    (staging / "original_preview.mp3").write_bytes(b"legacy original preview")
    store.commit_source(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": SOURCE_ID,
            "created_at": BASE_TIME.isoformat(),
            "metadata": {"title": "Song", "artist": "Artist", "duration_s": 120.0},
            "files": [
                _entry(staging / "canonical.wav", "canonical"),
                _entry(staging / "original_preview.mp3", "preview"),
            ],
        },
    )


def _write_roles(directory: Path, roles: tuple[str, ...], *, tag: str) -> None:
    for role in roles:
        (directory / f"{role}.wav").write_bytes(f"{tag}-{role}".encode())
        (directory / f"{role}_preview.mp3").write_bytes(f"{tag}-{role}-preview".encode())


def _seed_legacy_demucs_set(store: ArtifactStore) -> Path:
    legacy = store.sources_dir / SOURCE_ID / "stems"
    legacy.mkdir()
    _write_roles(legacy, DEMUCS_ROLES, tag="v0.1.0")
    (legacy / "manifest.json").write_text(
        json.dumps(
            _manifest(
                legacy,
                kind="separation",
                identity_field="source_id",
                identity=SOURCE_ID,
            )
        ),
        encoding="utf-8",
    )
    return legacy


def _seed_legacy_demucs_render(store: ArtifactStore) -> str:
    key = "render-legacy-demucs"
    directory = store.renders_dir / key
    directory.mkdir(parents=True)
    _write_roles(directory, DEMUCS_ROLES, tag="v0.1.0-render")
    (directory / "manifest.json").write_text(
        json.dumps(
            _manifest(
                directory,
                kind="render",
                identity_field="render_key",
                identity=key,
                source_id=SOURCE_ID,
                scope="stems",
                semitones=3,
                gain_db=0.0,
            )
        ),
        encoding="utf-8",
    )
    return key


def _commit_set(
    store: ArtifactStore,
    *,
    method: str,
    tag: str,
    created_delta: timedelta = timedelta(),
) -> None:
    roles = KARAOKE_ROLES if method == "karaoke" else DEMUCS_ROLES
    staging = store.stage(f"job-{method}-{tag}")
    _write_roles(staging, roles, tag=tag)
    manifest = _manifest(
        staging,
        kind="separation",
        identity_field="source_id",
        identity=SOURCE_ID,
    )
    manifest["method"] = method
    manifest["method_version"] = 1
    manifest["created_at"] = (BASE_TIME + created_delta).isoformat()
    store.commit_separation(staging, SOURCE_ID, manifest, method=method)


def _track() -> Track:
    source = Source(
        source_id=SOURCE_ID,
        origin="/music/song.wav",
        origin_kind=OriginKind.FILE,
        metadata=SourceMetadata(title="Song", artist="Artist", duration_s=120.0),
    )
    return Track(track_id=TRACK_ID, source=source, title="Song")


def _service(tmp_path: Path) -> tuple[WorkspaceService, ArtifactStore]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    workspace = Workspace()
    workspace.add_track(_track())
    service = WorkspaceService(
        SettingsStore(paths),
        paths=paths,
        artifacts=store,
        runner=JobRunner(paths=paths),
        engines=ServiceEngines(),
        workspace=workspace,
    )
    return service, store


def test_a_v0_1_0_cache_keeps_its_demucs_set_and_render_beside_a_karaoke_set(
    tmp_path: Path,
) -> None:
    service, store = _service(tmp_path)
    _commit_source(store)
    legacy_set = _seed_legacy_demucs_set(store)
    legacy_render = _seed_legacy_demucs_render(store)
    track = _track()

    # The seeded v0.1.0 Demucs set lists as Demucs and plays.
    (demucs,) = service.separation_sets(track)
    assert demucs.method == "demucs"
    assert demucs.output_roles == DEMUCS_ROLES
    assert demucs.set_key
    assert service.separation_preview_target(track, demucs.set_key, "vocals") is not None
    assert service.separation_preview_target(track, demucs.set_key, "drums") is not None

    # Its legacy Render lists, plays, and stays exportable.
    (render,) = service.pitch_sets(track)
    assert render.render_key == legacy_render
    assert render.method == "demucs"
    assert render.based_on_earlier_separation is False
    assert service.pitch_render_preview_target(track, legacy_render, "vocals") is not None
    targets = {target.key: target for target in service.export_targets(track)}
    for role in DEMUCS_ROLES:
        assert f"separation:demucs:{role}" in targets
        assert f"render:{legacy_render}:{role}" in targets
    vocals = resolve_export_target(store, source_id=SOURCE_ID, key="separation:demucs:vocals")
    assert vocals is not None
    assert vocals.path.read_bytes() == b"v0.1.0-vocals"

    # A Karaoke set coexists; neither set replaces the other.
    _commit_set(store, method="karaoke", tag="karaoke-1", created_delta=timedelta(minutes=1))
    demucs_set, karaoke_set = service.separation_sets(track)
    assert (demucs_set.method, karaoke_set.method) == ("demucs", "karaoke")
    assert legacy_set.is_dir()

    # Re-running Karaoke replaces only the Karaoke set.
    _commit_set(store, method="karaoke", tag="karaoke-2", created_delta=timedelta(minutes=2))
    demucs_after, karaoke_after = service.separation_sets(track)
    assert demucs_after.method == "demucs"
    assert karaoke_after.method == "karaoke"
    assert karaoke_after.set_key != karaoke_set.set_key
    assert legacy_set.is_dir()
    assert service.pitch_sets(track)[0].render_key == legacy_render

    # Promoting a new Demucs set removes the legacy directory and keeps Karaoke.
    _commit_set(store, method="demucs", tag="demucs-2", created_delta=timedelta(minutes=3))
    karaoke_final, demucs_final = service.separation_sets(track)
    assert demucs_final.method == "demucs"
    assert karaoke_final.method == "karaoke"
    assert not legacy_set.exists()
    assert (
        store.find_separation(SOURCE_ID, method="demucs").directory
        == store.sources_dir / SOURCE_ID / "separations" / "demucs"
    )
    assert service.pitch_sets(track)[0].render_key == legacy_render
    assert (
        resolve_export_target(store, source_id=SOURCE_ID, key=f"render:{legacy_render}:vocals")
        is not None
    )
