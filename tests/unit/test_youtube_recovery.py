"""YouTube recovery never trusts a native file that fails its recorded hash."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.engines.youtube import parse_youtube_url
from stemforge.errors import StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.youtube_job import run_youtube_import
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

VIDEO = parse_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
LOG = logging.getLogger("test.youtube-recovery")


def _context() -> JobContext:
    return JobContext(
        job_id="youtube-recovery-job",
        kind=JobKind.IMPORT_YOUTUBE,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )


def _entry(name: str, role: str, data: bytes) -> dict[str, object]:
    return {
        "path": name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _store_with_broken_native(tmp_path: Path) -> tuple[ArtifactStore, Path]:
    paths = AppPaths.for_home(tmp_path / "home").ensure()
    store = ArtifactStore(paths)
    staging = store.stage("source-job")
    native = b"the native bytes"
    canonical = b"canonical audio"
    preview = b"preview audio"
    (staging / "native.webm").write_bytes(native)
    (staging / "canonical.wav").write_bytes(canonical)
    (staging / "original.mp3").write_bytes(preview)
    artifact = store.commit_source(
        staging,
        VIDEO.source_id,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": VIDEO.source_id,
            "metadata": {"title": "Video"},
            "files": [
                _entry("native.webm", "native", native),
                _entry("canonical.wav", "canonical", canonical),
                _entry("original.mp3", "preview", preview),
            ],
        },
    )
    (artifact.path_for("native") or Path()).write_bytes(b"!" * len(native))
    assert store.find_source(VIDEO.source_id) is None
    return store, artifact.directory


class _NoNetwork:
    def resolve(self, *args: object, **kwargs: object) -> None:
        raise StemForgeError(errors.IMPORT_NETWORK, "network is not available in this test")


@pytest.mark.unit
def test_a_native_hash_mismatch_deletes_the_broken_cache_entry(tmp_path: Path) -> None:
    store, source_dir = _store_with_broken_native(tmp_path)

    with pytest.raises(StemForgeError) as caught:
        run_youtube_import(
            _context(),
            VIDEO,
            artifacts=store,
            media=object(),  # type: ignore[arg-type]
            youtube=_NoNetwork(),  # type: ignore[arg-type]
            finalize=lambda source: None,  # type: ignore[arg-type,return-value]
            sleep=lambda seconds: None,
        )

    assert caught.value.code == errors.IMPORT_NETWORK
    assert not source_dir.exists()
