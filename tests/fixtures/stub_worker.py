"""A scripted JSONL worker for protocol tests (ticket 11 §1, ticket 06 §4).

It is a plain Python script: no torch, no Demucs, no real audio work. Each mode
emits a different stream or exit code so the parent's parser, progress mapping,
error mapping, cancel, and timeout paths are exercised through a real process
boundary.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import struct
import sys
import time
from pathlib import Path

SAMPLE_RATE = 44_100
CHANNELS = 2
OUTPUTS = ("vocals", "drums", "bass", "other", "instrumental")


def emit(payload: dict[str, object]) -> None:
    print(json.dumps({"v": 1, **payload}, sort_keys=True), flush=True)


def hello() -> None:
    emit(
        {
            "event": "hello",
            "engine": "stub",
            "engine_version": "1.0",
            "model": "htdemucs",
            "model_revision": "test-revision",
            "device": "cpu",
            "sample_rate": SAMPLE_RATE,
            "threads": 2,
        }
    )


def write_wav(path: Path, *, frames: int = 4410, sample_rate: int = SAMPLE_RATE) -> None:
    data = struct.pack(f"<{frames * CHANNELS}f", *([0.1] * (frames * CHANNELS)))
    byte_rate = sample_rate * CHANNELS * 4
    header = bytearray(56)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, 48 + len(data))
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<HHIIHH", header, 20, 3, CHANNELS, sample_rate, byte_rate, 8, 32)
    header[36:40] = b"fact"
    struct.pack_into("<I", header, 40, 4)
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


def write_outputs(out: Path, *, extra_frames: int = 0) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for role in OUTPUTS:
        target = out / f"{role}.wav"
        write_wav(target, frames=4410 + extra_frames)
        paths[role] = target
    return paths


def progress(fraction: float) -> None:
    emit(
        {
            "event": "progress",
            "stage": "separate",
            "fraction": fraction,
            "segment": max(1, round(fraction * 4)),
            "segments": 4,
            "audio_seconds_done": round(0.1 * fraction, 3),
            "audio_seconds_total": 0.1,
            "eta_seconds": 0.0,
        }
    )


def mode_ok(out: Path) -> int:
    hello()
    emit({"event": "stage", "stage": "model_load", "state": "start"})
    emit({"event": "stage", "stage": "model_load", "state": "end", "seconds": 0.01})
    emit({"event": "stage", "stage": "separate", "state": "start"})
    progress(0.5)
    paths = write_outputs(out)
    for role, path in paths.items():
        emit(
            {
                "event": "output",
                "stem": role,
                "path": path.name,
                "format": "wav",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
            }
        )
    progress(1.0)
    emit({"event": "stage", "stage": "separate", "state": "end", "seconds": 0.01})
    emit({"event": "done", "elapsed_seconds": 0.01, "outputs": list(OUTPUTS)})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="ok")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    args, _unknown = parser.parse_known_args()
    out = args.out
    mode = args.mode

    if mode == "ok":
        return mode_ok(out)
    if mode == "unknown-event":
        emit({"event": "future", "anything": True})
        return mode_ok(out)
    if mode == "version":
        print(json.dumps({"v": 2, "event": "hello"}), flush=True)
        return 0
    if mode == "malformed":
        print("this is not json", flush=True)
        return 0
    if mode == "crash":
        hello()
        progress(0.2)
        print("stub worker: synthetic crash", file=sys.stderr, flush=True)
        os._exit(7)
    if mode == "error":
        hello()
        emit(
            {
                "event": "error",
                "code": "separation.engine_error",
                "message": "synthetic failure",
                "detail": "reason=stub",
                "retryable": False,
            }
        )
        return 2
    if mode == "error-unknown":
        hello()
        emit({"event": "error", "code": "separation.mystery", "detail": "unknown"})
        return 2
    if mode == "hang":
        hello()
        progress(0.2)
        write_wav(out / "vocals.partial.wav")
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read(1)
        emit({"event": "error", "code": "cancelled", "detail": "stdin closed"})
        return 2
    if mode == "slow":
        hello()
        progress(0.1)
        time.sleep(30)
        return 0
    if mode == "silent":
        return 0
    if mode == "missing-output":
        hello()
        paths = write_outputs(out)
        for role in ("vocals", "drums", "bass", "other"):
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
        emit({"event": "done", "elapsed_seconds": 0.01, "outputs": list(OUTPUTS)})
        return 0
    if mode == "bad-path":
        hello()
        emit(
            {
                "event": "output",
                "stem": "vocals",
                "path": "../escape.wav",
                "format": "wav",
                "sample_rate": SAMPLE_RATE,
                "channels": CHANNELS,
            }
        )
        return 0
    if mode == "exit2-no-error":
        return 2
    if mode == "exit3":
        return 3
    if mode == "exit4":
        return 4
    print(f"stub worker: unknown mode {mode}", file=sys.stderr)
    return 9


if __name__ == "__main__":
    raise SystemExit(main())
