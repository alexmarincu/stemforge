"""Integration: the pinned vendor set, exercised without PATH fallbacks.

Marked ``slow`` because it hashes and runs multi-hundred-megabyte binaries; it
skips cleanly when ``vendor/`` is absent (the normal developer checkout) and is
the gate that runs after ``scripts/vendor-fetch.sh``. Every audio fixture is
generated at test time from the vendored FFmpeg; the repository commits none.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
import subprocess
import sys
from array import array
from pathlib import Path

import pytest

from stemforge.domain.cancellation import CancelToken
from stemforge.engines.media import FfmpegTool
from stemforge.engines.pitch import RubberBandEngine
from stemforge.platform import binaries

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not (Path(__file__).resolve().parents[2] / "vendor" / "ffmpeg").is_dir(),
        reason="vendor/ is absent; run scripts/vendor-fetch.sh first",
    ),
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENDOR = PROJECT_ROOT / "vendor"
LOCK_PATH = PROJECT_ROOT / "packaging" / "vendor.lock.json"

TONE_HZ = 440.0
TONE_SECONDS = 1.0
SEMITONE_TOLERANCE = 0.15
DURATION_TOLERANCE_S = 0.050


def _lock() -> dict[str, object]:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _target_name() -> str:
    if sys.platform.startswith("win"):
        return "windows-x64"
    return "linux-x86_64"


def _component(name: str) -> dict[str, object]:
    components = _lock()["components"]
    assert isinstance(components, dict)
    component = components[name]
    assert isinstance(component, dict)
    return component


def _target(name: str) -> dict[str, object]:
    entry = _component(name)["targets"][_target_name()]
    assert isinstance(entry, dict)
    return entry


def _installed(name: str) -> dict[str, Path]:
    """Every installed file for one component, keyed by its vendor-relative path."""
    files: dict[str, Path] = {}
    for item in _target(name)["install"]:
        assert isinstance(item, dict)
        relative = str(item["to"])
        files[relative] = VENDOR / relative
    return files


def _tool(name: str) -> Path:
    return binaries.vendor_path(name, system=sys.platform)  # type: ignore[return-value]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
        check=False,
        env=env,
    )


def _clean_env() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("LD_LIBRARY_PATH", None)
    return environment


def _version_output(path: Path, flag: str) -> str:
    completed = _run([str(path), flag], env=_clean_env())
    assert completed.returncode == 0, completed.stderr
    return completed.stdout or completed.stderr


# --- the tree and the lock -------------------------------------------------


def test_every_locked_file_is_present_executable_and_hash_matching() -> None:
    checked = 0
    for name in ("ffmpeg", "ffprobe", "rubberband", "deno", "libsndfile"):
        entry = _target(name)
        for item in entry["install"]:
            assert isinstance(item, dict)
            path = VENDOR / str(item["to"])
            assert path.is_file(), f"missing {path}"
            if sys.platform.startswith("win"):
                assert path.suffix.lower() == ".exe" or not item["executable"], path
            elif item["executable"]:
                assert os.access(path, os.X_OK), f"not executable: {path}"
            expected = item.get("sha256")
            if expected is not None:
                assert _sha256(path) == expected, path
            checked += 1
    assert checked >= 5


def test_the_vendor_set_contains_no_model_weights() -> None:
    suffixes = {".safetensors", ".pt", ".pth", ".ckpt", ".onnx"}
    files = [path for path in VENDOR.rglob("*") if path.is_file()]
    assert files
    assert not [path for path in files if path.suffix.lower() in suffixes]


# --- versions -------------------------------------------------------------


def test_ffmpeg_and_ffprobe_report_the_locked_version() -> None:
    expected = str(_component("ffmpeg")["version"])
    assert expected.startswith("n9.0.2")
    for name in ("ffmpeg", "ffprobe"):
        output = _version_output(VENDOR / _tool(name), "-version")
        assert expected.removeprefix("n") in output
        assert f"{name} version" in output


def test_deno_reports_the_locked_version() -> None:
    output = _version_output(VENDOR / _tool("deno"), "--version")
    first = output.splitlines()[0]
    assert first.startswith(f"deno {_component('deno')['version']} (stable, release, ")


def test_rubberband_reports_the_locked_version_from_a_clean_loader_environment() -> None:
    output = _version_output(VENDOR / _tool("rubberband"), "--version").strip()
    expected = str(_component("rubberband")["version"])
    assert output in {expected, f"Rubber Band {expected}"}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="ELF loader inspection is Linux-only")
def test_rubberband_loads_the_co_located_sndfile() -> None:
    completed = _run(["ldd", str(VENDOR / _tool("rubberband"))], env=_clean_env())
    assert completed.returncode == 0, completed.stderr
    line = next((item for item in completed.stdout.splitlines() if "libsndfile.so.1" in item), None)
    assert line is not None, completed.stdout
    resolved = Path(line.split("=>", 1)[1].split("(", 1)[0].strip()).resolve()
    assert resolved == (VENDOR / "rubberband" / "libsndfile.so.1").resolve()


# --- the application paths -------------------------------------------------


def _read_f32_wav(path: Path) -> tuple[int, int, tuple[float, ...]]:
    data = path.read_bytes()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    offset = 12
    fmt: tuple[int, int] | None = None
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        body = data[offset + 8 : offset + 8 + size]
        if chunk_id == b"fmt ":
            tag, channels, rate = struct.unpack("<HHI", body[:8])
            assert tag in (3, 0xFFFE), f"not float32: {path}"
            fmt = (channels, rate)
        elif chunk_id == b"data":
            assert fmt is not None
            channels, rate = fmt
            values = array("f")
            values.frombytes(body[: len(body) - (len(body) % 4)])
            return channels, rate, tuple(values)
        offset += 8 + size + (size & 1)
    raise AssertionError(f"WAV has no data chunk: {path}")


def _measure_frequency(channels: int, rate: int, samples: tuple[float, ...]) -> float:
    start = max(1, int(0.1 * rate))
    end = min(len(samples) // channels - 1, int(0.9 * rate))
    crossings: list[float] = []
    previous = samples[(start - 1) * channels]
    for frame in range(start, end):
        current = samples[frame * channels]
        if previous <= 0.0 < current:
            crossings.append((frame - 1) + (-previous) / (current - previous))
        previous = current
    assert len(crossings) >= 2
    return (len(crossings) - 1) * rate / (crossings[-1] - crossings[0])


def test_media_and_pitch_run_through_the_environment_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = VENDOR / _tool("ffmpeg")
    ffprobe = VENDOR / _tool("ffprobe")
    rubberband = VENDOR / _tool("rubberband")

    # Simulate a checkout without vendor/ so the environment override is the
    # resolver, exactly as a developer points tests at a locally built set.
    monkeypatch.setattr(binaries, "bundle_root", lambda: tmp_path)
    monkeypatch.setenv(binaries.FFMPEG_ENV, str(ffmpeg))
    monkeypatch.setenv(binaries.FFPROBE_ENV, str(ffprobe))
    monkeypatch.setenv(binaries.RUBBERBAND_ENV, str(rubberband))
    assert binaries.ffmpeg() == ffmpeg
    assert binaries.ffprobe() == ffprobe
    assert binaries.rubberband() == rubberband

    tone = tmp_path / "tone.wav"
    completed = _run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=sin(2*PI*{TONE_HZ}*t)|sin(2*PI*{TONE_HZ}*t)"
            f":s=44100:d={TONE_SECONDS}:c=stereo",
            "-c:a",
            "pcm_f32le",
            str(tone),
        ],
        env=_clean_env(),
    )
    assert completed.returncode == 0, completed.stderr

    media = FfmpegTool(ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    info = media.probe(tone)
    assert info.codec == "pcm_f32le"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert abs(info.duration_s - TONE_SECONDS) <= DURATION_TOLERANCE_S

    engine = RubberBandEngine(rubberband_path=rubberband, media=media)
    shifted = tmp_path / "tone-plus-3.wav"
    engine.shift(
        tone,
        shifted,
        semitones=3,
        cancel=CancelToken(),
        log=logging.getLogger("test.vendor.pitch"),
    )

    output = media.probe(shifted)
    assert output.codec == "pcm_f32le"
    assert output.sample_rate == 44_100
    assert output.channels == 2
    assert abs(output.duration_s - TONE_SECONDS) <= DURATION_TOLERANCE_S

    channels, rate, samples = _read_f32_wav(shifted)
    measured = _measure_frequency(channels, rate, samples)
    expected = TONE_HZ * math.pow(2.0, 3.0 / 12.0)
    error_semitones = 12.0 * math.log2(measured / expected)
    assert abs(error_semitones) <= SEMITONE_TOLERANCE
