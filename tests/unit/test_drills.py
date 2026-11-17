"""Platform-neutral failure drills on temporary ``STEMFORGE_HOME`` roots.

These run in the unit layer on both CI operating systems: no native dialog, no
file manager, no network, and no model weights. The Windows runner executes the
same file via ``pytest -m "unit or ui"`` after the merge; the drills do not fake
a platform.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.engines import youtube
from stemforge.engines.media import ExportFormat, MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext
from stemforge.jobs.models import JobState
from stemforge.jobs.runner import JobRunner
from stemforge.models import ModelConsent, ModelStore
from stemforge.pipeline import export_job, model_job
from stemforge.pipeline.export_job import run_export_job
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

LOG = logging.getLogger("test.drills")


def _store(paths: AppPaths) -> ArtifactStore:
    return ArtifactStore(paths)


def _commit_source(store: ArtifactStore, source_id: str = "src-drill") -> None:
    staging = store.stage("source-drill")
    canonical = staging / "canonical.wav"
    canonical.write_bytes(b"canonical audio")
    data = canonical.read_bytes()
    store.commit_source(
        staging,
        source_id,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": source_id,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": {"title": "Drill"},
            "files": [
                {
                    "path": "canonical.wav",
                    "role": "canonical",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        },
    )


# --- Crash mid-Job and the startup sweep -----------------------------------


def test_a_process_crash_mid_job_leaves_residue_the_sweep_removes(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    paths = AppPaths.for_home(home).ensure()
    store = _store(paths)
    _commit_source(store)
    script = textwrap.dedent(
        """
        import os
        import sys

        from stemforge.artifacts import ArtifactStore
        from stemforge.platform.paths import AppPaths

        store = ArtifactStore(AppPaths.for_home(sys.argv[1]))
        staging = store.stage("crashed-job")
        (staging / "vocals.wav").write_bytes(b"unfinished stem")
        renders = store.renders_dir / "crashed-render"
        renders.mkdir(parents=True, exist_ok=True)
        (renders / "original.wav.partial").write_bytes(b"unfinished render")
        os._exit(9)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(home)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={
            **os.environ,
            "STEMFORGE_HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 9, result.stderr
    assert (paths.cache / "staging" / "crashed-job" / "vocals.wav").is_file()
    assert (paths.cache / "renders" / "crashed-render" / "original.wav.partial").is_file()

    reopened = _store(AppPaths.for_home(home))
    report = reopened.sweep_stale()

    assert report.deleted_anything is True
    assert not (paths.cache / "staging" / "crashed-job").exists()
    assert not (paths.cache / "renders" / "crashed-render").exists()
    assert reopened.find_source("src-drill") is not None
    # The recovery banner's one and only trigger is this sweep flag.
    show_recovery = report.deleted_anything
    assert show_recovery is True

    assert reopened.sweep_stale().deleted_anything is False


# --- Close mid-Job ---------------------------------------------------------


def test_close_cancels_the_active_job_and_removes_its_partial(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    runner = JobRunner(paths=paths)
    started = threading.Event()
    partial = paths.cache / "tmp" / "close-drill.partial"
    partial.parent.mkdir(parents=True, exist_ok=True)

    def run(ctx: JobContext) -> None:
        partial.write_bytes(b"half written")
        started.set()
        try:
            while True:
                ctx.cancel.check()
                time.sleep(0.005)
        finally:
            partial.unlink(missing_ok=True)

    runner.start()
    job_id = runner.submit(Job(kind=JobKind.EXPORT, identity=("close",), run=run))
    assert started.wait(5)
    assert partial.is_file()

    runner.stop(timeout=5)

    assert runner.snapshot(job_id).state is JobState.CANCELLED
    assert not partial.exists()


# --- Cancel mid-Job during an Export --------------------------------------


class _CancelAfter(CancelToken):
    def __init__(self, checks: int) -> None:
        super().__init__()
        self._remaining = checks

    def check(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self.cancel()
        super().check()


class _ExportMedia:
    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo("pcm_s24le", 44_100, 2, 2.0, "wav", {"title": "Drill"}, bit_depth=24)

    def encode_export(self, src: Path, dst: Path, **kwargs: object) -> None:
        dst.write_bytes(b"x" * (3 * 1024 * 1024))


def test_cancel_mid_export_removes_the_destination_partial_and_staging(
    tmp_path: Path,
) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = _store(paths)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()
    context = JobContext(
        job_id="export-drill",
        kind=JobKind.EXPORT,
        cancel=_CancelAfter(4),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )

    with pytest.raises(errors.JobCancelled):
        run_export_job(
            context,
            artifacts=store,
            media=_ExportMedia(),  # type: ignore[arg-type]
            source_id="src-drill",
            target="master",
            fmt=ExportFormat.WAV24,
            destination_dir=destination,
        )

    assert list(destination.iterdir()) == []
    assert not (store.staging_dir / "export-drill").exists()


# --- Offline model install and import -------------------------------------


class _OfflineResponse:
    headers: ClassVar[dict[str, str]] = {}

    def read(self, size: int = -1, /) -> bytes:
        return b""

    def close(self) -> None:
        pass


def test_offline_model_install_is_coded_and_leaves_no_partial(tmp_path: Path) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ModelStore(paths)
    calls = 0

    def transport(url: str) -> _OfflineResponse:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError(OSError("no route to host"))

    job = model_job.ModelInstallJob(
        store=store,
        consent=ModelConsent(version=1, acknowledged=True, accepted_at=datetime.now(UTC)),
        transport=transport,  # type: ignore[arg-type]
    )
    context = JobContext(
        job_id="model-offline",
        kind=JobKind.MODEL_INSTALL,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )

    with pytest.raises(StemForgeError) as raised:
        job.run(context)

    assert raised.value.code == errors.SEPARATION_MODEL_DOWNLOAD_FAILED
    assert raised.value.detail == "reason=offline"
    assert raised.value.retryable is True
    assert calls == 1
    assert not store.partial_path().exists()


def test_offline_youtube_classification_is_the_network_code() -> None:
    error = youtube.classify_youtube_error(RuntimeError("connection timed out"))
    assert error.code == errors.IMPORT_NETWORK
    assert error.retryable is True


# --- Disk full -------------------------------------------------------------


def test_disk_full_during_export_is_coded_and_removes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = _store(paths)
    _commit_source(store)
    destination = tmp_path / "exports"
    destination.mkdir()

    def full(_source: Path, partial: Path, _cancel: CancelToken) -> None:
        partial.write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(export_job, "_copy_with_fsync", full)
    context = JobContext(
        job_id="export-full",
        kind=JobKind.EXPORT,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )

    with pytest.raises(StemForgeError) as raised:
        run_export_job(
            context,
            artifacts=store,
            media=_ExportMedia(),  # type: ignore[arg-type]
            source_id="src-drill",
            target="master",
            fmt=ExportFormat.WAV24,
            destination_dir=destination,
        )

    assert raised.value.code == errors.STORAGE_DISK_FULL
    assert raised.value.retryable is True
    assert list(destination.iterdir()) == []
    assert not (store.staging_dir / "export-full").exists()


def test_disk_full_during_model_install_is_coded_and_leaves_no_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ModelStore(paths)
    transport_calls = 0

    class _Response:
        headers: ClassVar[dict[str, str]] = {"Content-Length": "4096"}

        def read(self, size: int = -1, /) -> bytes:
            return b""

        def close(self) -> None:
            pass

    def transport(url: str) -> _Response:
        nonlocal transport_calls
        transport_calls += 1
        return _Response()

    real_open = Path.open

    def no_space(self: Path, *args: object, **kwargs: object):
        if self.name.endswith(".partial"):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", no_space)
    job = model_job.ModelInstallJob(
        store=store,
        consent=ModelConsent(version=1, acknowledged=True, accepted_at=datetime.now(UTC)),
        transport=transport,  # type: ignore[arg-type]
    )
    context = JobContext(
        job_id="model-full",
        kind=JobKind.MODEL_INSTALL,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )

    with pytest.raises(StemForgeError) as raised:
        job.run(context)

    assert raised.value.code == errors.STORAGE_DISK_FULL
    assert raised.value.retryable is True
    assert transport_calls == 1
    assert not store.partial_path().exists()
