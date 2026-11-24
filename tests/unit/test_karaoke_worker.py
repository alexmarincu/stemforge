"""The real Karaoke worker: masked centre extraction, two WAVs, no heavyweight imports.

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
    METHOD_VERSIONS,
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
BASS_HZ = 60.0
PRESENCE_HZ = 1_000.0
BASS_AMPLITUDE = 0.3
PRESENCE_AMPLITUDE = 0.1
EDGE_SAMPLES = 2_048


def write_stereo_wav(path: Path, *, seconds: float = 1.0) -> None:
    """L carries centre + side, R carries centre - side."""
    frames = round(seconds * SAMPLE_RATE)
    samples = array("f")
    for frame in range(frames):
        centre = CENTRE_AMPLITUDE * math.sin(2.0 * math.pi * CENTRE_HZ * frame / SAMPLE_RATE)
        side = SIDE_AMPLITUDE * math.sin(2.0 * math.pi * SIDE_HZ * frame / SAMPLE_RATE)
        samples.append(centre + side)
        samples.append(centre - side)
    _write_float_wav(path, samples, frames)


def write_centre_wav(path: Path, *, seconds: float = 1.0) -> None:
    """A two-tone centre mix: bass and a vocal-band presence tone."""
    frames = round(seconds * SAMPLE_RATE)
    samples = array("f")
    for frame in range(frames):
        value = BASS_AMPLITUDE * math.sin(
            2.0 * math.pi * BASS_HZ * frame / SAMPLE_RATE
        ) + PRESENCE_AMPLITUDE * math.sin(2.0 * math.pi * PRESENCE_HZ * frame / SAMPLE_RATE)
        samples.append(value)
        samples.append(value)
    _write_float_wav(path, samples, frames)


def _write_float_wav(path: Path, samples: array[float], frames: int) -> None:
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


def tone_level(samples: tuple[float, ...], channel: int, frequency: float) -> float:
    """The amplitude of one tone in the interior of one channel, via a Hann DFT."""
    start = EDGE_SAMPLES
    count = len(samples) // CHANNELS - 2 * EDGE_SAMPLES
    total = 0.0
    real = 0.0
    imaginary = 0.0
    for index in range(count):
        window = 0.5 - 0.5 * math.cos(2.0 * math.pi * index / max(1, count - 1))
        value = samples[CHANNELS * (start + index) + channel]
        angle = 2.0 * math.pi * frequency * index / SAMPLE_RATE
        real += window * value * math.cos(angle)
        imaginary -= window * value * math.sin(angle)
        total += window
    return 2.0 * math.hypot(real, imaginary) / total


def run_karaoke(
    tmp_path: Path,
    *,
    seconds: float = 1.0,
    stdin: int | None = None,
    env: dict[str, str] | None = None,
    centre_only: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, dict[str, str]]:
    source = tmp_path / "source.wav"
    if centre_only:
        write_centre_wav(source, seconds=seconds)
    else:
        write_stereo_wav(source, seconds=seconds)
    out = tmp_path / "out"
    resolved = (
        env if env is not None else shadow_environment(tmp_path, watch_stdin=stdin is not None)
    )
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
        env=resolved,
        stdin=stdin,
    )
    return process, source, out, resolved


SITECUSTOMIZE = """\
import os
import socket
import sys

_LOOPBACK_HOSTS = {"::1", "127.0.0.1", "localhost"}


def _is_loopback(address):
    if isinstance(address, (bytes, str)):
        return True
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _guard(original):
    def connect(self, address, *args, **kwargs):
        if not _is_loopback(address):
            raise RuntimeError(f"non-loopback network access: {address!r}")
        return original(self, address, *args, **kwargs)

    return connect


socket.socket.connect = _guard(socket.socket.connect)
socket.socket.connect_ex = _guard(socket.socket.connect_ex)


class _BlockedHeavyImport:
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in {"demucs", "torch"}:
            raise RuntimeError(f"{root} imported on the Karaoke path")
        return None


sys.meta_path.insert(0, _BlockedHeavyImport())

_marker = os.environ.get("STEMFORGE_GUARD_MARKER")
if _marker:
    with open(_marker, "w", encoding="utf-8") as _handle:
        _handle.write("guarded\\n")
