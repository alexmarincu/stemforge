"""A fast fake Separation worker used by the Separation Job integration test.

It speaks the worker JSONL protocol and writes five real float32 WAVs — the
four stems plus their in-memory sum as Instrumental — with distinct tones, so the
Job's ffmpeg probing, gain, previews, manifest, and commit are exercised through
a real subprocess without torch, Demucs, or the 84 MB weights.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import struct
import sys
import time
from pathlib import Path

SAMPLE_RATE = 44_100
CHANNELS = 2
STEMS = ("vocals", "drums", "bass", "other")
NON_VOCAL = ("drums", "bass", "other")
OUTPUTS = (*STEMS, "instrumental")
FREQUENCIES = {"vocals": 440.0, "drums": 330.0, "bass": 110.0, "other": 220.0}


def emit(payload: dict[str, object]) -> None:
    print(json.dumps({"v": 1, **payload}, sort_keys=True), flush=True)


def hello() -> None:
    emit(
        {
            "event": "hello",
            "engine": "fake",
            "engine_version": "1.0",
            "model": "htdemucs",
            "model_revision": "fake-revision",
            "device": "cpu",
            "sample_rate": SAMPLE_RATE,
            "threads": 2,
        }
    )


def samples_for(frequency: float, *, frames: int, amplitude: float = 0.2) -> list[float]:
    values: list[float] = []
    for frame in range(frames):
        value = amplitude * math.sin(2.0 * math.pi * frequency * frame / SAMPLE_RATE)
        values.extend([value] * CHANNELS)
    return values


def write_samples(
    path: Path, samples: list[float], *, frames: int, channels: int = CHANNELS
) -> None:
    data = struct.pack(f"<{len(samples)}f", *samples)
    byte_rate = SAMPLE_RATE * channels * 4
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<HHIIHH", header, 20, 3, channels, SAMPLE_RATE, byte_rate, channels * 4, 32)
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


def progress(fraction: float, *, frames: int) -> None:
    emit(
        {
            "event": "progress",
            "stage": "separate",
            "fraction": fraction,
            "segment": max(1, round(fraction * 4)),
            "segments": 4,
            "audio_seconds_done": round(frames / SAMPLE_RATE * fraction, 3),
            "audio_seconds_total": round(frames / SAMPLE_RATE, 3),
            "eta_seconds": 0.0,
        }
    )


def emit_outputs(paths: dict[str, Path]) -> None:
    for role in OUTPUTS:
        emit(
            {
                "event": "output",
                "stem": role,
                "path": paths[role].name,
                "format": "wav",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="ok")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--expected-duration", type=float, default=1.0)
    parser.add_argument("--marker", type=Path, default=None)
    parser.add_argument("--input", type=Path, default=Path("input.wav"))
    parser.add_argument("--model-path", type=Path, default=Path("weights"))
    args, _unknown = parser.parse_known_args()
    if args.marker is not None:
        args.marker.write_text("spawned\n", encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(args.expected_duration * SAMPLE_RATE))
    hello()
    emit({"event": "stage", "stage": "model_load", "state": "start"})
    emit({"event": "stage", "stage": "model_load", "state": "end", "seconds": 0.01})
    emit({"event": "stage", "stage": "separate", "state": "start"})
    progress(0.5, frames=frames)

    if args.mode == "hang":
        write_samples(args.out / "vocals.partial.wav", [0.0, 0.0, 0.0, 0.0], frames=1)
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read(1)
        emit({"event": "error", "code": "cancelled", "detail": "stdin closed"})
        return 2
    if args.mode == "slow":
        time.sleep(30)
        return 0

    stems: dict[str, list[float]] = {
        role: samples_for(FREQUENCIES[role], frames=frames) for role in STEMS
    }
    paths: dict[str, Path] = {}
    for role in STEMS:
        target = args.out / f"{role}.wav"
        if args.mode == "bad-output" and role == "drums":
            write_samples(target, stems[role], frames=frames, channels=1)
        else:
            write_samples(target, stems[role], frames=frames)
        paths[role] = target
    instrumental = [
        sum(values) for values in zip(stems["drums"], stems["bass"], stems["other"], strict=True)
    ]
    paths["instrumental"] = args.out / "instrumental.wav"
    write_samples(paths["instrumental"], instrumental, frames=frames)
    emit_outputs(paths)
    progress(1.0, frames=frames)
    emit({"event": "stage", "stage": "separate", "state": "end", "seconds": 0.1})
    emit({"event": "done", "elapsed_seconds": 0.2, "outputs": list(OUTPUTS)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
