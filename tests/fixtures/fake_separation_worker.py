"""A fast fake Separation worker used by the Separation Job integration test.

It speaks the ticket 13 JSONL protocol and writes four real float32 stem WAVs
with distinct tones, so the Job's ffmpeg probing, instrumental sum, gain,
previews, manifest, and commit are exercised through a real subprocess without
torch, Demucs, or the 84 MB weights.
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
FREQUENCIES = {"vocals": 440.0, "drums": 330.0, "bass": 110.0, "other": 220.0}


def emit(payload: dict[str, object]) -> None:
    print(json.dumps({"v": 1, **payload}, sort_keys=True), flush=True)


def write_tone(
    path: Path,
    frequency: float,
    *,
    frames: int,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
    amplitude: float = 0.2,
) -> None:
    samples: list[float] = []
    for frame in range(frames):
        value = amplitude * math.sin(2.0 * math.pi * frequency * frame / sample_rate)
        samples.extend([value] * channels)
    data = struct.pack(f"<{len(samples)}f", *samples)
    write_pcm(path, data, frames=frames, sample_rate=sample_rate, channels=channels)


def write_pcm(path: Path, data: bytes, *, frames: int, sample_rate: int, channels: int) -> None:
    byte_rate = sample_rate * channels * 4
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<HHIIHH", header, 20, 3, channels, sample_rate, byte_rate, channels * 4, 32)
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


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
    emit({"event": "ready", "engine": "fake", "engine_version": "1.0"})
    emit({"event": "block", "index": 1, "total": 1, "fraction": 0.0})
    emit({"event": "progress", "stage": "Separate", "fraction": 0.5, "block": 1, "blocks": 1})

    if args.mode == "hang":
        write_pcm(
            args.out / "vocals.partial.wav",
            struct.pack("<4f", 0.0, 0.0, 0.0, 0.0),
            frames=1,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
        )
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read(1)
        emit({"event": "error", "code": "cancelled", "detail": "stdin closed"})
        return 2
    if args.mode == "slow":
        time.sleep(30)
        return 0

    for stem in STEMS:
        target = args.out / f"{stem}.wav"
        if args.mode == "bad-output" and stem == "drums":
            write_tone(target, FREQUENCIES[stem], frames=frames, channels=1)
        else:
            write_tone(target, FREQUENCIES[stem], frames=frames)
        emit({"event": "stem", "stem": stem, "path": target.name})
    emit({"event": "progress", "stage": "Separate", "fraction": 1.0, "block": 1, "blocks": 1})
    emit({"event": "done", "outputs": list(STEMS)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