"""


def events_from(process: subprocess.CompletedProcess[str]) -> list[WorkerEvent]:
    events = []
    for line in process.stdout.splitlines():
        event = parse_event(line)
        if event is not None:
            events.append(event)
    return events


@pytest.mark.unit
def test_karaoke_writes_the_masked_centre_and_the_residual_without_the_side(
    tmp_path: Path,
) -> None:
    process, _source, out, env = run_karaoke(tmp_path)

    assert process.returncode == 0, process.stderr
    events = events_from(process)
    hello = next(event for event in events if event.event == EVENT_HELLO)
    outputs = [event for event in events if event.event == EVENT_OUTPUT]
    done = next(event for event in events if event.event == EVENT_DONE)

    assert hello.method == "karaoke"
    assert hello.engine_version == str(METHOD_VERSIONS["karaoke"])
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

    # Instrumental is the exact residual, so the set reconstructs the input at
    # every sample, edges included.
    for frame in range(frames):
        expected_centre = CENTRE_AMPLITUDE * math.sin(
            2.0 * math.pi * CENTRE_HZ * frame / SAMPLE_RATE
        )
        expected_side = SIDE_AMPLITUDE * math.sin(2.0 * math.pi * SIDE_HZ * frame / SAMPLE_RATE)
        for channel in range(CHANNELS):
            mixed = expected_centre + expected_side * (1 if channel == 0 else -1)
            index = CHANNELS * frame + channel
            assert abs(vocals[index] + instrumental[index] - mixed) <= 1e-5

    # The first and last window are a fallback edge; the interior is the mask.
    worst_vocals = 0.0
    worst_instrumental = 0.0
    for frame in range(EDGE_SAMPLES, frames - EDGE_SAMPLES):
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
            abs(right_side + expected_side),
        )
    assert worst_vocals <= 1e-3
    assert worst_instrumental <= 1e-3

    # The no-model promise: the worker never created a model store.
    assert not (Path(env["STEMFORGE_HOME"]) / "models").exists()


@pytest.mark.unit
def test_karaoke_keeps_the_bass_out_of_vocals_and_the_presence_tone_in(
    tmp_path: Path,
) -> None:
    """A centre bass must leave Vocals: the old mid downmix kept the whole mix."""
    process, _source, out, _env = run_karaoke(tmp_path, centre_only=True)

    assert process.returncode == 0, process.stderr
    _channels, _rate, vocals = read_float_wav(out / "vocals.wav")
    _channels, _rate, instrumental = read_float_wav(out / "instrumental.wav")
    for channel in range(CHANNELS):
        assert tone_level(vocals, channel, PRESENCE_HZ) > PRESENCE_AMPLITUDE * 0.95
        assert tone_level(vocals, channel, BASS_HZ) < BASS_AMPLITUDE * 0.01
        assert tone_level(instrumental, channel, BASS_HZ) > BASS_AMPLITUDE * 0.95
        assert tone_level(instrumental, channel, PRESENCE_HZ) < PRESENCE_AMPLITUDE * 0.01


@pytest.mark.unit
def test_karaoke_succeeds_under_a_zero_network_no_heavy_stack_guard(tmp_path: Path) -> None:
    """The worker neither connects nor imports torch or Demucs."""
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    marker = tmp_path / "guard-loaded"
    env = shadow_environment(tmp_path)
    env["PYTHONPATH"] = f"{harness}{os.pathsep}{env['PYTHONPATH']}"
    env["STEMFORGE_GUARD_MARKER"] = str(marker)

    process, _source, out, _env = run_karaoke(tmp_path, env=env)

    assert marker.read_text(encoding="utf-8") == "guarded\n"
    assert process.returncode == 0, process.stderr
    assert {path.name for path in out.glob("*.wav")} == {"vocals.wav", "instrumental.wav"}
    assert not list(out.glob("*.partial*"))
    vocals_channels, vocals_rate, vocals = read_float_wav(out / "vocals.wav")
    instrumental_channels, instrumental_rate, instrumental = read_float_wav(
        out / "instrumental.wav"
    )
    assert (vocals_channels, vocals_rate) == (CHANNELS, SAMPLE_RATE)
    assert (instrumental_channels, instrumental_rate) == (CHANNELS, SAMPLE_RATE)
    assert max(abs(value) for value in vocals) > 0
    assert max(abs(value) for value in instrumental) > 0


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
