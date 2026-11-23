"""The worker protocol: JSONL schema, exit-code mapping, cancel, and timeout.

The parent is exercised against a real subprocess running
``tests/fixtures/stub_worker.py``, so the parser, reader threads, process tree
termination, and error mapping all cross a process boundary. No torch or Demucs
is involved and no external binary is needed, so this is a unit-layer test.
"""

from __future__ import annotations

import logging
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.engines.separation import SeparationOutputs, WorkerSeparationEngine
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.worker_protocol import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_HELLO,
    EVENT_OUTPUT,
    EVENT_PROGRESS,
    EVENT_STAGE,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    METHOD_OUTPUT_ROLES,
    PROTOCOL_VERSION,
    ProtocolError,
    format_event,
    is_safe_relative_output,
    parse_event,
)

pytestmark = pytest.mark.unit

STUB_WORKER = Path(__file__).resolve().parents[1] / "fixtures" / "stub_worker.py"
LOG = logging.getLogger("test.worker-protocol")

DEMUCS_OUTPUTS = METHOD_OUTPUT_ROLES["demucs"]
KARAOKE_OUTPUTS = METHOD_OUTPUT_ROLES["karaoke"]

HELLO = (
    '{"v":2,"event":"hello","engine":"demucs","engine_version":"4.1.0",'
    '"method":"demucs","outputs":["vocals","instrumental","drums","bass","other"],'
    '"model":"htdemucs","model_revision":"cbc8a9b","device":"cpu",'
    '"sample_rate":44100,"threads":8}'
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_format_event_adds_the_protocol_version_and_round_trips() -> None:
    line = format_event(
        {
            "event": EVENT_PROGRESS,
            "stage": "separate",
            "fraction": 0.25,
            "segment": 2,
            "segments": 4,
            "audio_seconds_done": 10.0,
            "audio_seconds_total": 40.0,
            "eta_seconds": 30.0,
        }
    )

    assert line.endswith("\n")
    assert '"v":2' in line
    event = parse_event(line)
    assert event is not None
    assert event.event == EVENT_PROGRESS
    assert event.fraction == 0.25
    assert event.stage == "separate"
    assert event.segment == 2
    assert event.segments == 4
    assert event.audio_seconds_done == 10.0
    assert event.audio_seconds_total == 40.0
    assert event.eta_seconds == 30.0


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (HELLO, EVENT_HELLO),
        ('{"v":2,"event":"stage","stage":"model_load","state":"start"}', EVENT_STAGE),
        (
            '{"v":2,"event":"stage","stage":"model_load","state":"end","seconds":1.98}',
            EVENT_STAGE,
        ),
        (
            '{"v":2,"event":"progress","stage":"separate","fraction":0.5,'
            '"segment":1,"segments":2,"audio_seconds_done":1.0,'
            '"audio_seconds_total":2.0,"eta_seconds":1.0}',
            EVENT_PROGRESS,
        ),
        (
            '{"v":2,"event":"output","stem":"vocals","path":"vocals.wav",'
            '"format":"wav","sample_rate":44100,"channels":2}',
            EVENT_OUTPUT,
        ),
        (
            '{"v":2,"event":"done","elapsed_seconds":1.5,"outputs":'
            '["vocals","instrumental","drums","bass","other"]}',
            EVENT_DONE,
        ),
        ('{"v":2,"event":"error","code":"separation.engine_error"}', EVENT_ERROR),
    ],
)
def test_known_events_parse(line: str, expected: str) -> None:
    event = parse_event(line)
    assert event is not None
    assert event.event == expected


def test_the_hello_declares_the_method_and_its_output_roles() -> None:
    event = parse_event(HELLO)

    assert event is not None
    assert event.method == "demucs"
    assert event.declared_outputs == DEMUCS_OUTPUTS
    assert event.outputs == ()


def test_output_roles_are_validated_against_the_hello_by_the_parent() -> None:
    event = parse_event(
        '{"v":2,"event":"output","stem":"guitar","path":"guitar.wav",'
        '"format":"wav","sample_rate":44100,"channels":2}'
    )

    assert event is not None
    assert event.stem == "guitar"


def test_unknown_events_are_ignored_for_forward_compatibility() -> None:
    assert parse_event('{"v":2,"event":"future","payload":{}}') is None


