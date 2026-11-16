"""Integration: the Rubber Band adapter against generated sine fixtures.

Every fixture is produced at test time by the real ffmpeg; the repo commits no
audio. Frequency is measured from interpolated positive zero crossings of the
left channel, an independent estimate that never reads the expected value
(ticket 10 acceptance: 440 x 2^(N/12) within 0.15 semitone, 50 ms, stereo).
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import struct
import subprocess
import threading
import time
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind, RenderScope
from stemforge.engines.media import FfmpegTool
from stemforge.engines.pitch import RubberBandEngine, file_sha256, shifted_frequency
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.jobs.job import JobContext
from stemforge.pipeline.pitch_job import PitchRenderSet, find_pitch_render, run_pitch_job
from stemforge.platform import binaries
from stemforge.platform.paths import AppPaths

pytestmark = pytest.mark.integration

LOG = logging.getLogger("test.pitch")

TONE_HZ = 440.0
TONE_DURATION_S = 2.0
SEMITONE_TOLERANCE = 0.15
DURATION_TOLERANCE_S = 0.050


# --- Fixtures --------------------------------------------------------------


@dataclass(frozen=True)
class Wav:
    """A decoded float32 WAV: rate, channels, and interleaved samples."""

    sample_rate: int
    channels: int
    samples: tuple[float, ...]

    @property
    def frames(self) -> int:
        return len(self.samples) // self.channels

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate


def read_wav(path: Path) -> Wav:
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
            format_tag, channels, rate = struct.unpack("<HHI", body[:8])
            assert format_tag in (3, 0xFFFE), f"not float32: format tag {format_tag}"
            fmt = (channels, rate)
        elif chunk_id == b"data":
            assert fmt is not None, "data chunk before fmt chunk"
            channels, rate = fmt
            values = array("f")
            values.frombytes(body[: len(body) - (len(body) % 4)])
            return Wav(rate, channels, tuple(values))
        offset += 8 + size + (size & 1)
    raise AssertionError(f"WAV has no data chunk: {path}")


def measure_frequency(wav: Wav, *, start_s: float = 0.1, end_s: float = 1.9) -> float:
    """Estimate the fundamental from interpolated positive zero crossings."""
    start = max(1, int(start_s * wav.sample_rate))
    end = min(wav.frames - 1, int(end_s * wav.sample_rate))
    crossings: list[float] = []
    previous = wav.samples[(start - 1) * wav.channels]
    for frame in range(start, end):
        current = wav.samples[frame * wav.channels]
        if previous <= 0.0 < current:
            crossings.append((frame - 1) + (-previous) / (current - previous))
        previous = current
    assert len(crossings) >= 2, "not enough zero crossings to measure a frequency"
    span = crossings[-1] - crossings[0]
    return (len(crossings) - 1) * wav.sample_rate / span


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
def ffprobe_path() -> Path:
    try:
        return binaries.ffprobe()
    except FileNotFoundError as error:  # pragma: no cover - layer prerequisite
        pytest.fail(f"ffprobe is required for this layer and was not found: {error}")


@pytest.fixture(scope="session")
def rubberband_path() -> Path:
    resolved = binaries.rubberband()
    if resolved is None:  # pragma: no cover - layer prerequisite
        pytest.fail(
            "Rubber Band 4.0.0 is required for this layer: install it or point "
            "STEMFORGE_RUBBERBAND at the CLI"
        )
    return resolved


@pytest.fixture(scope="session")
def media(ffmpeg_path: Path, ffprobe_path: Path) -> FfmpegTool:
    return FfmpegTool(ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path)


@pytest.fixture(scope="session")
def engine(rubberband_path: Path, media: FfmpegTool) -> RubberBandEngine:
    return RubberBandEngine(rubberband_path=rubberband_path, media=media)


@pytest.fixture(scope="session")
def tone(tmp_path_factory: pytest.TempPathFactory, ffmpeg_path: Path) -> Path:
    """A 2 s stereo 44.1 kHz float32 440 Hz sine."""
    target = tmp_path_factory.mktemp("pitch-fixtures") / "tone-440.wav"
    _run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=sin(2*PI*{TONE_HZ}*t)|sin(2*PI*{TONE_HZ}*t):s=44100:d={TONE_DURATION_S}:c=stereo",
            "-c:a",
            "pcm_f32le",
            target,
        ]
    )
    return target


# --- Engine: pitch accuracy, duration, format ------------------------------


@pytest.mark.parametrize("semitones", [-12, -7, -5, 3, 7, 12])
def test_shift_matches_equal_temperament_and_preserves_tempo(
    engine: RubberBandEngine,
    media: FfmpegTool,
    tone: Path,
    tmp_path: Path,
    semitones: int,
) -> None:
    output = tmp_path / f"tone-{semitones:+d}.wav"

    engine.shift(
        tone,
        output,
        semitones=semitones,
        cancel=CancelToken(),
        log=LOG,
    )

    wav = read_wav(output)
    measured = measure_frequency(wav)
    expected = shifted_frequency(TONE_HZ, semitones)
    error_semitones = 12.0 * math.log2(measured / expected)
    info = media.probe(output)

    print(
        f"{semitones:+3d} st | expected {expected:8.3f} Hz | measured {measured:8.3f} Hz"
        f" | error {error_semitones:+.4f} st | {info.duration_s:.3f} s"
        f" | {info.sample_rate} Hz {info.channels} ch {info.codec}"
    )

    assert abs(error_semitones) <= SEMITONE_TOLERANCE
    assert info.codec == "pcm_f32le"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert abs(info.duration_s - TONE_DURATION_S) <= DURATION_TOLERANCE_S
    assert abs(wav.duration_s - TONE_DURATION_S) <= DURATION_TOLERANCE_S


def test_the_fixture_itself_measures_440_hz(tone: Path) -> None:
    """Control: the zero-crossing estimator must find the generated tone."""
    assert measure_frequency(read_wav(tone)) == pytest.approx(TONE_HZ, rel=0.002)


def test_progress_is_monotonic_and_covers_both_passes(
    engine: RubberBandEngine, tone: Path, tmp_path: Path
) -> None:
    events: list[tuple[float, str, str]] = []

    engine.shift(
        tone,
        tmp_path / "progress.wav",
        semitones=3,
        cancel=CancelToken(),
        log=LOG,
        progress=lambda fraction, stage, detail: events.append((fraction, stage, detail)),
    )

    fractions = [fraction for fraction, _stage, _detail in events]
    stages = {stage for _fraction, stage, _detail in events}
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in fractions)
    assert {"Studying", "Calculating profile", "Processing", "Validating", "Done"} <= stages
    assert fractions[-1] > max(
        fraction for fraction, stage, _detail in events if stage == "Processing"
    )


def test_missing_rubberband_is_a_coded_error(tmp_path: Path, media: FfmpegTool, tone: Path) -> None:
    engine = RubberBandEngine(rubberband_path=tmp_path / "no-such-rubberband", media=media)

    with pytest.raises(StemForgeError) as raised:
        engine.shift(
            tone,
            tmp_path / "out.wav",
            semitones=3,
            cancel=CancelToken(),
            log=LOG,
        )

    assert raised.value.code == errors.PITCH_ENGINE_MISSING
    assert not (tmp_path / "out.wav").exists()
    assert not list(tmp_path.glob("*.partial*"))


def test_a_truncated_input_fails_with_a_coded_error_and_leaves_no_partial(
    engine: RubberBandEngine, tone: Path, tmp_path: Path
) -> None:
    truncated = tmp_path / "truncated.wav"
    truncated.write_bytes(tone.read_bytes()[:30_000])
    output = tmp_path / "out.wav"

    with pytest.raises(StemForgeError) as raised:
        engine.shift(
            truncated,
            output,
            semitones=3,
            cancel=CancelToken(),
            log=LOG,
        )

    assert raised.value.code in {errors.PITCH_RENDER_FAILED, errors.PITCH_OUTPUT_INVALID}
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial*"))


# --- Render Job: render sets, identity, gain, cancellation ------------------


def tone_at(
    ffmpeg_path: Path,
    target: Path,
    *,
    frequency: float,
    duration: float = 1.0,
    amplitude: float = 0.2,
) -> Path:
    expression = f"{amplitude}*sin(2*PI*{frequency}*t)|{amplitude}*sin(2*PI*{frequency}*t)"
    _run(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc={expression}:s=44100:d={duration}:c=stereo",
            "-c:a",
            "pcm_f32le",
            target,
        ]
    )
    return target


def _entry(path: Path, role: str) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.name,
        "role": role,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(AppPaths.for_home(tmp_path / "home").ensure())


def commit_source(store: ArtifactStore, canonical: Path, source_id: str = "src-tone") -> str:
    staging = store.stage("job-source")
    target = staging / "canonical.wav"
    shutil.copyfile(canonical, target)
    store.commit_source(
        staging,
        source_id,
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": source_id,
            "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
            "metadata": {"title": "Pitch Fixture", "artist": "StemForge Tests"},
            "files": [_entry(target, "canonical")],
        },
    )
    return source_id


def commit_separation(
    store: ArtifactStore,
    media: FfmpegTool,
    source_id: str,
    stems: Mapping[str, Path],
) -> None:
    staging = store.stage("job-separation")
    for role, source in stems.items():
        shutil.copyfile(source, staging / f"{role}.wav")
    media.sum_wavs(
        [staging / "drums.wav", staging / "bass.wav", staging / "other.wav"],
        staging / "instrumental.wav",
        cancel=CancelToken(),
        log=LOG,
    )
    roles = [*stems, "instrumental"]
    store.commit_separation(
        staging,
        source_id,
        {
            "schema_version": 1,
            "kind": "separation",
            "source_id": source_id,
            "created_at": datetime(2026, 11, 16, 12, 0, tzinfo=UTC).isoformat(),
            "files": [_entry(staging / f"{role}.wav", role) for role in roles],
        },
    )


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
        kind=JobKind.RENDER_PITCH,
        cancel=cancel if cancel is not None else CancelToken(),
        log=LOG,
        update=update,
    )


def render(
    store: ArtifactStore,
    engine: RubberBandEngine,
    media: FfmpegTool,
    source_id: str,
    *,
    semitones: int,
    scope: RenderScope,
    job_id: str,
    events: list[tuple[float, str | None, str | None]] | None = None,
) -> PitchRenderSet:
    return run_pitch_job(
        job_context(job_id, events=events),
        artifacts=store,
        pitch=engine,
        media=media,
        source_id=source_id,
        semitones=semitones,
        scope=scope,
        title="Pitch Fixture",
        artist="StemForge Tests",
    )


def quiet_stems(ffmpeg_path: Path, root: Path) -> dict[str, Path]:
    return {
        "vocals": tone_at(ffmpeg_path, root / "vocals.wav", frequency=220.0),
        "drums": tone_at(ffmpeg_path, root / "drums.wav", frequency=330.0),
        "bass": tone_at(ffmpeg_path, root / "bass.wav", frequency=110.0),
        "other": tone_at(ffmpeg_path, root / "other.wav", frequency=550.0),
    }


def test_master_and_stems_renders_have_distinct_keys_and_never_cross_satisfy(
    tmp_path: Path,
    engine: RubberBandEngine,
    media: FfmpegTool,
    tone: Path,
    ffmpeg_path: Path,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    commit_separation(store, media, source_id, quiet_stems(ffmpeg_path, tmp_path))

    master = render(
        store, engine, media, source_id, semitones=3, scope=RenderScope.ORIGINAL, job_id="job-m"
    )
    assert (
        find_pitch_render(store, source_id=source_id, semitones=3, scope=RenderScope.STEMS) is None
    )

    events: list[tuple[float, str | None, str | None]] = []
    stems = render(
        store,
        engine,
        media,
        source_id,
        semitones=3,
        scope=RenderScope.STEMS,
        job_id="job-s",
        events=events,
    )

    assert master.artifact.render_key != stems.artifact.render_key
    assert master.artifact.directory != stems.artifact.directory
    print(
        f"master key {master.artifact.render_key}\n"
        f"stems  key {stems.artifact.render_key}\n"
        f"master dir {master.artifact.directory.name}\n"
        f"stems  dir {stems.artifact.directory.name}"
    )
    assert master.artifact.manifest["scope"] == "original"
    assert master.artifact.manifest["semitones"] == 3
    assert stems.artifact.manifest["scope"] == "stems"
    assert sorted(master.masters) == ["original"]
    assert sorted(stems.masters) == ["bass", "drums", "instrumental", "other", "vocals"]
    assert set(stems.previews) == set(stems.masters)

    fractions = [fraction for fraction, _stage, _detail in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    stages = {stage for _fraction, stage, _detail in events}
    assert {
        "Studying",
        "Processing",
        "Instrumental",
        "Gain",
        "Previews",
        "Saving",
        "Done",
    } <= stages
    assert any(detail == "stem 1 of 4" for _fraction, _stage, detail in events)

    found_master = find_pitch_render(
        store, source_id=source_id, semitones=3, scope=RenderScope.ORIGINAL
    )
    found_stems = find_pitch_render(
        store, source_id=source_id, semitones=3, scope=RenderScope.STEMS
    )
    assert found_master is not None
    assert found_master.artifact.render_key == master.artifact.render_key
    assert found_stems is not None
    assert found_stems.artifact.render_key == stems.artifact.render_key

    repeated = render(
        store, engine, media, source_id, semitones=3, scope=RenderScope.ORIGINAL, job_id="job-m2"
    )
    assert repeated.artifact.render_key == master.artifact.render_key
    assert len(list(store.renders_dir.iterdir())) == 2


def test_shifted_instrumental_equals_the_sum_of_the_shifted_non_vocal_stems(
    tmp_path: Path,
    engine: RubberBandEngine,
    media: FfmpegTool,
    tone: Path,
    ffmpeg_path: Path,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    commit_separation(store, media, source_id, quiet_stems(ffmpeg_path, tmp_path))

    rendered = render(
        store, engine, media, source_id, semitones=3, scope=RenderScope.STEMS, job_id="job-sum"
    )

    instrumental = read_wav(rendered.masters["instrumental"])
    parts = [read_wav(rendered.masters[role]) for role in ("drums", "bass", "other")]
    assert len({part.frames for part in [instrumental, *parts]}) == 1

    worst_instrumental = max(
        abs(sum(part.samples[index] for part in parts) - instrumental.samples[index])
        for index in range(len(instrumental.samples))
    )
    assert worst_instrumental <= 1e-3

    vocals = read_wav(rendered.masters["vocals"])
    all_stems = [vocals, *parts]
    worst_mix = max(
        abs(
            vocals.samples[index]
            + instrumental.samples[index]
            - sum(part.samples[index] for part in all_stems)
        )
        for index in range(len(instrumental.samples))
    )
    assert worst_mix <= 1e-3


def test_a_set_above_full_scale_gets_one_shared_gain_and_keeps_master_headroom(
    tmp_path: Path,
    engine: RubberBandEngine,
    media: FfmpegTool,
    tone: Path,
    ffmpeg_path: Path,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    loud = {
        "vocals": tone_at(
            ffmpeg_path, tmp_path / "loud-vocals.wav", frequency=220.0, amplitude=0.8
        ),
        "drums": tone_at(ffmpeg_path, tmp_path / "loud-drums.wav", frequency=330.0, amplitude=0.8),
        "bass": tone_at(ffmpeg_path, tmp_path / "loud-bass.wav", frequency=330.0, amplitude=0.8),
        "other": tone_at(ffmpeg_path, tmp_path / "loud-other.wav", frequency=330.0, amplitude=0.8),
    }
    commit_separation(store, media, source_id, loud)
    inputs = [path for path in store.sources_dir.joinpath(source_id).rglob("*.wav")]
    before = {path: file_sha256(path) for path in inputs}

    rendered = render(
        store, engine, media, source_id, semitones=3, scope=RenderScope.STEMS, job_id="job-gain"
    )

    gain = rendered.gain_db
    peak = rendered.artifact.manifest["peak_dbfs"]
    assert isinstance(peak, float)
    assert peak > 1.0
    assert gain < 0.0
    assert rendered.artifact.manifest["gain_db"] == gain
    assert rendered.artifact.gain_db == gain

    master_peaks = {
        role: media.peak_dbfs(path, cancel=CancelToken(), log=LOG)
        for role, path in rendered.masters.items()
    }
    for role, master_peak in master_peaks.items():
        assert master_peak + gain <= 0.0 + 1e-6, role
    assert master_peaks["instrumental"] > 1.0

    preview_peaks = {
        role: media.peak_dbfs(path, cancel=CancelToken(), log=LOG)
        for role, path in rendered.previews.items()
    }
    for role, preview_peak in preview_peaks.items():
        assert preview_peak <= 0.0, (role, preview_peak)
    print(f"set peak {peak:+.3f} dBFS | shared gain {gain:+.3f} dB")
    print(
        "master peaks: " + ", ".join(f"{role} {value:+.3f}" for role, value in master_peaks.items())
    )
    print(
        "preview peaks: "
        + ", ".join(f"{role} {value:+.3f}" for role, value in preview_peaks.items())
    )

    assert {path: file_sha256(path) for path in inputs} == before


def test_a_set_below_full_scale_gets_zero_gain_and_keeps_the_masters(
    tmp_path: Path,
    engine: RubberBandEngine,
    media: FfmpegTool,
    tone: Path,
    ffmpeg_path: Path,
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    commit_separation(store, media, source_id, quiet_stems(ffmpeg_path, tmp_path))
    inputs = [path for path in store.sources_dir.joinpath(source_id).rglob("*.wav")]
    before = {path: file_sha256(path) for path in inputs}

    rendered = render(
        store,
        engine,
        media,
        source_id,
        semitones=-5,
        scope=RenderScope.STEMS,
        job_id="job-quiet",
    )

    assert rendered.gain_db == 0.0
    assert rendered.artifact.manifest["gain_db"] == 0.0
    assert all(
        media.peak_dbfs(path, cancel=CancelToken(), log=LOG) <= 0.0
        for path in rendered.masters.values()
    )
    assert {path: file_sha256(path) for path in inputs} == before


class CancellingEngine:
    """A fake adapter that cancels after the first shift and must not be resumed."""

    version = "fake-1.0"

    def __init__(self) -> None:
        self.shifts = 0

    def shift(
        self,
        src: Path,
        dst: Path,
        *,
        semitones: int,
        cancel: CancelToken,
        log: logging.Logger,
        progress: object = None,
    ) -> None:
        shutil.copyfile(src, dst)
        self.shifts += 1
        cancel.cancel()

    def sum_stems(
        self, srcs: Sequence[Path], dst: Path, *, cancel: CancelToken, log: logging.Logger
    ) -> None:
        raise AssertionError("the Job continued after cancellation")

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        raise AssertionError("the Job continued after cancellation")


def test_cancel_mid_render_leaves_no_visible_render_staging_or_partial(
    tmp_path: Path, media: FfmpegTool, tone: Path, ffmpeg_path: Path
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    commit_separation(store, media, source_id, quiet_stems(ffmpeg_path, tmp_path))
    cancelling = CancellingEngine()

    token = CancelToken()
    with pytest.raises(JobCancelled):
        run_pitch_job(
            job_context("job-cancel", cancel=token),
            artifacts=store,
            pitch=cancelling,  # type: ignore[arg-type]
            media=media,
            source_id=source_id,
            semitones=3,
            scope=RenderScope.STEMS,
        )

    assert cancelling.shifts == 1
    assert list(store.renders_dir.iterdir()) == []
    assert not (store.staging_dir / "job-cancel").exists()
    assert list(store.root.rglob("*.partial*")) == []
    assert (
        find_pitch_render(store, source_id=source_id, semitones=3, scope=RenderScope.STEMS) is None
    )


def test_a_missing_rubberband_fails_the_job_without_staging(
    tmp_path: Path, media: FfmpegTool, tone: Path
) -> None:
    store = make_store(tmp_path)
    source_id = commit_source(store, tone)
    missing = RubberBandEngine(rubberband_path=tmp_path / "no-such-rubberband", media=media)

    with pytest.raises(StemForgeError) as raised:
        render(
            store,
            missing,  # type: ignore[arg-type]
            media,
            source_id,
            semitones=3,
            scope=RenderScope.ORIGINAL,
            job_id="job-missing",
        )

    assert raised.value.code == errors.PITCH_ENGINE_MISSING
    assert list(store.renders_dir.iterdir()) == []
    assert list(store.staging_dir.iterdir()) == []


def test_a_broken_binary_override_is_coded_as_engine_missing(
    monkeypatch: pytest.MonkeyPatch, media: FfmpegTool, tone: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv(binaries.RUBBERBAND_ENV, str(tmp_path / "gone"))
    engine = RubberBandEngine(media=media)

    with pytest.raises(StemForgeError) as raised:
        engine.shift(
            tone,
            tmp_path / "out.wav",
            semitones=3,
            cancel=CancelToken(),
            log=LOG,
        )

    assert raised.value.code == errors.PITCH_ENGINE_MISSING
    assert not (tmp_path / "out.wav").exists()


def test_cancelling_the_real_cli_kills_the_process_and_removes_the_partial(
    engine: RubberBandEngine, ffmpeg_path: Path, tmp_path: Path
) -> None:
    long_tone = tone_at(
        ffmpeg_path, tmp_path / "long.wav", frequency=440.0, duration=30.0, amplitude=0.2
    )
    output = tmp_path / "cancelled.wav"
    token = CancelToken()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            engine.shift(long_tone, output, semitones=3, cancel=token, log=LOG)
        except BaseException as error:  # recorded for the assertion
            failure.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 30.0
    while not list(tmp_path.glob("cancelled.partial.wav")):
        assert time.monotonic() < deadline, "the real Rubber Band run never created its partial"
        assert worker.is_alive(), "the render finished before cancellation could land"
        time.sleep(0.02)
    token.cancel()
    worker.join(timeout=30.0)

    assert not worker.is_alive(), "cancellation did not terminate the render"
    assert len(failure) == 1
    assert isinstance(failure[0], JobCancelled)
    assert not output.exists()
    assert not list(tmp_path.glob("*.partial*"))
