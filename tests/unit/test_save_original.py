"""Save original: byte-for-byte copy, Native-file checks, and destination rules."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind, OriginKind
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline import save_original_job
from stemforge.pipeline.save_original_job import (
    NativeFile,
    require_native_file,
    run_save_original_job,
)
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

LOG = logging.getLogger("test.save-original")
SOURCE_ID = "src-original"
TITLE = "Song"
NATIVE_BYTES = b"original flac bytes" * 8


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
    native: bytes = NATIVE_BYTES,
    suffix: str = ".flac",
    origin_kind: OriginKind = OriginKind.FILE,
    include_native: bool = True,
) -> None:
    staging = store.stage("source-job")
    canonical = staging / "canonical.wav"
    canonical.write_bytes(b"canonical audio")
    files = [_entry(canonical, "canonical")]
    if include_native:
        native_path = staging / f"native{suffix}"
        native_path.write_bytes(native)
        files.insert(0, _entry(native_path, "native"))
    store.commit_source(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": SOURCE_ID,
            "created_at": datetime(2026, 11, 16, 9, 0, tzinfo=UTC).isoformat(),
            "origin": "/music/song.flac",
            "origin_kind": origin_kind.value,
            "gain_db": 0.0,
            "metadata": {"title": TITLE},
            "files": files,
        },
    )


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(AppPaths.for_home(tmp_path / "home").ensure())


def _context(*, cancel: CancelToken | None = None) -> JobContext:
    return JobContext(
        job_id="job-save-original",
        kind=JobKind.SAVE_ORIGINAL,
        cancel=cancel if cancel is not None else CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )


def test_save_original_copies_the_native_bytes_and_reports_the_recorded_hash(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()

    result = run_save_original_job(
        _context(),
        artifacts=store,
        source_id=SOURCE_ID,
        title=TITLE,
        destination_dir=destination,
    )

    assert result.path == destination / f"{TITLE}.flac"
    assert result.path.read_bytes() == NATIVE_BYTES
    assert result.size_bytes == len(NATIVE_BYTES)
    assert result.sha256 == hashlib.sha256(NATIVE_BYTES).hexdigest()
    assert result.path.suffix == ".flac"
    assert not list(destination.glob("*.partial"))


def test_a_collision_walks_the_same_suffix_sequence_as_export(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()
    (destination / f"{TITLE}.flac").write_bytes(b"already here")

    result = run_save_original_job(
        _context(),
        artifacts=store,
        source_id=SOURCE_ID,
        title=TITLE,
        destination_dir=destination,
    )

    assert result.path == destination / f"{TITLE} (2).flac"
    assert result.path.read_bytes() == NATIVE_BYTES
    assert (destination / f"{TITLE}.flac").read_bytes() == b"already here"


def test_a_chosen_name_keeps_the_native_extension(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()

    result = run_save_original_job(
        _context(),
        artifacts=store,
        source_id=SOURCE_ID,
        title=TITLE,
        destination_dir=destination,
        destination_name="My mix.flac",
    )

    assert result.path == destination / "My mix.flac"


def test_a_missing_native_role_is_coded_missing_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store, include_native=False)
    destination = tmp_path / "exports"
    destination.mkdir()

    with pytest.raises(StemForgeError) as raised:
        run_save_original_job(
            _context(),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert raised.value.code == errors.EXPORT_ORIGINAL_UNAVAILABLE
    assert raised.value.detail == "missing"
    assert raised.value.retryable is False
    assert list(destination.iterdir()) == []


def test_a_youtube_source_is_coded_not_local(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store, origin_kind=OriginKind.YOUTUBE)

    with pytest.raises(StemForgeError) as raised:
        require_native_file(store, SOURCE_ID)

    assert raised.value.code == errors.EXPORT_ORIGINAL_UNAVAILABLE
    assert raised.value.detail == "not_local"


def test_a_changed_native_file_is_removed_before_the_final_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    native = store.sources_dir / SOURCE_ID / "native.flac"
    native.write_bytes(b"tampered with")
    destination = tmp_path / "exports"
    destination.mkdir()

    with pytest.raises(StemForgeError) as raised:
        run_save_original_job(
            _context(),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert raised.value.code == errors.EXPORT_ORIGINAL_UNAVAILABLE
    assert raised.value.detail == "changed"
    assert list(destination.iterdir()) == []


def test_a_same_size_hash_mismatch_removes_the_partial(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    native = store.sources_dir / SOURCE_ID / "native.flac"
    native.write_bytes(b"X" * len(NATIVE_BYTES))
    destination = tmp_path / "exports"
    destination.mkdir()

    with pytest.raises(StemForgeError) as raised:
        run_save_original_job(
            _context(),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert raised.value.code == errors.EXPORT_ORIGINAL_UNAVAILABLE
    assert raised.value.detail == "changed"
    assert list(destination.iterdir()) == []


def test_a_changed_size_is_caught_before_the_copy_starts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    native = store.sources_dir / SOURCE_ID / "native.flac"
    native.write_bytes(native.read_bytes() + b"extra")
    destination = tmp_path / "exports"
    destination.mkdir()

    with pytest.raises(StemForgeError) as raised:
        run_save_original_job(
            _context(),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert raised.value.detail == "changed"
    assert list(destination.iterdir()) == []


def test_cancel_removes_the_partial_and_leaves_no_final_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()
    token = CancelToken()
    token.cancel()

    with pytest.raises(JobCancelled):
        run_save_original_job(
            _context(cancel=token),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert list(destination.iterdir()) == []


def test_the_copy_loop_removes_the_partial_when_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "native.flac"
    source.write_bytes(NATIVE_BYTES)
    native = NativeFile(
        source_id=SOURCE_ID,
        path=source,
        suffix=".flac",
        size_bytes=len(NATIVE_BYTES),
        sha256=hashlib.sha256(NATIVE_BYTES).hexdigest(),
    )
    partial = tmp_path / "Song.flac.partial"
    token = CancelToken()
    token.cancel()

    with pytest.raises(JobCancelled):
        save_original_job._copy_verified(
            native,
            partial,
            token,
            on_progress=lambda copied: None,
        )

    assert not partial.exists()


def test_a_cancel_after_the_copy_before_the_rename_removes_the_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()
    token = CancelToken()

    def copied(
        native: NativeFile,
        partial: Path,
        cancel: CancelToken,
        *,
        on_progress,
    ) -> str:
        del on_progress
        partial.write_bytes(b"copied bytes")
        cancel.cancel()
        return native.sha256

    monkeypatch.setattr(save_original_job, "_copy_verified", copied)

    with pytest.raises(JobCancelled):
        run_save_original_job(
            _context(cancel=token),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert list(destination.iterdir()) == []


def test_a_full_destination_fails_before_any_file_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()

    def full(path: Path, needed: int, *, floor: int = 0) -> None:
        del path, needed, floor
        raise StemForgeError(
            errors.STORAGE_DISK_FULL,
            "full",
            retryable=True,
            context={"path": str(destination), "required": 1, "free": 0},
        )

    monkeypatch.setattr(save_original_job, "require_free_space", full)

    with pytest.raises(StemForgeError) as raised:
        run_save_original_job(
            _context(),
            artifacts=store,
            source_id=SOURCE_ID,
            title=TITLE,
            destination_dir=destination,
        )

    assert raised.value.code == errors.STORAGE_DISK_FULL
    assert list(destination.iterdir()) == []


def test_a_missing_native_file_is_coded_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_source(store)
    (store.sources_dir / SOURCE_ID / "native.flac").unlink()

    with pytest.raises(StemForgeError) as raised:
        require_native_file(store, SOURCE_ID)

    assert raised.value.detail == "missing"