@pytest.mark.parametrize(
    ("line", "match"),
    [
        ("", "empty"),
        ("not json", "non-JSON"),
        ("[]", "not a JSON object"),
        ('{"event":"hello"}', "integer v"),
        ('{"v":1,"event":"hello"}', "unsupported protocol version"),
        ('{"v":2}', "no event name"),
        (
            '{"v":2,"event":"hello","engine":"demucs","method":"demucs",'
            '"outputs":["vocals"],"sample_rate":44100,"threads":2}',
            "engine_version",
        ),
        (
            '{"v":2,"event":"hello","engine":"demucs","engine_version":"1",'
            '"outputs":["vocals"],"sample_rate":44100,"threads":2}',
            "method",
        ),
        (
            '{"v":2,"event":"hello","engine":"demucs","engine_version":"1",'
            '"method":"demucs","outputs":"vocals","sample_rate":44100,"threads":2}',
            "outputs",
        ),
        (
            '{"v":2,"event":"hello","engine":"demucs","engine_version":"1",'
            '"method":"demucs","outputs":["vocals","vocals"],"sample_rate":44100,"threads":2}',
            "duplicate",
        ),
        ('{"v":2,"event":"stage","stage":"model_load","state":"middle"}', "state"),
        ('{"v":2,"event":"stage","state":"start"}', "stage"),
        ('{"v":2,"event":"progress","stage":"separate"}', "fraction"),
        ('{"v":2,"event":"progress","stage":"separate","fraction":1.5}', "0..1"),
        (
            '{"v":2,"event":"progress","stage":"separate","fraction":0.5,"segment":2,"segments":1}',
            "exceeds",
        ),
        (
            '{"v":2,"event":"output","stem":"","path":"vocals.wav",'
            '"format":"wav","sample_rate":44100,"channels":2}',
            "stem",
        ),
        (
            '{"v":2,"event":"output","stem":"vocals","path":"/tmp/vocals.wav",'
            '"format":"wav","sample_rate":44100,"channels":2}',
            "unsafe",
        ),
        (
            '{"v":2,"event":"output","stem":"vocals","path":"../vocals.wav",'
            '"format":"wav","sample_rate":44100,"channels":2}',
            "unsafe",
        ),
        (
            '{"v":2,"event":"output","stem":"vocals","path":"vocals.wav",'
            '"format":"mp3","sample_rate":44100,"channels":2}',
            "format",
        ),
        ('{"v":2,"event":"done","elapsed_seconds":1.0}', "outputs"),
        ('{"v":2,"event":"done","outputs":["vocals"]}', "elapsed"),
        (
            '{"v":2,"event":"done","elapsed_seconds":1.0,"outputs":["vocals","vocals"]}',
            "duplicate",
        ),
        ('{"v":2,"event":"error"}', "no code"),
        ('{"v":2,"event":"error","code":"x","retryable":"yes"}', "boolean"),
    ],
)
def test_malformed_events_raise_protocol_error(line: str, match: str) -> None:
    with pytest.raises(ProtocolError, match=match):
        parse_event(line)


def test_protocol_version_is_two() -> None:
    assert PROTOCOL_VERSION == 2


def test_block_count_is_the_one_plan_shared_by_parent_and_worker() -> None:
    from stemforge.worker_protocol import block_count

    sample_rate = 44_100
    assert block_count(total_frames=0, block_frames=480 * sample_rate, fade_frames=sample_rate) == 0
    assert (
        block_count(
            total_frames=sample_rate, block_frames=480 * sample_rate, fade_frames=sample_rate
        )
        == 1
    )
    assert (
        block_count(
            total_frames=1800 * sample_rate,
            block_frames=480 * sample_rate,
            fade_frames=sample_rate,
        )
        == 4
    )


@pytest.mark.parametrize(
    ("path", "safe"),
    [
        ("vocals.wav", True),
        ("instrumental.wav", True),
        ("sub/vocals.wav", False),
        ("../vocals.wav", False),
        ("/vocals.wav", False),
        ("C:/vocals.wav", False),
        ("vocals.mp3", False),
        ("", False),
    ],
)
def test_only_bare_wav_names_are_safe_outputs(path: str, safe: bool) -> None:
    assert is_safe_relative_output(path) is safe


# ---------------------------------------------------------------------------
# Parent against the stub worker
# ---------------------------------------------------------------------------


def write_input(path: Path, *, frames: int = 4410, sample_rate: int = 44_100) -> Path:
    data = struct.pack(f"<{frames * 2}f", *([0.1] * (frames * 2)))
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<HHIIHH", header, 20, 3, 2, sample_rate, sample_rate * 8, 8, 32)
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)
    return path


@pytest.fixture
def harness(tmp_path: Path) -> tuple[WorkerSeparationEngine, Path, Path, Path]:
    weights = tmp_path / "weights.safetensors"
    weights.write_bytes(b"not really weights")
    canonical = write_input(tmp_path / "canonical.wav")
    out = tmp_path / "out"
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "ok"],
        poll_interval_s=0.01,
        cancel_grace_s=1.0,
    )
    return engine, canonical, out, weights


