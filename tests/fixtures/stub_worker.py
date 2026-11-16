"""A scripted JSONL worker for protocol tests (ticket 11 §1, ticket 13).

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
STEMS = ("vocals", "drums", "bass", "other")


def emit(payload: dict[str, object]) -> None:
    print(json.dumps({"v": 1, **payload}, sort_keys=True), flush=True)


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
    struct.pack_into("<I", header, 44, frames)
    header[48:52] = b"data"
    struct.pack_into("<I", header, 52, len(data))
    path.write_bytes(header + data)


def write_stems(out: Path, *, extra_frames: int = 0) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for stem in STEMS:
        target = out / f"{stem}.wav"
        write_wav(target, frames=4410 + extra_frames)
        paths[stem] = target
    return paths


def mode_ok(out: Path) -> int:
    emit({"event": "ready", "engine": "stub", "engine_version": "1.0"})
    emit({"event": "block", "index": 1, "total": 1, "fraction": 0.0})
    emit({"event": "progress", "stage": "Separate", "fraction": 0.5, "block": 1, "blocks": 1})
    paths = write_stems(out)
    for stem, path in paths.items():
        emit({"event": "stem", "stem": stem, "path": path.name})
    emit({"event": "progress", "stage": "Separate", "fraction": 1.0, "block": 1, "blocks": 1})
    emit({"event": "done", "outputs": list(STEMS)})
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
        print(json.dumps({"v": 2, "event": "ready"}), flush=True)
        return 0
    if mode == "malformed":
        print("this is not json", flush=True)
        return 0
    if mode == "crash":
        emit({"event": "progress", "stage": "Separate", "fraction": 0.2})
        print("stub worker: synthetic crash", file=sys.stderr, flush=True)
        os._exit(7)
    if mode == "error":
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
        emit({"event": "error", "code": "separation.mystery", "detail": "unknown"})
        return 2
    if mode == "hang":
        emit({"event": "progress", "stage": "Separate", "fraction": 0.2})
        write_wav(out / "vocals.partial.wav")
        with contextlib.suppress(OSError, ValueError):
            sys.stdin.buffer.read(1)
        emit({"event": "error", "code": "cancelled", "detail": "stdin closed"})
        return 2
    if mode == "slow":
        emit({"event": "progress", "stage": "Separate", "fraction": 0.1})
        time.sleep(30)
        return 0
    if mode == "silent":
        return 0
    if mode == "missing-stem":
        paths = write_stems(out)
        for stem in ("vocals", "drums", "bass"):
            emit({"event": "stem", "stem": stem, "path": paths[stem].name})
        emit({"event": "done", "outputs": ["vocals", "drums", "bass"]})
        return 0
    if mode == "bad-path":
        emit({"event": "stem", "stem": "vocals", "path": "../escape.wav"})
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
