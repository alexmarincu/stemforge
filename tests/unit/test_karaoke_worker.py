"""The real Karaoke worker: centre extraction, two WAVs, no heavyweight imports.

The worker runs as a real subprocess. Shadow packages on ``PYTHONPATH`` raise if
torch or Demucs is imported, so the no-model path is enforced structurally rather
than by inspecting the source.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
from array import array
from pathlib import Path

import pytest

from stemforge.worker_protocol import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_HELLO,
    EVENT_OUTPUT,
    METHOD_OUTPUT_ROLES,
    WorkerEvent,
    parse_event,
)

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
SAMPLE_RATE = 44_100
CHANNELS = 2
CENTRE_HZ = 440.0
SIDE_HZ = 660.0
CENTRE_AMPLITUDE = 0.2
SIDE_AMPLITUDE = 0.1


def write_stereo_wav(path: Path, *, seconds: float = 1.0) -> None:
    """L carries centre + side, R carries centre - side."""
    frames = round(seconds * SAMPLE_RATE)
    samples = array("f")
    for frame in range(frames):
        centre = CENTRE_AMPLITUDE * math.sin(2.0 * math.pi * CENTRE_HZ * frame / SAMPLE_RATE)
        side = SIDE_AMPLITUDE * math.sin(2.0 * math.pi * SIDE_HZ * frame / SAMPLE_RATE)
        samples.append(centre + side)
        samples.append(centre - side)
    data = samples.tobytes()
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into(
        "<HHIIHH", header, 20, 3, CHANNELS, SAMPLE_RATE, SAMPLE_RATE * CHANNELS * 4, 8, 32
    )
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


def read_float_wav(path: Path) -> tuple[int, int, tuple[float, ...]]:
    data = path.read_bytes()
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
            values = array("f")
            values.frombytes(body[: len(body) - (len(body) % 4)])
            return fmt[0], fmt[1], tuple(values)
        offset += 8 + size + (size & 1)
    raise AssertionError(f"WAV has no data chunk: {path}")


def shadow_environment(tmp_path: Path, *, watch_stdin: bool = False) -> dict[str, str]:
    """A PYTHONPATH whose torch and Demucs packages fail on import."""
    shadow = tmp_path / "shadow"
    for package in ("torch", "demucs"):
        directory = shadow / package
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text(
            f"raise RuntimeError('{package} imported on the Karaoke path')\n",
            encoding="utf-8",
        )
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(shadow), str(SRC), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["STEMFORGE_HOME"] = str(home)
    if watch_stdin:
        env["STEMFORGE_WORKER_STDIN_WATCH"] = "1"
    return env


def run_karaoke(
    tmp_path: Path,
    *,
    seconds: float = 1.0,
    stdin: int | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, dict[str, str]]:
    source = tmp_path / "source.wav"
    write_stereo_wav(source, seconds=seconds)
    out = tmp_path / "out"
    env = shadow_environment(tmp_path, watch_stdin=stdin is not None)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "stemforge.workers.separation",
            "--method",
            "karaoke",
            "--input",
            str(source),
            "--out",
            str(out),
            "--expected-duration",
            f"{seconds:.3f}",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        stdin=stdin,
    )
    return process, source, out, env


def events_from(process: subprocess.CompletedProcess[str]) -> list[WorkerEvent]:
    events = []
    for line in process.stdout.splitlines():
        event = parse_event(line)
        if event is not None:
            events.append(event)
    return events


@pytest.mark.unit
def test_karaoke_writes_vocals_from_the_centre_and_instrumental_from_the_side(
    tmp_path: Path,
) -> None:
    process, _source, out, env = run_karaoke(tmp_path)

    assert process.returncode == 0, process.stderr
    events = events_from(process)
    hello = next(event for event in events if event.event == EVENT_HELLO)
    outputs = [event for event in events if event.event == EVENT_OUTPUT]
    done = next(event for event in events if event.event == EVENT_DONE)

    assert hello.method == "karaoke"
    assert hello.declared_outputs == METHOD_OUTPUT_ROLES["karaoke"]
    assert {event.stem for event in outputs} == set(METHOD_OUTPUT_ROLES["karaoke"])
    assert done.outputs == METHOD_OUTPUT_ROLES["karaoke"]
    assert not list(out.glob("*.partial*"))

    vocals_channels, vocals_rate, vocals = read_float_wav(out / "vocals.wav")
    instrumental_channels, instrumental_rate, instrumental = read_float_wav(
        out / "instrumental.wav"
    )
    assert (vocals_channels, vocals_rate) == (CHANNELS, SAMPLE_RATE)
    assert (instrumental_channels, instrumental_rate) == (CHANNELS, SAMPLE_RATE)
    frames = round(SAMPLE_RATE * 1.0)
    assert len(vocals) == len(instrumental) == frames * CHANNELS

    worst_vocals = 0.0
    worst_instrumental = 0.0
    for frame in range(frames):
        expected_centre = CENTRE_AMPLITUDE * math.sin(
            2.0 * math.pi * CENTRE_HZ * frame / SAMPLE_RATE
        )
        expected_side = SIDE_AMPLITUDE * math.sin(2.0 * math.pi * SIDE_HZ * frame / SAMPLE_RATE)
        left_vocals, right_vocals = vocals[2 * frame], vocals[2 * frame + 1]
        left_side, right_side = instrumental[2 * frame], instrumental[2 * frame + 1]
        worst_vocals = max(
            worst_vocals,
            abs(left_vocals - expected_centre),
            abs(right_vocals - expected_centre),
        )
        worst_instrumental = max(
            worst_instrumental,
            abs(left_side - expected_side),
            abs(right_side - expected_side),
        )
    assert worst_vocals <= 1e-4
    assert worst_instrumental <= 1e-4

    # The no-model promise: the worker never created a model store.
    assert not (Path(env["STEMFORGE_HOME"]) / "models").exists()


@pytest.mark.unit
def test_closing_stdin_cancels_karaoke_and_removes_the_partials(tmp_path: Path) -> None:
    process, _source, out, _env = run_karaoke(tmp_path, seconds=60.0, stdin=subprocess.PIPE)
    # ``subprocess.run`` writes stdin once at start; closing it immediately is
    # the same EOF the engine uses to request cancellation.
    assert process.returncode == 2, process.stderr
    events = events_from(process)
    error = next(event for event in events if event.event == EVENT_ERROR)
    assert error.code == "cancelled"
    assert not list(out.glob("*.wav"))
    assert not list(out.glob("*.partial*"))
