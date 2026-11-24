"""Integration: the Separation Job around a real fake worker and real FFmpeg.

The worker is a subprocess script that writes four float32 stem WAVs with
distinct tones. The Job's model re-verification, protocol, probing, instrumental
sum, presentation gain, previews, manifest, and atomic promotion all run for
real; no torch, Demucs, weights, or network are involved.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import subprocess
import sys
import threading
from array import array
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.engines.media import FfmpegTool
from stemforge.engines.separation import WorkerSeparationEngine
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.models import ModelSpec, ModelStore
from stemforge.pipeline.separation_job import SeparationSet, run_separation_job
from stemforge.platform import binaries
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.integration

LOG = logging.getLogger("test.separation-job")
FAKE_WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "fake_separation_worker.py"
STEMS = ("vocals", "drums", "bass", "other")
NON_VOCAL = ("drums", "bass", "other")
WEIGHTS = b"stemforge fake htdemucs weights for integration tests\n" * 4


def _run(argv: Sequence[str | Path]) -> None:
    result = subprocess.run(
        [str(argument) for argument in argv],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="session")
def ffmpeg_path() -> Path:
    try:
        return binaries.ffmpeg()
    except FileNotFoundError as error:  # pragma: no cover - layer prerequisite
        pytest.fail(f"ffmpeg is required for this layer and was not found: {error}")


@pytest.fixture(scope="session")
def media(ffmpeg_path: Path) -> FfmpegTool:
    return FfmpegTool()


@pytest.fixture
def tone(ffmpeg_path: Path, tmp_path: Path) -> Path:
    target = tmp_path / "canonical-source.wav"
    _run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.2*sin(2*PI*220*t)|0.2*sin(2*PI*330*t):s=44100:d=1:c=stereo",
            "-c:a",
            "pcm_f32le",
            target,
        ]
    )
    return target


@pytest.fixture
def centre(ffmpeg_path: Path, tmp_path: Path) -> Path:
    target = tmp_path / "centre-source.wav"
    _run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.2*sin(2*PI*220*t)|0.2*sin(2*PI*220*t):s=44100:d=1:c=stereo",
            "-c:a",
            "pcm_f32le",
            target,
        ]
    )
    return target


def make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(AppPaths.for_home(tmp_path / "home").ensure())


def make_models(store: ArtifactStore) -> ModelStore:
    spec = ModelSpec(
        name="htdemucs",
        repo_id="adefossez/HTDemucs",
        revision="cbc8a9b1a87023b7fd74e7b3412e6321c0eab003",
        filename="fake.safetensors",
        size_bytes=len(WEIGHTS),
        sha256=hashlib.sha256(WEIGHTS).hexdigest(),
        download_url="https://example.invalid/htdemucs.safetensors",
        source_url="https://example.invalid/htdemucs",
        licence="test only",
        licence_notice="test only",
    )
    return ModelStore(store.paths, spec=spec)


def entry(path: Path, role: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def commit_source(
    store: ArtifactStore,
    canonical: Path,
    source_id: str = "src-separation",
    *,
    original_channels: int | None = None,
    stereo_layout: str | None = None,
) -> str:
    staging = store.stage("job-source")
    target = staging / "canonical.wav"
    target.write_bytes(canonical.read_bytes())
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "source",
        "source_id": source_id,
        "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
        "metadata": {"title": "Separation Fixture", "artist": "StemForge Tests"},
        "files": [entry(target, "canonical")],
    }
    if original_channels is not None:
        manifest["original_channels"] = original_channels
    if stereo_layout is not None:
        manifest["stereo_layout"] = stereo_layout
    store.commit_source(staging, source_id, manifest)
    return source_id


def job_context(
    job_id: str,
    *,
    cancel: CancelToken | None = None,
    events: list[tuple[float, str | None, str | None]] | None = None,
) -> JobContext:
    def update(fraction: float, stage: str | None, detail: str | None) -> None:
        if events is not None:
            events.append((fraction, stage, detail))

    return JobContext(
        job_id=job_id,
        kind=JobKind.SEPARATE,
        cancel=cancel if cancel is not None else CancelToken(),
        log=LOG,
        update=update,
    )


def fake_engine(mode: str, marker: Path | None = None) -> WorkerSeparationEngine:
    argv = [sys.executable, str(FAKE_WORKER), "--mode", mode]
    if marker is not None:
        argv.extend(["--marker", str(marker)])
    return WorkerSeparationEngine(argv=argv, poll_interval_s=0.01, cancel_grace_s=2.0)


def separate(
    store: ArtifactStore,
    engine: WorkerSeparationEngine,
    media: FfmpegTool,
    models: ModelStore,
    source_id: str,
    *,
    method: str = "demucs",
    job_id: str = "job-separation",
    events: list[tuple[float, str | None, str | None]] | None = None,
    cancel: CancelToken | None = None,
) -> SeparationSet:
    return run_separation_job(
        job_context(job_id, events=events, cancel=cancel),
        artifacts=store,
        engine=engine,
        media=media,
        models=models,
        source_id=source_id,
        method=method,
        threads=1,
        title="Separation Fixture",
        artist="StemForge Tests",
    )


def karaoke_engine() -> WorkerSeparationEngine:
    return WorkerSeparationEngine(
        argv=[sys.executable, "-m", "stemforge.workers.separation"],
        poll_interval_s=0.01,
        cancel_grace_s=2.0,
    )


class _UnusedModelStore(ModelStore):
    """A model store that fails the test if the Karaoke Job reads it."""

    def require_verified(self) -> object:
        raise AssertionError("Karaoke read the model store")

    def status(self) -> object:
        raise AssertionError("Karaoke read the model store")

    @property
    def spec(self) -> object:
        raise AssertionError("Karaoke read the model store")


def read_float_wav(path: Path) -> tuple[int, int, tuple[float, ...]]:
    data = path.read_bytes()
    assert data[:4] == b"RIFF", path
    assert data[8:12] == b"WAVE", path
    offset = 12
    fmt: tuple[int, int] | None = None
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body = data[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt ":
            tag, channels, rate = struct.unpack("<HHI", body[:8])
            assert tag == 3, f"not float32: {tag}"
            fmt = (channels, rate)
        elif chunk_id == b"data":
            assert fmt is not None
            channels, rate = fmt
            values = array("f")
            values.frombytes(body[: len(body) - (len(body) % 4)])
            return channels, rate, tuple(values)
        offset += 8 + size + (size & 1)
    raise AssertionError(f"WAV has no data chunk: {path}")


def assert_instrumental_is_the_non_vocal_sum(artifact_dir: Path) -> None:
    channels, rate, instrumental = read_float_wav(artifact_dir / "instrumental.wav")
    assert channels == 2
    assert rate == 44_100
    sums = [0.0] * len(instrumental)
    for role in NON_VOCAL:
        stem_channels, stem_rate, samples = read_float_wav(artifact_dir / f"{role}.wav")
        assert (stem_channels, stem_rate) == (channels, rate)
        assert len(samples) == len(instrumental)
        for index, value in enumerate(samples):
            sums[index] += value
    worst = max(abs(value - expected) for value, expected in zip(instrumental, sums, strict=True))
    assert worst <= 1e-3


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


def test_four_stems_and_instrumental_commit_atomically(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)
    events: list[tuple[float, str | None, str | None]] = []
    marker = tmp_path / "worker-spawned"

    result = separate(
        store,
        fake_engine("ok", marker),
        media,
        models,
        source_id,
        events=events,
    )

    assert marker.is_file()
    assert result.artifact.source_id == source_id
    assert not (store.staging_dir / "job-separation").exists()
    assert result.artifact.directory == store.sources_dir / source_id / "separations" / "demucs"
    assert result.artifact.method == "demucs"
    assert result.artifact.method_version == 1
    assert result.artifact.manifest_path.is_file()

    for role in (*STEMS, "instrumental"):
        path = result.artifact.path_for(role)
        assert path is not None, role
        assert path.is_file(), role
        info = media.probe(path)
        assert info.codec == "pcm_f32le"
        assert info.sample_rate == 44_100
        assert info.channels == 2
        assert abs(info.duration_s - 1.0) <= 0.050
        assert result.master(role) == path

    for role in (*STEMS, "instrumental"):
        preview = result.artifact.path_for(f"{role}_preview")
        assert preview is not None, role
        assert preview.is_file(), role
        info = media.probe(preview)
        assert info.codec == "mp3"
        assert abs(info.duration_s - 1.0) <= 0.050

    assert_instrumental_is_the_non_vocal_sum(result.artifact.directory)

    manifest = result.artifact.manifest
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "separation"
    assert manifest["method"] == "demucs"
    assert manifest["method_version"] == 1
    assert manifest["engine"] == "demucs"
    assert manifest["engine_version"] == "4.1.0"
    assert manifest["model"] == "htdemucs"
    assert manifest["model_revision"] == models.spec.revision
    assert manifest["weights_sha256"] == models.spec.sha256
    assert manifest["instrumental"].startswith("float32 sum")
    assert isinstance(manifest["gain_db"], float)
    plan = manifest["block_plan"]
    assert isinstance(plan, dict)
    assert plan["shifts"] == 0
    assert plan["block_seconds"] == 480.0
    assert plan["crossfade_seconds"] == 1.0
    fingerprint = manifest["block_plan_fingerprint"]
    assert isinstance(fingerprint, str)
    assert len(fingerprint) == 64
    assert fingerprint == plan["fingerprint"]
    assert manifest["validation"]["ok"] is True

    assert not list(result.artifact.directory.glob("*.partial*"))
    assert sorted(result.stems) == sorted(STEMS)
    assert sorted(result.previews) == sorted([*STEMS, "instrumental"])

    fractions = [fraction for fraction, _stage, _detail in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    stages = {stage for _fraction, stage, _detail in events}
    assert {
        "Verify",
        "Separate",
        "Validate",
        "Gain",
        "Previews",
        "Saving",
        "Done",
    } <= stages


def test_karaoke_commits_two_outputs_without_reading_the_model_store(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone, original_channels=2, stereo_layout="stereo")
    models = _UnusedModelStore(store.paths)
    events: list[tuple[float, str | None, str | None]] = []

    result = separate(
        store,
        karaoke_engine(),
        media,
        models,
        source_id,
        method="karaoke",
        events=events,
    )

    assert result.artifact.directory == store.sources_dir / source_id / "separations" / "karaoke"
    assert result.artifact.method == "karaoke"
    assert result.artifact.method_version == 2
    assert tuple(result.outputs) == ("vocals", "instrumental")
    assert tuple(result.stems) == ("vocals",)
    assert not (store.paths.models / "htdemucs").exists()
    assert not (store.staging_dir / "job-separation").exists()

    for role in ("vocals", "instrumental"):
        master = result.master(role)
        assert master is not None
        assert master.is_file()
        info = media.probe(master)
        assert info.codec == "pcm_f32le"
        assert info.sample_rate == 44_100
        assert info.channels == 2
        assert abs(info.duration_s - 1.0) <= 0.050

        preview = result.preview(role)
        assert preview is not None
        assert preview.is_file()
        assert media.probe(preview).codec == "mp3"

    manifest = result.artifact.manifest
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "separation"
    assert manifest["method"] == "karaoke"
    assert manifest["method_version"] == 2
    assert manifest["engine"] == "karaoke"
    assert manifest["engine_version"] == "2"
    assert manifest["validation"]["ok"] is True
    assert isinstance(manifest["gain_db"], float)
    assert isinstance(manifest["peak_dbfs"], float)
    for demucs_field in (
        "model",
        "model_revision",
        "weights_sha256",
        "device",
        "block_plan",
        "block_plan_fingerprint",
        "instrumental",
    ):
        assert demucs_field not in manifest
    assert len(manifest["files"]) == 4

    fractions = [fraction for fraction, _stage, _detail in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    stages = {stage for _fraction, stage, _detail in events}
    assert {"Verify", "Separate", "Validate", "Gain", "Previews", "Saving", "Done"} <= stages


def test_karaoke_refuses_a_recorded_mono_source_before_staging(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone, original_channels=1, stereo_layout="mono")
    models = _UnusedModelStore(store.paths)

    with pytest.raises(StemForgeError) as raised:
        separate(
            store,
            karaoke_engine(),
            media,
            models,
            source_id,
            method="karaoke",
            job_id="job-mono",
        )

    assert raised.value.code == errors.SEPARATION_METHOD_UNSUPPORTED
    assert raised.value.detail == "reason=mono"
    assert raised.value.context["reason"] == "mono"
    assert not (store.staging_dir / "job-mono").exists()
    assert store.separation_sets(source_id) == ()


def test_karaoke_refuses_a_legacy_dual_mono_source_after_measuring_the_side_signal(
    tmp_path: Path,
    centre: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, centre)
    models = _UnusedModelStore(store.paths)

    with pytest.raises(StemForgeError) as raised:
        separate(
            store,
            karaoke_engine(),
            media,
            models,
            source_id,
            method="karaoke",
            job_id="job-legacy",
        )

    assert raised.value.code == errors.SEPARATION_METHOD_UNSUPPORTED
    assert raised.value.detail == "reason=dual_mono"
    assert raised.value.context["reason"] == "dual_mono"
    assert not (store.staging_dir / "job-legacy").exists()
    assert store.separation_sets(source_id) == ()


def test_karaoke_allows_a_legacy_stereo_source_after_measuring_the_side_signal(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)

    result = separate(
        store,
        karaoke_engine(),
        media,
        _UnusedModelStore(store.paths),
        source_id,
        method="karaoke",
        job_id="job-legacy-stereo",
    )

    assert result.artifact.method == "karaoke"
    assert store.find_separation(source_id, method="karaoke") is not None


def test_demucs_still_runs_on_a_recorded_mono_source(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone, original_channels=1, stereo_layout="mono")
    models = make_models(store)
    models.install_bytes(WEIGHTS)

    result = separate(
        store,
        fake_engine("ok"),
        media,
        models,
        source_id,
        job_id="job-demucs-mono",
    )

    assert result.artifact.method == "demucs"
    assert store.find_separation(source_id, method="demucs") is not None


def test_demucs_and_karaoke_sets_survive_side_by_side(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)

    demucs = separate(store, fake_engine("ok"), media, models, source_id, job_id="job-demucs")
    karaoke = separate(
        store,
        karaoke_engine(),
        media,
        models,
        source_id,
        method="karaoke",
        job_id="job-karaoke",
    )

    assert demucs.artifact.directory != karaoke.artifact.directory
    assert demucs.artifact.directory.is_dir()
    assert karaoke.artifact.directory.is_dir()
    assert [artifact.method for artifact in store.separation_sets(source_id)] == [
        "demucs",
        "karaoke",
    ]
    assert store.find_separation(source_id, method="demucs") is not None
    assert store.find_separation(source_id, method="karaoke") is not None
    assert not (store.staging_dir / "job-demucs").exists()
    assert not (store.staging_dir / "job-karaoke").exists()


def test_re_separation_replaces_the_previous_set(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)

    first = separate(store, fake_engine("ok"), media, models, source_id, job_id="job-first")
    second = separate(store, fake_engine("ok"), media, models, source_id, job_id="job-second")

    assert first.artifact.directory == second.artifact.directory
    assert store.find_separation(source_id) is not None
    assert not (store.staging_dir / "job-first").exists()
    assert not (store.staging_dir / "job-second").exists()


# ---------------------------------------------------------------------------
# Cancel and model refusal
# ---------------------------------------------------------------------------


def test_cancel_discards_staging_and_keeps_the_last_successful_set(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)
    first = separate(store, fake_engine("ok"), media, models, source_id, job_id="job-first")

    cancel = CancelToken()
    timer = threading.Timer(0.3, cancel.cancel)
    timer.start()
    try:
        with pytest.raises(JobCancelled):
            separate(
                store,
                fake_engine("hang"),
                media,
                models,
                source_id,
                job_id="job-cancelled",
                cancel=cancel,
            )
    finally:
        timer.cancel()

    assert not (store.staging_dir / "job-cancelled").exists()
    current = store.find_separation(source_id)
    assert current is not None
    assert current.manifest["created_at"] == first.artifact.manifest["created_at"]
    assert not list(first.artifact.directory.rglob("*.partial*"))


def test_a_missing_model_is_refused_before_the_worker_spawns(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    marker = tmp_path / "worker-spawned"

    with pytest.raises(StemForgeError) as raised:
        separate(store, fake_engine("ok", marker), media, models, source_id)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.context["reason"] == "absent"
    assert not marker.exists()
    assert not (store.staging_dir / "job-separation").exists()
    assert store.find_separation(source_id) is None


def test_a_tampered_model_is_refused_before_the_worker_spawns(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)
    models.path().write_bytes(WEIGHTS + b"tampered")
    marker = tmp_path / "worker-spawned"

    with pytest.raises(StemForgeError) as raised:
        separate(store, fake_engine("ok", marker), media, models, source_id)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert raised.value.context["reason"] == "checksum"
    assert not marker.exists()
    assert not models.path().exists()
    assert not (store.staging_dir / "job-separation").exists()


def test_invalid_worker_output_fails_output_invalid_and_discards_staging(
    tmp_path: Path,
    tone: Path,
    media: FfmpegTool,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    models = make_models(store)
    models.install_bytes(WEIGHTS)

    with pytest.raises(StemForgeError) as raised:
        separate(store, fake_engine("bad-output"), media, models, source_id)

    assert raised.value.code == errors.SEPARATION_OUTPUT_INVALID
    assert "drums" in (raised.value.detail or "")
    assert not (store.staging_dir / "job-separation").exists()
    assert store.find_separation(source_id) is None


def test_block_plan_fingerprint_changes_with_the_engine_plan() -> None:
    from stemforge.pipeline.separation_job import block_plan

    baseline = block_plan(
        duration_s=60.0,
        source_sha256="a" * 64,
        weights_sha256="b" * 64,
    )
    changed = block_plan(
        duration_s=60.0,
        source_sha256="a" * 64,
        weights_sha256="c" * 64,
    )

    assert baseline["fingerprint"] != changed["fingerprint"]
    assert baseline["blocks"] == 1


def test_block_count_uses_eight_minute_blocks_with_a_one_second_hop() -> None:
    from stemforge.pipeline.separation_job import block_plan

    plan = block_plan(duration_s=1800.0)

    assert plan["block_seconds"] == 480.0
    assert plan["crossfade_seconds"] == 1.0
    assert plan["blocks"] == 4
