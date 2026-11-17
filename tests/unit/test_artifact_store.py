"""ArtifactStore: manifests, atomic commit, lookup, sweep, LRU, and removal."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from stemforge.artifacts import ArtifactStore
from stemforge.errors import StemForgeError
from stemforge.platform.paths import AppPaths


@pytest.fixture
def paths(tmp_path: Path) -> AppPaths:
    return AppPaths.for_home(tmp_path / "home").ensure()


@pytest.fixture
def store(paths: AppPaths) -> ArtifactStore:
    return ArtifactStore(paths)


def _file_entry(name: str, data: bytes, role: str | None = None) -> dict[str, object]:
    entry: dict[str, object] = {
        "path": name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if role is not None:
        entry["role"] = role
    return entry


def _source_manifest(
    source_id: str,
    files: list[dict[str, object]],
    *,
    created_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "source",
        "source_id": source_id,
        "created_at": (created_at or datetime(2026, 11, 16, 12, 0, tzinfo=UTC)).isoformat(),
        "metadata": {"title": "Song", "artist": "Artist"},
        "files": files,
    }


def _commit_source(
    store: ArtifactStore,
    source_id: str,
    *,
    data: bytes = b"canonical-audio",
    name: str = "canonical.wav",
    created_at: datetime | None = None,
) -> Any:
    staging = store.stage(f"job-{uuid4().hex}")
    (staging / name).write_bytes(data)
    manifest = _source_manifest(
        source_id,
        [_file_entry(name, data, "canonical")],
        created_at=created_at,
    )
    return store.commit_source(staging, source_id, manifest)


@pytest.mark.unit
def test_stage_creates_a_job_directory_under_staging(store: ArtifactStore, paths: AppPaths) -> None:
    staging = store.stage("job-1")

    assert staging == paths.cache / "staging" / "job-1"
    assert staging.is_dir()
    assert not any((paths.cache / "sources").iterdir())
    assert not any((paths.cache / "renders").iterdir())


@pytest.mark.unit
def test_commit_source_promotes_one_atomic_directory_and_writes_a_relative_manifest(
    store: ArtifactStore, paths: AppPaths
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"canonical-audio")
    manifest = _source_manifest(
        "abc123",
        [_file_entry("canonical.wav", b"canonical-audio", "canonical")],
    )

    artifact = store.commit_source(staging, "abc123", manifest)

    source_dir = paths.cache / "sources" / "abc123"
    assert not staging.exists()
    assert artifact.source_id == "abc123"
    assert artifact.directory == source_dir
    assert artifact.canonical_path == source_dir / "canonical.wav"
    assert artifact.manifest_path == source_dir / "source.json"
    assert artifact.manifest["metadata"] == {"title": "Song", "artist": "Artist"}
    payload = json.loads((source_dir / "source.json").read_text(encoding="utf-8"))
    assert payload["files"][0]["path"] == "canonical.wav"
    assert not Path(payload["files"][0]["path"]).is_absolute()
    assert (source_dir / ".used").is_file()


@pytest.mark.unit
def test_find_source_round_trips_and_reports_unknown_ids(store: ArtifactStore) -> None:
    committed = _commit_source(store, "abc123")

    found = store.find_source("abc123")

    assert found is not None
    assert found.source_id == committed.source_id
    assert found.canonical_path == committed.canonical_path
    assert found.files == committed.files
    assert store.find_source("missing") is None
    assert [item.source_id for item in store.iter_sources()] == ["abc123"]


@pytest.mark.unit
def test_commit_source_is_idempotent_and_reuses_the_valid_existing_set(
    store: ArtifactStore, paths: AppPaths
) -> None:
    first = _commit_source(store, "abc123")

    second = _commit_source(store, "abc123")

    assert second.canonical_path == first.canonical_path
    assert list((paths.cache / "sources").iterdir()) == [paths.cache / "sources" / "abc123"]


@pytest.mark.unit
def test_invalid_manifest_is_invisible_to_lookup(store: ArtifactStore, paths: AppPaths) -> None:
    source_dir = paths.cache / "sources" / "broken"
    source_dir.mkdir(parents=True)
    (source_dir / "canonical.wav").write_bytes(b"x")
    (source_dir / "source.json").write_text('{"schema_version": 1}', encoding="utf-8")

    assert store.find_source("broken") is None
    assert list(store.iter_sources()) == []


@pytest.mark.unit
def test_a_missing_referenced_file_makes_the_set_invalid(
    store: ArtifactStore, paths: AppPaths
) -> None:
    artifact = _commit_source(store, "abc123")
    (artifact.canonical_path).unlink()

    assert store.find_source("abc123") is None


@pytest.mark.unit
def test_a_hash_mismatch_makes_the_set_invalid(store: ArtifactStore) -> None:
    artifact = _commit_source(store, "abc123")
    payload = json.loads(artifact.manifest_path.read_text(encoding="utf-8"))
    payload["files"][0]["sha256"] = hashlib.sha256(b"other").hexdigest()
    artifact.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert store.find_source("abc123") is None


@pytest.mark.unit
def test_newer_schema_manifest_is_ignored_but_left_untouched(
    store: ArtifactStore, paths: AppPaths
) -> None:
    source_dir = paths.cache / "sources" / "future"
    source_dir.mkdir(parents=True)
    (source_dir / "canonical.wav").write_bytes(b"future-audio")
    payload = _source_manifest(
        "future",
        [_file_entry("canonical.wav", b"future-audio", "canonical")],
    )
    payload["schema_version"] = 2
    (source_dir / "source.json").write_text(json.dumps(payload), encoding="utf-8")

    assert store.find_source("future") is None
    assert list(store.iter_sources()) == []

    report = store.sweep_stale()

    assert source_dir.is_dir()
    assert report.deleted_anything is False


@pytest.mark.unit
@pytest.mark.parametrize("source_id", ["", ".", "..", "../escape", "a/b", "a\\b", "con", "A" * 201])
def test_source_identity_charset_is_validated_and_traversal_is_refused(
    store: ArtifactStore, source_id: str
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"x")

    with pytest.raises(ValueError, match="invalid source id"):
        store.commit_source(
            staging,
            source_id,
            _source_manifest(source_id, [_file_entry("canonical.wav", b"x", "canonical")]),
        )

    assert list(store.sources_dir.iterdir()) == []


@pytest.mark.unit
def test_commit_refuses_a_staging_path_outside_the_staging_root(
    store: ArtifactStore, tmp_path: Path
) -> None:
    rogue = tmp_path / "rogue"
    rogue.mkdir()
    (rogue / "canonical.wav").write_bytes(b"x")

    with pytest.raises(ValueError, match="staging path"):
        store.commit_source(
            rogue,
            "abc123",
            _source_manifest("abc123", [_file_entry("canonical.wav", b"x", "canonical")]),
        )


@pytest.mark.unit
def test_commit_refuses_a_manifest_path_that_escapes_the_set(
    store: ArtifactStore, paths: AppPaths
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"x")
    (paths.cache / "outside.wav").write_bytes(b"x")

    with pytest.raises(ValueError, match="manifest references"):
        store.commit_source(
            staging,
            "abc123",
            _source_manifest(
                "abc123",
                [{"path": "../outside.wav", "role": "canonical"}],
            ),
        )


@pytest.mark.unit
def test_oserror_during_commit_is_wrapped_as_permission_denied(
    store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"x")

    def refuse(source: object, destination: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr("stemforge.artifacts.os.replace", refuse)

    with pytest.raises(StemForgeError) as caught:
        store.commit_source(
            staging,
            "abc123",
            _source_manifest("abc123", [_file_entry("canonical.wav", b"x", "canonical")]),
        )

    assert caught.value.code == "storage.permission_denied"


@pytest.mark.unit
def test_enospc_is_wrapped_as_disk_full(
    store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"x")

    def full(source: object, destination: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("stemforge.artifacts.os.replace", full)

    with pytest.raises(StemForgeError) as caught:
        store.commit_source(
            staging,
            "abc123",
            _source_manifest("abc123", [_file_entry("canonical.wav", b"x", "canonical")]),
        )

    assert caught.value.code == "storage.disk_full"


def _commit_render(
    store: ArtifactStore,
    render_key: str,
    *,
    source_id: str = "abc123",
    data: bytes = b"rendered",
    name: str = "original.wav",
) -> Any:
    staging = store.stage(f"job-{uuid4().hex}")
    (staging / name).write_bytes(data)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "render",
        "render_key": render_key,
        "source_id": source_id,
        "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
        "semitones": 3,
        "files": [_file_entry(name, data, "original")],
    }
    return store.commit_render(staging, render_key, manifest)


@pytest.mark.unit
def test_commit_separation_promotes_into_the_source_subtree(store: ArtifactStore) -> None:
    _commit_source(store, "abc123")
    staging = store.stage("job-sep")
    (staging / "vocals.wav").write_bytes(b"vocals")
    (staging / "vocals.mp3").write_bytes(b"preview")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "separation",
        "source_id": "abc123",
        "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
        "gain_db": -1.0,
        "files": [
            _file_entry("vocals.wav", b"vocals", "vocals"),
            _file_entry("vocals.mp3", b"preview", "vocals_preview"),
        ],
    }

    artifact = store.commit_separation(staging, "abc123", manifest)

    assert artifact.directory == store.sources_dir / "abc123" / "stems"
    assert artifact.path_for("vocals") == artifact.directory / "vocals.wav"
    assert artifact.gain_db == -1.0
    assert (artifact.directory / "manifest.json").is_file()
    assert store.find_separation("abc123") == artifact
    assert store.find_separation("missing") is None


@pytest.mark.unit
def test_commit_separation_requires_a_committed_source(store: ArtifactStore) -> None:
    staging = store.stage("job-sep")
    (staging / "vocals.wav").write_bytes(b"vocals")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "separation",
        "source_id": "missing",
        "files": [_file_entry("vocals.wav", b"vocals", "vocals")],
    }

    with pytest.raises(ValueError, match="source is not committed"):
        store.commit_separation(staging, "missing", manifest)


@pytest.mark.unit
def test_commit_render_round_trips_with_its_source_identity(store: ArtifactStore) -> None:
    artifact = _commit_render(store, "render123")

    assert artifact.render_key == "render123"
    assert artifact.source_id == "abc123"
    assert artifact.manifest["semitones"] == 3
    assert store.find_render("render123") == artifact
    assert list(store.iter_renders()) == [artifact]


@pytest.mark.unit
def test_manifest_is_written_last_and_a_failed_promotion_is_invisible(
    store: ArtifactStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = store.stage("job-1")
    (staging / "canonical.wav").write_bytes(b"canonical")
    real_replace = os.replace
    calls = {"count": 0}

    def fail_on_promotion(source: object, destination: object) -> None:
        calls["count"] += 1
        if calls["count"] > 1:
            raise PermissionError(13, "simulated crash before promotion")
        real_replace(source, destination)

    monkeypatch.setattr("stemforge.artifacts.os.replace", fail_on_promotion)

    with pytest.raises(StemForgeError):
        store.commit_source(
            staging,
            "abc123",
            _source_manifest("abc123", [_file_entry("canonical.wav", b"canonical", "canonical")]),
        )

    assert (staging / "source.json").is_file()
    assert store.find_source("abc123") is None
    assert not (store.sources_dir / "abc123").exists()


@pytest.mark.unit
def test_sweep_removes_tmp_staging_and_partials_but_keeps_completed_work(
    store: ArtifactStore,
) -> None:
    source = _commit_source(store, "abc123")
    render = _commit_render(store, "render123")
    (store.tmp_dir / "leftover.bin").write_bytes(b"tmp")
    (store.tmp_dir / "nested").mkdir()
    (store.tmp_dir / "nested" / "file").write_bytes(b"tmp")
    staging = store.stage("crashed-job")
    (staging / "output.wav").write_bytes(b"unfinished")
    partial = source.directory / "canonical.partial.wav"
    partial.write_bytes(b"partial")
    render_partial = render.directory / "original.partial.mp3"
    render_partial.write_bytes(b"partial")

    report = store.sweep_stale()

    assert report.removed_tmp == (store.tmp_dir / "leftover.bin", store.tmp_dir / "nested")
    assert report.removed_staging == (staging,)
    assert set(report.removed_partials) == {partial, render_partial}
    assert report.deleted_anything is True
    assert not partial.exists()
    assert not staging.exists()
    assert list(store.tmp_dir.iterdir()) == []
    assert store.find_source("abc123") is not None
    assert store.find_render("render123") is not None


@pytest.mark.unit
def test_sweep_deletes_invalid_sets_without_raising_the_banner_flag(
    store: ArtifactStore,
) -> None:
    missing_manifest = store.sources_dir / "no-manifest"
    missing_manifest.mkdir(parents=True)
    (missing_manifest / "canonical.wav").write_bytes(b"x")
    bad_render = store.renders_dir / "bad-render"
    bad_render.mkdir(parents=True)
    (bad_render / "manifest.json").write_text("{not json", encoding="utf-8")

    report = store.sweep_stale()

    assert report.removed_sources == ("no-manifest",)
    assert report.removed_renders == ("bad-render",)
    assert report.deleted_anything is False
    assert not missing_manifest.exists()
    assert not bad_render.exists()


@pytest.mark.unit
def test_sweep_deletes_an_invalid_separation_but_keeps_its_source(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    stems = source.directory / "stems"
    stems.mkdir()
    (stems / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")

    report = store.sweep_stale()

    assert report.removed_separations == ("abc123",)
    assert report.deleted_anything is False
    assert not stems.exists()
    assert store.find_source("abc123") is not None


@pytest.mark.unit
def test_sweep_deletes_a_set_when_a_referenced_file_is_missing(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    render = _commit_render(store, "render123")
    source.canonical_path.unlink()
    (render.directory / "original.wav").unlink()

    report = store.sweep_stale()

    assert report.removed_sources == ("abc123",)
    assert report.removed_renders == ("render123",)
    assert report.deleted_anything is False


@pytest.mark.unit
def test_sweep_never_touches_the_model_store_or_other_roots(store: ArtifactStore) -> None:
    model = store.paths.models / "installed" / "htdemucs.json"
    model.parent.mkdir(parents=True)
    model.write_text("{}", encoding="utf-8")
    log = store.paths.logs / "keep.log"
    log.write_text("keep", encoding="utf-8")

    store.sweep_stale()

    assert model.is_file()
    assert log.is_file()


@pytest.mark.unit
def test_sweep_removes_stray_entries_at_the_cache_root(store: ArtifactStore) -> None:
    stray = store.sources_dir / "stray.txt"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("not a source", encoding="utf-8")
    stray_render = store.renders_dir / "stray.txt"
    stray_render.parent.mkdir(parents=True, exist_ok=True)
    stray_render.write_text("not a render", encoding="utf-8")

    report = store.sweep_stale()

    assert not stray.exists()
    assert not stray_render.exists()
    assert report.deleted_anything is False


def _set_used(store: ArtifactStore, key: str, when: float, *, kind: str = "source") -> None:
    directory = store.sources_dir if kind == "source" else store.renders_dir
    marker = directory / key / ".used"
    marker.touch()
    os.utime(marker, (when, when))


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


@pytest.mark.unit
def test_usage_counts_only_sources_and_renders(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123", data=b"0123456789")
    render = _commit_render(store, "render123", data=b"01234")
    (store.tmp_dir / "tmp.bin").write_bytes(b"x" * 1000)
    deno = store.root / "deno"
    deno.mkdir()
    (deno / "cache.bin").write_bytes(b"x" * 1000)
    model = store.paths.models / "weights.bin"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"x" * 1000)

    assert store.usage_bytes() == _dir_size(source.directory) + _dir_size(render.directory)


@pytest.mark.unit
def test_enforce_cap_evicts_oldest_whole_sets_first(store: ArtifactStore) -> None:
    source = _commit_source(store, "oldest")
    render = _commit_render(store, "middle")
    newest = _commit_source(store, "newest")
    _set_used(store, "oldest", 100.0)
    _set_used(store, "middle", 200.0, kind="render")
    _set_used(store, "newest", 300.0)
    sizes = {
        "oldest": _dir_size(source.directory),
        "middle": _dir_size(render.directory),
        "newest": _dir_size(newest.directory),
    }
    store = ArtifactStore(store.paths, cap_bytes=sum(sizes.values()) - sizes["oldest"])

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["oldest"]
    assert not source.directory.exists()
    assert render.directory.is_dir()
    assert newest.directory.is_dir()
    assert report.usage_after == sum(sizes.values()) - sizes["oldest"]
    assert store.usage_bytes() == report.usage_after


@pytest.mark.unit
def test_enforce_cap_records_evictions_for_the_diagnostics_window(
    store: ArtifactStore,
) -> None:
    source = _commit_source(store, "old")
    newest = _commit_source(store, "new")
    _set_used(store, "old", 100.0)
    _set_used(store, "new", 200.0)
    store = ArtifactStore(store.paths, cap_bytes=_dir_size(newest.directory))

    store.enforce_cap(set())

    now = datetime.now(UTC)
    assert store.eviction_count(now - timedelta(days=7)) == 1
    assert store.eviction_count(now + timedelta(minutes=1)) == 0
    assert store.eviction_count(now - timedelta(days=7)) < 2
    assert not source.directory.exists()
    assert store.eviction_ledger_path.is_file()


@pytest.mark.unit
def test_a_corrupt_eviction_ledger_never_breaks_diagnostics(store: ArtifactStore) -> None:
    store.eviction_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    store.eviction_ledger_path.write_text("not json\n", encoding="utf-8")

    assert store.eviction_count(datetime.now(UTC)) == 0


@pytest.mark.unit
def test_a_missing_used_marker_falls_back_to_manifest_created_at(store: ArtifactStore) -> None:
    old = _commit_source(store, "old", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    new = _commit_source(store, "new", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    (old.directory / ".used").unlink()
    (new.directory / ".used").unlink()
    cap = _dir_size(new.directory)
    store = ArtifactStore(store.paths, cap_bytes=cap)

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["old"]
    assert not old.directory.exists()
    assert new.directory.is_dir()


@pytest.mark.unit
def test_touch_source_moves_an_entry_to_the_newest_position(store: ArtifactStore) -> None:
    first = _commit_source(store, "first")
    second = _commit_source(store, "second")
    _set_used(store, "first", 100.0)
    _set_used(store, "second", 200.0)
    cap = _dir_size(first.directory) + _dir_size(second.directory) - _dir_size(second.directory)
    store = ArtifactStore(store.paths, cap_bytes=cap)

    assert store.touch_source("first") is True

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["second"]
    assert first.directory.is_dir()
    assert not second.directory.exists()


@pytest.mark.unit
def test_live_entries_are_never_evicted(store: ArtifactStore) -> None:
    live = _commit_source(store, "live")
    dead = _commit_source(store, "dead")
    _set_used(store, "live", 100.0)
    _set_used(store, "dead", 200.0)
    store = ArtifactStore(store.paths, cap_bytes=_dir_size(dead.directory) + 1)
    store.retain("live")

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["dead"]
    assert live.directory.is_dir()
    assert store.is_live("live") is True
    assert store.release("live") is True
    assert store.is_live("live") is False
    assert store.release("live") is False


@pytest.mark.unit
def test_live_keys_from_the_workspace_callback_are_protected(store: ArtifactStore) -> None:
    live = _commit_source(store, "live")
    dead = _commit_source(store, "dead")
    store = ArtifactStore(
        store.paths,
        cap_bytes=_dir_size(dead.directory) + 1,
        live_keys=lambda: {"live"},
    )
    _set_used(store, "live", 100.0)
    _set_used(store, "dead", 200.0)

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["dead"]
    assert live.directory.is_dir()


@pytest.mark.unit
def test_cache_full_is_raised_when_everything_left_is_live(store: ArtifactStore) -> None:
    first = _commit_source(store, "first")
    second = _commit_source(store, "second")
    store = ArtifactStore(store.paths, cap_bytes=1)
    store.retain("first")
    store.retain("second")

    with pytest.raises(StemForgeError) as caught:
        store.enforce_cap(set())

    assert caught.value.code == "storage.cache_full"
    assert first.directory.is_dir()
    assert second.directory.is_dir()


@pytest.mark.unit
def test_a_newer_schema_entry_is_ignored_by_lookup_but_eligible_for_lru(
    store: ArtifactStore,
) -> None:
    future = store.sources_dir / "future"
    future.mkdir(parents=True)
    (future / "canonical.wav").write_bytes(b"future")
    payload = _source_manifest(
        "future",
        [_file_entry("canonical.wav", b"future", "canonical")],
    )
    payload["schema_version"] = 2
    (future / "source.json").write_text(json.dumps(payload), encoding="utf-8")
    valid = _commit_source(store, "valid")
    store = ArtifactStore(store.paths, cap_bytes=_dir_size(valid.directory))
    _set_used(store, "future", 100.0)
    _set_used(store, "valid", 300.0)

    report = store.enforce_cap(set())

    assert [entry.key for entry in report.evicted] == ["future"]
    assert not future.exists()
    assert valid.directory.is_dir()


@pytest.mark.unit
def test_refcount_hooks_are_thread_safe(store: ArtifactStore) -> None:
    import threading

    def churn() -> None:
        for _ in range(200):
            store.retain("key")
            assert store.is_live("key") is True
            assert store.release("key") is True

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.is_live("key") is False


@pytest.mark.unit
def test_remove_source_deletes_its_subtree_and_every_derived_render(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    derived = _commit_render(store, "derived", source_id="abc123")
    unrelated = _commit_render(store, "unrelated", source_id="other")

    report = store.remove_source("abc123")

    assert report.source_removed is True
    assert report.renders_removed == ("derived",)
    assert not source.directory.exists()
    assert not derived.directory.exists()
    assert unrelated.directory.is_dir()
    assert store.find_source("abc123") is None
    assert store.find_render("derived") is None
    assert store.find_render("unrelated") is not None


@pytest.mark.unit
def test_remove_source_refuses_while_the_source_is_still_live(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    store.retain("abc123")

    with pytest.raises(ValueError, match="still in use"):
        store.remove_source("abc123")

    assert source.directory.is_dir()
    store.release("abc123")
    assert store.remove_source("abc123").source_removed is True


@pytest.mark.unit
def test_remove_source_keeps_a_render_that_is_still_retained(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    render = _commit_render(store, "derived", source_id="abc123")
    store.retain("derived")

    report = store.remove_source("abc123")

    assert report.renders_removed == ()
    assert not source.directory.exists()
    assert render.directory.is_dir()


@pytest.mark.unit
def test_clear_cache_keeps_live_work_and_clears_tool_caches(store: ArtifactStore) -> None:
    live = _commit_source(store, "live")
    dead = _commit_source(store, "dead")
    render = _commit_render(store, "render123", source_id="dead")
    store.retain("live")
    for tool in ("tmp", "deno", "yt-dlp"):
        directory = store.root / tool
        directory.mkdir(exist_ok=True)
        (directory / "cache.bin").write_bytes(b"x" * 50)
    staging = store.stage("running-job")
    model = store.paths.models / "weights.bin"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"weights")

    report = store.clear_cache()

    assert report.sources_removed == ("dead",)
    assert report.renders_removed == ("render123",)
    assert live.directory.is_dir()
    assert not dead.directory.exists()
    assert not render.directory.exists()
    assert not (store.root / "deno").exists()
    assert not (store.root / "yt-dlp").exists()
    assert list(store.tmp_dir.iterdir()) == []
    assert staging.is_dir()
    assert model.is_file()
    assert store.usage_bytes() == _dir_size(live.directory)


@pytest.mark.unit
def test_stemforge_home_fully_isolates_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "portable"
    monkeypatch.setenv("STEMFORGE_HOME", str(home))
    store = ArtifactStore()

    artifact = _commit_source(store, "abc123")

    assert store.paths.cache == (home / "cache").resolve()
    assert artifact.directory.is_relative_to(home.resolve())
    assert (home / "cache" / "sources" / "abc123" / "source.json").is_file()
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "data").exists()


@pytest.mark.unit
def test_the_default_cap_is_eight_gibibytes(store: ArtifactStore) -> None:
    assert store.cap_bytes == 8 * 1024**3


@pytest.mark.unit
def test_commit_source_requires_a_canonical_role(store: ArtifactStore) -> None:
    staging = store.stage("job-1")
    (staging / "native.flac").write_bytes(b"native")

    with pytest.raises(ValueError, match="required roles"):
        store.commit_source(
            staging,
            "abc123",
            _source_manifest("abc123", [_file_entry("native.flac", b"native", "native")]),
        )

    assert not (store.sources_dir / "abc123").exists()


@pytest.mark.unit
def test_commit_render_requires_a_source_id(store: ArtifactStore) -> None:
    staging = store.stage("job-render")
    (staging / "original.wav").write_bytes(b"rendered")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "render",
        "render_key": "render123",
        "files": [_file_entry("original.wav", b"rendered", "original")],
    }

    with pytest.raises(ValueError, match="source_id"):
        store.commit_render(staging, "render123", manifest)

    assert not (store.renders_dir / "render123").exists()


@pytest.mark.unit
def test_re_separation_replaces_the_existing_stem_set(store: ArtifactStore) -> None:
    _commit_source(store, "abc123")

    def separate(job: str, vocals: bytes) -> Any:
        staging = store.stage(job)
        (staging / "vocals.wav").write_bytes(vocals)
        return store.commit_separation(
            staging,
            "abc123",
            {
                "schema_version": 1,
                "kind": "separation",
                "source_id": "abc123",
                "files": [_file_entry("vocals.wav", vocals, "vocals")],
            },
        )

    first = separate("job-first", b"first")
    second = separate("job-second", b"second")

    assert (second.directory / "vocals.wav").read_bytes() == b"second"
    assert first.manifest_path == second.manifest_path
    assert list(second.directory.glob(".*.old-*")) == []


@pytest.mark.unit
def test_sweep_removes_replacement_residue_inside_a_source(store: ArtifactStore) -> None:
    source = _commit_source(store, "abc123")
    residue = source.directory / ".stems.old-deadbeef"
    residue.mkdir()
    (residue / "vocals.wav").write_bytes(b"old")

    report = store.sweep_stale()

    assert not residue.exists()
    assert report.deleted_anything is False
    assert store.find_source("abc123") is not None


@pytest.mark.unit
def test_newer_schema_render_is_left_untouched_by_the_sweep(store: ArtifactStore) -> None:
    render_dir = store.renders_dir / "future"
    render_dir.mkdir(parents=True)
    (render_dir / "original.wav").write_bytes(b"future")
    (render_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "kind": "render",
                "render_key": "future",
                "source_id": "abc123",
                "created_at": datetime(2026, 11, 16, tzinfo=UTC).isoformat(),
                "files": [_file_entry("original.wav", b"future", "original")],
            }
        ),
        encoding="utf-8",
    )

    report = store.sweep_stale()

    assert render_dir.is_dir()
    assert report.deleted_anything is False
    assert store.find_render("future") is None