def run_engine(
    engine: WorkerSeparationEngine,
    canonical: Path,
    out: Path,
    weights: Path | None,
    *,
    method: str = "demucs",
    expected_outputs: tuple[str, ...] = DEMUCS_OUTPUTS,
    cancel: CancelToken | None = None,
    timeout_s: float | None = None,
    progress: list[tuple[float, str, str]] | None = None,
) -> SeparationOutputs:
    sink = progress if progress is not None else []
    return engine.separate(
        canonical,
        out,
        method=method,
        expected_outputs=expected_outputs,
        model_path=weights,
        expected_duration_s=0.1,
        progress=lambda fraction, stage, detail: sink.append((fraction, stage, detail)),
        cancel=cancel if cancel is not None else CancelToken(),
        log=LOG,
        timeout_s=timeout_s,
    )


def test_ok_returns_exactly_the_declared_outputs_and_monotonic_progress(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    events: list[tuple[float, str, str]] = []

    outputs = run_engine(engine, canonical, out, weights, progress=events)

    assert tuple(outputs.outputs) == DEMUCS_OUTPUTS
    assert all(path.is_file() for path in outputs.outputs.values())
    fractions = [fraction for fraction, _stage, _detail in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert all(0.0 <= fraction <= 1.0 for fraction in fractions)
    assert "Separate" in {stage for _fraction, stage, _detail in events}


def test_a_two_output_method_returns_only_its_declared_outputs(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, _weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "ok", "--method", "karaoke"],
        poll_interval_s=0.01,
    )

    outputs = run_engine(
        engine,
        canonical,
        out,
        None,
        method="karaoke",
        expected_outputs=KARAOKE_OUTPUTS,
    )

    assert tuple(outputs.outputs) == KARAOKE_OUTPUTS
    assert outputs.path_for("drums") is None


def test_an_undeclared_output_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "undeclared-output"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "undeclared" in (raised.value.detail or "")


def test_a_done_set_that_differs_from_the_declaration_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "done-mismatch"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "done.outputs" in (raised.value.detail or "")


def test_a_hello_method_that_differs_from_the_request_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "method-mismatch"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "method" in (raised.value.detail or "")


def test_a_hello_that_declares_other_roles_than_the_request_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "roles-mismatch"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights, method="demucs")

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "roles" in (raised.value.detail or "")


def test_an_output_before_hello_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "output-before-hello"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "hello" in (raised.value.detail or "")


def test_unknown_events_are_skipped_without_failing(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "unknown-event"],
        poll_interval_s=0.01,
    )

    outputs = run_engine(engine, canonical, out, weights)

    assert len(outputs.outputs) == 5


def test_a_non_json_line_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "malformed"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "non-JSON" in (raised.value.detail or "")


def test_a_different_protocol_version_is_a_protocol_mismatch(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "version"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_PROTOCOL_MISMATCH
    assert "version" in (raised.value.detail or "")


def test_a_crash_maps_to_worker_crashed_with_the_stderr_tail(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "crash"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_WORKER_CRASHED
    assert "synthetic crash" in (raised.value.detail or "")
    assert raised.value.context["returncode"] == 7


def test_a_handled_error_keeps_its_code(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "error"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_ENGINE_ERROR
    assert raised.value.detail == "reason=stub"


def test_an_unknown_worker_code_falls_back_to_engine_error(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "error-unknown"],
    )

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == errors.SEPARATION_ENGINE_ERROR
    assert raised.value.context["worker_code"] == "separation.mystery"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("exit2-no-error", errors.SEPARATION_PROTOCOL_MISMATCH),
        ("exit3", errors.SEPARATION_MODEL_MISSING),
        ("exit4", errors.SEPARATION_ENGINE_ERROR),
        ("silent", errors.SEPARATION_PROTOCOL_MISMATCH),
        ("missing-output", errors.SEPARATION_OUTPUT_INVALID),
        ("bad-path", errors.SEPARATION_PROTOCOL_MISMATCH),
    ],
)
def test_exit_and_output_failures_map_to_coded_errors(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
    mode: str,
    expected: str,
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(argv=[sys.executable, str(STUB_WORKER), "--mode", mode])

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights)

    assert raised.value.code == expected


def test_a_missing_weights_file_is_refused_before_spawn(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, _weights = harness
    missing = out.parent / "absent.safetensors"

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, missing)

    assert raised.value.code == errors.SEPARATION_MODEL_MISSING
    assert not out.exists()


