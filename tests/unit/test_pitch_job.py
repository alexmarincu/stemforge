"""The Pitch Job's disk-space gate (ticket 05 §4, ticket 07 §8)."""

from __future__ import annotations

import hashlib
import logging
from collections import namedtuple
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind, RenderScope
from stemforge.engines.media import MediaInfo
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.pitch_job import run_pitch_job
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

SOURCE_ID = "pitch-disk-source"
LOG = logging.getLogger("test.pitch-disk")


def _context() -> JobContext:
    return JobContext(
        job_id="pitch-disk-job",
        kind=JobKind.RENDER_PITCH,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )


def _store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    staging = store.stage("source-job")
    data = b"canonical-audio"
    (staging / "canonical.wav").write_bytes(data)
    store.commit_source(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": SOURCE_ID,
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
    return store


class _Media:
    def probe(self, path: Path) -> MediaInfo:
        del path
        return MediaInfo("pcm_f32le", 44_100, 2, 60.0, "wav", {})

    def encode_preview(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("a render must not encode after a disk check fails")


class _Pitch:
    version = "4.0.0"

    def shift(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("a render must not shift after a disk check fails")

    def sum_stems(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("not reached")

    def peak_dbfs(self, *args: object, **kwargs: object) -> float:
        raise AssertionError("not reached")


@pytest.mark.unit
def test_a_full_cache_volume_fails_before_any_render_is_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 0, 100 * 1024**2))

    with pytest.raises(StemForgeError) as caught:
        run_pitch_job(
            _context(),
            artifacts=store,
            pitch=_Pitch(),  # type: ignore[arg-type]
            media=_Media(),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            semitones=3,
            scope=RenderScope.ORIGINAL,
        )

    assert caught.value.code == errors.STORAGE_DISK_FULL
    assert list(store.iter_renders()) == []
    assert list(store.staging_dir.iterdir()) == []
    assert store.find_source(SOURCE_ID) is not None
