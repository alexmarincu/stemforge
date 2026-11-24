"""The stems Render follows the Separation method's own set and output shape."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind, RenderScope
from stemforge.engines.media import MediaInfo
from stemforge.engines.pitch import render_key_for
from stemforge.jobs.job import JobContext
from stemforge.pipeline.pitch_job import run_pitch_job
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.unit

SOURCE_ID = "pitch-method-source"
LOG = logging.getLogger("test.pitch-method")


class _Media:
    def probe(self, path: Path) -> MediaInfo:
        del path
        return MediaInfo("pcm_f32le", 44_100, 2, 60.0, "wav", {})

    def encode_preview(
        self,
        src: Path,
        dst: Path,
        *,
        gain_db: float,
        title: str,
        artist: str,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        del gain_db, title, artist, cancel, log
        dst.write_bytes(b"preview:" + src.read_bytes())


class _Pitch:
    version = "4.0.0"

    def __init__(self) -> None:
        self.shifts: list[Path] = []
        self.sums: list[list[Path]] = []

    def shift(
        self,
        src: Path,
        dst: Path,
        *,
        semitones: int,
        cancel: CancelToken,
        log: logging.Logger,
        progress: object | None = None,
    ) -> None:
        del semitones, cancel, log, progress
        self.shifts.append(src)
        dst.write_bytes(b"shift:" + src.read_bytes())

    def sum_stems(
        self,
        srcs: list[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        del cancel, log
        self.sums.append(list(srcs))
        dst.write_bytes(b"sum:" + b"|".join(path.read_bytes() for path in srcs))

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        del path, cancel, log
        return -3.0


def _context() -> JobContext:
    return JobContext(
        job_id="pitch-method-job",
        kind=JobKind.RENDER_PITCH,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )


def _file_entry(name: str, role: str, data: bytes) -> Mapping[str, object]:
    return {
        "path": name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


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
            "files": [_file_entry("canonical.wav", "canonical", data)],
        },
    )
    return store


def _commit_separation(
    store: ArtifactStore,
    *,
    method: str,
    roles: tuple[str, ...],
) -> Path:
    descriptor_label = "karaoke" if method == "karaoke" else "demucs"
    staging = store.stage(f"separation-{method}")
    files: list[Mapping[str, object]] = []
    for role in roles:
        data = f"{descriptor_label}-{role}".encode()
        (staging / f"{role}.wav").write_bytes(data)
        files.append(_file_entry(f"{role}.wav", role, data))
    artifact = store.commit_separation(
        staging,
        SOURCE_ID,
        {
            "schema_version": 1,
            "kind": "separation",
            "source_id": SOURCE_ID,
            "files": files,
        },
        method=method,
    )
    return artifact.directory


@pytest.mark.unit
def test_a_karaoke_render_shifts_its_two_stored_outputs_without_summing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_separation(store, method="karaoke", roles=("vocals", "instrumental"))
    pitch = _Pitch()

    rendered = run_pitch_job(
        _context(),
        artifacts=store,
        pitch=pitch,
        media=_Media(),  # type: ignore[arg-type]
        source_id=SOURCE_ID,
        semitones=3,
        scope=RenderScope.STEMS,
        separation_method="karaoke",
    )

    assert sorted(rendered.masters) == ["instrumental", "vocals"]
    assert set(rendered.previews) == set(rendered.masters)
    assert pitch.sums == []
    assert [path.name for path in pitch.shifts] == ["vocals.wav", "instrumental.wav"]
    assert rendered.masters["instrumental"].read_bytes().startswith(b"shift:karaoke-instrumental")
    assert rendered.separation_method == "karaoke"
    assert rendered.output_roles == ("vocals", "instrumental")

    manifest = rendered.artifact.manifest
    assert manifest["separation_method"] == "karaoke"
    assert manifest["separation_method_version"] == 4
    assert manifest["output_roles"] == ["vocals", "instrumental"]
    assert isinstance(manifest["separation_set_key"], str)
    assert [entry["role"] for entry in manifest["inputs"]] == ["vocals", "instrumental"]


@pytest.mark.unit
def test_a_demucs_render_keeps_summing_its_shifted_non_vocal_stems(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_separation(store, method="demucs", roles=("vocals", "drums", "bass", "other"))
    pitch = _Pitch()

    rendered = run_pitch_job(
        _context(),
        artifacts=store,
        pitch=pitch,
        media=_Media(),  # type: ignore[arg-type]
        source_id=SOURCE_ID,
        semitones=3,
        scope=RenderScope.STEMS,
        separation_method="demucs",
    )

    assert sorted(rendered.masters) == ["bass", "drums", "instrumental", "other", "vocals"]
    assert len(pitch.sums) == 1
    assert [path.name for path in pitch.sums[0]] == ["drums.wav", "bass.wav", "other.wav"]
    assert rendered.masters["instrumental"].read_bytes().startswith(b"sum:")
    assert rendered.separation_method == "demucs"
    assert rendered.output_roles == ("vocals", "instrumental", "drums", "bass", "other")
    assert rendered.artifact.manifest["separation_method"] == "demucs"


@pytest.mark.unit
def test_a_stems_render_never_reads_another_methods_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_separation(store, method="demucs", roles=("vocals", "drums", "bass", "other"))

    with pytest.raises(ValueError, match="Karaoke"):
        run_pitch_job(
            _context(),
            artifacts=store,
            pitch=_Pitch(),
            media=_Media(),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            semitones=3,
            scope=RenderScope.STEMS,
            separation_method="karaoke",
        )

    assert list(store.iter_renders()) == []
    assert list(store.staging_dir.iterdir()) == []


@pytest.mark.unit
def test_a_stems_render_requires_a_method(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _commit_separation(store, method="demucs", roles=("vocals", "drums", "bass", "other"))

    with pytest.raises(ValueError, match="method"):
        run_pitch_job(
            _context(),
            artifacts=store,
            pitch=_Pitch(),
            media=_Media(),  # type: ignore[arg-type]
            source_id=SOURCE_ID,
            semitones=3,
            scope=RenderScope.STEMS,
        )


@pytest.mark.unit
def test_an_original_render_carries_no_separation_method(tmp_path: Path) -> None:
    store = _store(tmp_path)

    rendered = run_pitch_job(
        _context(),
        artifacts=store,
        pitch=_Pitch(),
        media=_Media(),  # type: ignore[arg-type]
        source_id=SOURCE_ID,
        semitones=3,
        scope=RenderScope.ORIGINAL,
    )

    assert rendered.separation_method is None
    assert rendered.output_roles == ("original",)
    assert "separation_method" not in rendered.artifact.manifest
    assert "separation_set_key" not in rendered.artifact.manifest


@pytest.mark.unit
def test_the_render_key_separates_methods_at_the_same_semitone() -> None:
    inputs = [hashlib.sha256(b"a").hexdigest()]

    def key(method: str) -> str:
        return render_key_for(
            source_id=SOURCE_ID,
            scope=RenderScope.STEMS,
            semitones=3,
            input_hashes=inputs,
            engine_version="4.0.0",
            separation_method=method,
        )

    assert key("demucs") != key("karaoke")
    assert key("karaoke") == key("karaoke")