def test_cancel_closes_stdin_and_raises_job_cancelled(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "hang"],
        poll_interval_s=0.01,
        cancel_grace_s=2.0,
    )
    cancel = CancelToken()
    timer = threading.Timer(0.2, cancel.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(JobCancelled):
            run_engine(engine, canonical, out, weights, cancel=cancel)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 10.0
    assert not (out / "vocals.wav").exists()


def test_a_timeout_terminates_the_worker(
    harness: tuple[WorkerSeparationEngine, Path, Path, Path],
) -> None:
    engine, canonical, out, weights = harness
    engine = WorkerSeparationEngine(
        argv=[sys.executable, str(STUB_WORKER), "--mode", "slow"],
        poll_interval_s=0.01,
    )
    started = time.monotonic()

    with pytest.raises(StemForgeError) as raised:
        run_engine(engine, canonical, out, weights, timeout_s=0.4)

    assert raised.value.code == errors.SEPARATION_ENGINE_ERROR
    assert "timeout" in (raised.value.detail or "")
    assert time.monotonic() - started < 10.0


def test_the_default_development_command_is_the_worker_module() -> None:
    engine = WorkerSeparationEngine()
    argv = engine.command(
        Path("in.wav"),
        Path("out"),
        method="demucs",
        model_path=Path("weights.safetensors"),
        expected_duration_s=1.0,
        threads=2,
    )

    assert "stemforge.workers.separation" in argv
    assert "--input" in argv
    assert argv[argv.index("--method") + 1] == "demucs"
    assert "--model-path" in argv
    assert "--block-seconds" in argv
    assert argv[argv.index("--threads") + 1] == "2"
    assert EXIT_OK == 0


def test_a_karaoke_command_passes_no_model_or_demucs_block_flags() -> None:
    engine = WorkerSeparationEngine()
    argv = engine.command(
        Path("in.wav"),
        Path("out"),
        method="karaoke",
        expected_duration_s=1.0,
        threads=2,
    )

    assert argv[argv.index("--method") + 1] == "karaoke"
    for flag in ("--model", "--model-path", "--model-revision", "--device", "--block-seconds"):
        assert flag not in argv


@pytest.mark.unit
def test_the_entry_point_dispatches_worker_flags_without_touching_the_app(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from stemforge.__main__ import main

    assert main(["--worker", "separation", "--input", "in.wav", "--out", "out"]) == (
        EXIT_INVALID_INPUT
    )
    output = capsys.readouterr().out.strip()
    event = parse_event(output.splitlines()[0])
    assert event is not None
    assert event.event == EVENT_ERROR
    assert "model-path" in (event.detail or "")


@pytest.mark.unit
def test_an_unknown_worker_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    from stemforge.__main__ import main

    assert main(["--worker", "nope"]) == 4
    assert "unknown worker" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Block math and the float WAV writer
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_block_plan_covers_every_frame_exactly_once_with_one_second_overlap() -> None:
    from stemforge.workers.separation import _plan_blocks

    sample_rate = 44_100
    block = 480 * sample_rate
    fade = 1 * sample_rate
    total = 1800 * sample_rate

    blocks = _plan_blocks(total, block, fade)

    assert len(blocks) == 4
    assert blocks[0].start == 0
    assert [item.start for item in blocks] == [index * (block - fade) for index in range(4)]
    assert all(item.length == block for item in blocks[:-1])
    written = sum(item.length for item in blocks) - fade * (len(blocks) - 1)
    assert written == total


@pytest.mark.unit
def test_a_short_track_is_a_single_block() -> None:
    from stemforge.workers.separation import _plan_blocks

    blocks = _plan_blocks(44_100, 480 * 44_100, 44_100)

    assert len(blocks) == 1
    assert blocks[0].length == 44_100


@pytest.mark.unit
def test_the_worker_sums_instrumental_in_memory_not_with_ffmpeg() -> None:
    import numpy

    from stemforge.workers.separation import _instrumental

    current = {
        "vocals": numpy.array([[1.0, 1.0]], dtype="float32"),
        "drums": numpy.array([[0.25, -0.5]], dtype="float32"),
        "bass": numpy.array([[0.5, 0.25]], dtype="float32"),
        "other": numpy.array([[-0.25, 0.25]], dtype="float32"),
    }

    instrumental = _instrumental(numpy, current)

    assert instrumental.dtype == numpy.float32
    assert instrumental.tolist() == [[0.5, 0.0]]


@pytest.mark.unit
def test_the_float_wav_writer_round_trips_without_clamping(tmp_path: Path) -> None:
    from stemforge.workers.separation import _FloatWavWriter, _read_wav_info

    path = tmp_path / "out.wav"
    writer = _FloatWavWriter(path, sample_rate=44_100)
    samples = [[1.5, -1.5], [0.25, -0.25], [0.0, 0.0]]
    import numpy

    writer.write(numpy.array(samples, dtype="float32"))
    writer.close()

    info = _read_wav_info(path)
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert info.frames == 3
    values = numpy.frombuffer(path.read_bytes()[info.data_offset :], dtype="<f4")
    assert values.tolist() == [1.5, -1.5, 0.25, -0.25, 0.0, 0.0]
