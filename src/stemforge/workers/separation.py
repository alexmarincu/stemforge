"""The Separation worker: reads one canonical WAV, writes five outputs as JSONL.

Invoked by the frozen binary as ``stemforge --worker separation`` or in
development as ``python -m stemforge.workers.separation``. Stdout
is protocol only; stderr is the human log. The worker imports torch and Demucs
**inside** :func:`run`, never at module scope: the app must start
without the heavyweight stack.

The 30-minute source is processed in 8-minute blocks with a 1-second linear
crossfade (ADR 0008), so peak memory stays bounded independent of
track length. Blocks use ``shifts=0``: the random equivariant shift is disabled
in block mode so it cannot change timbre at a block boundary. Instrumental is the
float32 sum of drums + bass + other, written in the same pass while the stems are
in memory. Each output is written to ``<role>.partial.wav`` inside
``--out`` and renamed into place only after every block succeeded; a cancel, an
error, or a closed stdin removes the partials and leaves no output behind.
"""

from __future__ import annotations

import argparse
import contextlib
import math
import os
import signal
import struct
import sys
import threading
import time
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Final, NoReturn, TextIO

from stemforge.worker_protocol import (
    CODE_CANCELLED,
    CODE_ENGINE_ERROR,
    CODE_MODEL_MISSING,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_HELLO,
    EVENT_OUTPUT,
    EVENT_PROGRESS,
    EVENT_STAGE,
    EXIT_HANDLED,
    EXIT_INVALID_INPUT,
    EXIT_MODEL_UNAVAILABLE,
    EXIT_OK,
    INSTRUMENTAL_NAME,
    STAGE_END,
    STAGE_START,
    STEM_NAMES,
    WORKER_OUTPUT_NAMES,
    block_count,
    format_event,
)

DEFAULT_BLOCK_SECONDS: Final = 480.0
DEFAULT_CROSSFADE_SECONDS: Final = 1.0
DEFAULT_OVERLAP: Final = 0.25
DEFAULT_SHIFTS: Final = 0
SAMPLE_RATE: Final = 44_100
CHANNELS: Final = 2
NON_VOCAL_STEMS: Final = ("drums", "bass", "other")
MAX_DURATION_S: Final = 1800.0
DURATION_TOLERANCE_S: Final = 0.050
STDIN_WATCH_ENV: Final = "STEMFORGE_WORKER_STDIN_WATCH"
ERROR_DETAIL_CHARS: Final = 4_000


class _InvalidInput(Exception):
    """The worker cannot use the file it was given (exit 4)."""


class _ModelUnavailable(Exception):
    """The weights are missing or unreadable (exit 3)."""


class _StopRequested(Exception):
    """Cancellation through a signal or a closed stdin."""


@dataclass
class _Stop:
    """The worker-side cancellation flag shared with its watcher threads."""

    event: threading.Event

    def request(self) -> None:
        self.event.set()

    @property
    def requested(self) -> bool:
        return self.event.is_set()


@dataclass(frozen=True, slots=True)
class _WavInfo:
    sample_rate: int
    channels: int
    frames: int
    data_offset: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


@dataclass(frozen=True, slots=True)
class _Block:
    index: int
    start: int
    length: int


class _Parser(argparse.ArgumentParser):
    """An ArgumentParser that reports a coded error instead of exiting 2."""

    def error(self, message: str) -> NoReturn:
        raise _InvalidInput(message)


def run(argv: Sequence[str] | None = None) -> int:
    """Parse the worker arguments, separate, and return the process exit code."""
    protocol = sys.stdout
    if protocol is None:  # pragma: no cover - the Windows noconsole frozen build
        protocol = _pipe_stream(1)
        if protocol is None:
            return EXIT_INVALID_INPUT
    if sys.stderr is None:  # pragma: no cover - the Windows noconsole frozen build
        sys.stderr = _pipe_stream(2) or protocol

    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
    except _InvalidInput as exc:
        _emit(protocol, EVENT_ERROR, code=CODE_ENGINE_ERROR, detail=str(exc))
        return EXIT_INVALID_INPUT
    except SystemExit as exc:  # ``--help`` only; argparse already printed it
        return int(exc.code or 0)

    # Heavy imports stay inside run() and after argument validation, so a
    # malformed invocation fails fast and the app never imports them at all.
    import demucs
    import demucs.api as demucs_api
    import numpy as np
    import torch
    from demucs.hf import load_safetensors_model

    class LocalSeparator(demucs_api.Separator):
        """A Separator bound to the locally verified safetensors.

        Demucs 4.1.0's ``LocalRepo`` scans only legacy ``*.th`` files, and its
        Hub path would download. StemForge's model store holds the pinned
        ``955717e8.safetensors``, so ``_load_model`` is the one seam replaced;
        separation, callbacks, and parameter handling stay Demucs' own.
        """

        def _load_model(self) -> None:
            model = load_safetensors_model(args.model_path)
            self._model = model
            self._audio_channels = model.audio_channels
            self._samplerate = model.samplerate

    out_dir = Path(args.out)
    stop = _Stop(threading.Event())
    original_stdout = sys.stdout
    # Demucs, torch, and their dependencies may write human text to stdout.
    # Keep the protocol stream private and send everything else to stderr.
    sys.stdout = sys.stderr
    installed_handlers = _install_signal_handlers(stop)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        _remove_partials(out_dir)
        _emit(
            protocol,
            EVENT_HELLO,
            engine="demucs",
            engine_version=str(getattr(demucs, "__version__", "")),
            model=args.model,
            model_revision=args.model_revision,
            device=args.device,
            sample_rate=SAMPLE_RATE,
            threads=args.threads,
        )
        started = time.monotonic()

        try:
            info = _read_wav_info(Path(args.input))
            _validate_input(info, expected_duration_s=args.expected_duration)
            threads = max(1, int(args.threads))
            _configure_torch(torch, threads)
            _emit(protocol, EVENT_STAGE, stage="model_load", state=STAGE_START)
            load_started = time.monotonic()
            try:
                separator = LocalSeparator(
                    model="htdemucs",
                    repo=None,
                    device=args.device,
                    shifts=args.shifts,
                    overlap=args.overlap,
                    split=True,
                    segment=args.segment,
                    jobs=0,
                    progress=False,
                    callback=None,
                )
            except Exception as exc:
                raise _ModelUnavailable(f"{type(exc).__name__}: {exc}") from exc
            if separator.samplerate != SAMPLE_RATE or separator.audio_channels != CHANNELS:
                raise _ModelUnavailable(
                    f"model expects {separator.samplerate} Hz / {separator.audio_channels} channels"
                )
            _emit(
                protocol,
                EVENT_STAGE,
                stage="model_load",
                state=STAGE_END,
                seconds=round(time.monotonic() - load_started, 3),
            )
            _start_stdin_watch(stop)
            separate_started = time.monotonic()
            _emit(protocol, EVENT_STAGE, stage="separate", state=STAGE_START)
            _separate(
                args=args,
                info=info,
                separator=separator,
                protocol=protocol,
                stop=stop,
                numpy=np,
                torch=torch,
                started=started,
            )
            _emit(
                protocol,
                EVENT_STAGE,
                stage="separate",
                state=STAGE_END,
                seconds=round(time.monotonic() - separate_started, 3),
            )
        except _StopRequested:
            _remove_partials(out_dir)
            _emit(protocol, EVENT_ERROR, code=CODE_CANCELLED, detail="cancelled")
            return EXIT_HANDLED
        except KeyboardInterrupt:
            _remove_partials(out_dir)
            _emit(protocol, EVENT_ERROR, code=CODE_CANCELLED, detail="cancelled")
            return EXIT_HANDLED
        except _InvalidInput as exc:
            _remove_partials(out_dir)
            _emit(protocol, EVENT_ERROR, code=CODE_ENGINE_ERROR, detail=str(exc))
            return EXIT_INVALID_INPUT
        except _ModelUnavailable as exc:
            _remove_partials(out_dir)
            _emit(protocol, EVENT_ERROR, code=CODE_MODEL_MISSING, detail=str(exc))
            return EXIT_MODEL_UNAVAILABLE
        except Exception as exc:
            _remove_partials(out_dir)
            traceback.print_exc(file=sys.stderr)
            _emit(
                protocol,
                EVENT_ERROR,
                code=CODE_ENGINE_ERROR,
                detail=_tail(f"{type(exc).__name__}: {exc}"),
                retryable=True,
            )
            return EXIT_HANDLED

        elapsed = time.monotonic() - started
        _emit(
            protocol,
            EVENT_DONE,
            elapsed_seconds=round(elapsed, 3),
            outputs=list(WORKER_OUTPUT_NAMES),
        )
        return EXIT_OK
    finally:
        sys.stdout = original_stdout
        _restore_signal_handlers(installed_handlers)


def _separate(
    *,
    args: argparse.Namespace,
    info: _WavInfo,
    separator: Any,
    protocol: TextIO,
    stop: _Stop,
    numpy: Any,
    torch: Any,
    started: float,
) -> None:
    """Run the block plan and write all five outputs through partial files."""
    block_frames = max(1, round(float(args.block_seconds) * info.sample_rate))
    fade_frames = max(0, round(float(args.crossfade_seconds) * info.sample_rate))
    if fade_frames >= block_frames:
        raise _InvalidInput("crossfade must be shorter than the block")
    blocks = _plan_blocks(info.frames, block_frames, fade_frames)
    segment_frames = _segment_frames(separator)
    total_segments = max(1, math.ceil(info.frames / max(1, segment_frames)))
    total_audio_seconds = info.frames / info.sample_rate
    writers = {
        role: _FloatWavWriter(Path(args.out) / f"{role}.partial.wav", sample_rate=info.sample_rate)
        for role in WORKER_OUTPUT_NAMES
    }
    tail: dict[str, Any] | None = None
    try:
        with open(args.input, "rb") as handle:
            for block in blocks:
                if stop.requested:
                    raise _StopRequested()
                _emit(
                    protocol,
                    EVENT_PROGRESS,
                    stage="separate",
                    fraction=round(block.index / len(blocks), 6),
                    segment=min(total_segments, block.index + 1),
                    segments=total_segments,
                    audio_seconds_done=round(block.start / info.sample_rate, 3),
                    audio_seconds_total=round(total_audio_seconds, 3),
                    eta_seconds=_eta_seconds(started, block.index / len(blocks)),
                )
                audio = _read_block(numpy, handle, info, block)
                if stop.requested:
                    raise _StopRequested()
                wav = torch.from_numpy(audio)
                try:
                    separator.update_parameter(
                        callback=_progress_callback(
                            protocol=protocol,
                            stop=stop,
                            block=block,
                            blocks=len(blocks),
                            audio_length=block.length,
                            segment_frames=segment_frames,
                            total_segments=total_segments,
                            total_audio_seconds=total_audio_seconds,
                            started=started,
                        )
                    )
                    _origin, separated = separator.separate_tensor(wav, info.sample_rate)
                    separator.update_parameter(callback=None)
                except KeyboardInterrupt:
                    raise _StopRequested() from None
                except RuntimeError as exc:
                    if stop.requested:
                        raise _StopRequested() from None
                    raise _InvalidInput(f"{type(exc).__name__}: {exc}") from exc
                current = _block_stems(numpy, separated, block.length)
                del separated
                current[INSTRUMENTAL_NAME] = _instrumental(numpy, current)
                is_last = block.index == len(blocks) - 1
                _write_block(
                    numpy,
                    writers,
                    current,
                    tail,
                    fade_frames=fade_frames,
                    is_first=block.index == 0,
                    is_last=is_last,
                )
                if not is_last and fade_frames > 0:
                    tail = {role: current[role][-fade_frames:] for role in WORKER_OUTPUT_NAMES}
                else:
                    tail = None
                del current
                del wav
                del audio
                if stop.requested:
                    raise _StopRequested()
        for writer in writers.values():
            writer.close()
        for role in WORKER_OUTPUT_NAMES:
            partial = Path(args.out) / f"{role}.partial.wav"
            os.replace(partial, Path(args.out) / f"{role}.wav")
            _emit(
                protocol,
                EVENT_OUTPUT,
                stem=role,
                path=f"{role}.wav",
                format="wav",
                sample_rate=info.sample_rate,
                channels=info.channels,
            )
    except BaseException:
        for writer in writers.values():
            writer.abort()
        _remove_partials(Path(args.out))
        raise


def _instrumental(numpy: Any, current: dict[str, Any]) -> Any:
    """The float32 sum of drums, bass, and other, computed while in memory."""
    total = current[NON_VOCAL_STEMS[0]].astype("float32", copy=True)
    for role in NON_VOCAL_STEMS[1:]:
        total += current[role]
    return numpy.ascontiguousarray(total, dtype="float32")


def _eta_seconds(started: float, fraction: float) -> float:
    if fraction <= 0:
        return 0.0
    elapsed = time.monotonic() - started
    return round(max(0.0, elapsed * (1.0 - fraction) / fraction), 3)


def _block_stems(numpy: Any, separated: Any, length: int) -> dict[str, Any]:
    stems: dict[str, Any] = {}
    for stem in STEM_NAMES:
        tensor = separated[stem]
        array = tensor.detach().cpu().numpy().astype("float32", copy=False)[:, :length].T
        stems[stem] = numpy.ascontiguousarray(array)
    return stems


def _write_block(
    numpy: Any,
    writers: dict[str, _FloatWavWriter],
    current: dict[str, Any],
    tail: dict[str, Any] | None,
    *,
    fade_frames: int,
    is_first: bool,
    is_last: bool,
) -> None:
    fade = max(0, min(fade_frames, current[WORKER_OUTPUT_NAMES[0]].shape[0]))
    if is_first:
        for stem in WORKER_OUTPUT_NAMES:
            array = current[stem]
            if is_last or fade == 0:
                writers[stem].write(array)
            else:
                writers[stem].write(array[:-fade])
        return
    if tail is None:
        raise RuntimeError("missing crossfade tail")
    if fade == 0:
        for stem in WORKER_OUTPUT_NAMES:
            writers[stem].write(current[stem])
        return
    weights = numpy.arange(1, fade + 1, dtype="float32") / fade
    fade_out = (1.0 - weights)[:, None]
    fade_in = weights[:, None]
    for stem in WORKER_OUTPUT_NAMES:
        previous = tail[stem][-fade:]
        array = current[stem]
        blended = previous * fade_out + array[:fade] * fade_in
        writers[stem].write(blended.astype("float32", copy=False))
        if is_last:
            writers[stem].write(array[fade:])
        else:
            writers[stem].write(array[fade:-fade])


def _progress_callback(
    *,
    protocol: TextIO,
    stop: _Stop,
    block: _Block,
    blocks: int,
    audio_length: int,
    segment_frames: int,
    total_segments: int,
    total_audio_seconds: float,
    started: float,
) -> Any:
    def callback(payload: dict[str, object]) -> None:
        if stop.requested:
            raise KeyboardInterrupt
        if payload.get("state") != "end":
            return
        offset = payload.get("segment_offset")
        if not isinstance(offset, int):
            offset = 0
        length = payload.get("audio_length")
        total = length if isinstance(length, int) and length > 0 else audio_length
        local = min(1.0, (offset + max(1, segment_frames)) / max(1, total))
        overall = min(1.0, (block.index + local) / blocks)
        done = min(
            total_audio_seconds,
            (block.start + offset + min(segment_frames, total)) / SAMPLE_RATE,
        )
        segment = min(
            total_segments,
            math.floor((block.start + offset) / max(1, segment_frames)) + 1,
        )
        _emit(
            protocol,
            EVENT_PROGRESS,
            stage="separate",
            fraction=round(overall, 6),
            segment=segment,
            segments=total_segments,
            audio_seconds_done=round(done, 3),
            audio_seconds_total=round(total_audio_seconds, 3),
            eta_seconds=_eta_seconds(started, overall),
        )

    return callback


def _segment_frames(separator: object) -> int:
    model = getattr(separator, "model", None)
    segment = getattr(model, "segment", None)
    samplerate = int(getattr(separator, "samplerate", SAMPLE_RATE))
    try:
        seconds = float(segment) if segment is not None else 10.0
    except (TypeError, ValueError):
        seconds = 10.0
    return max(1, round(seconds * samplerate))


def _plan_blocks(total_frames: int, block_frames: int, fade_frames: int) -> list[_Block]:
    if total_frames <= 0:
        raise _InvalidInput("the input has no audio frames")
    count = block_count(
        total_frames=total_frames, block_frames=block_frames, fade_frames=fade_frames
    )
    hop = block_frames - fade_frames
    return [
        _Block(
            index=index,
            start=index * hop,
            length=min(block_frames, total_frames - index * hop),
        )
        for index in range(count)
    ]


def _read_block(numpy: Any, handle: IO[bytes], info: _WavInfo, block: _Block) -> Any:
    handle.seek(info.data_offset + block.start * info.channels * 4)
    expected = block.length * info.channels * 4
    raw = handle.read(expected)
    if len(raw) != expected:
        raise _InvalidInput("the input ended before its declared duration")
    array = numpy.frombuffer(raw, dtype="<f4").reshape(block.length, info.channels)
    return numpy.ascontiguousarray(array.T)


class _FloatWavWriter:
    """Write an IEEE-float 32-bit WAV without Demucs' ``clamp(-1, 1)``."""

    HEADER_BYTES: Final = 56
    DATA_SIZE_OFFSET: Final = 52

    def __init__(self, path: Path, *, sample_rate: int) -> None:
        self._path = path
        self._sample_rate = sample_rate
        self._channels = CHANNELS
        self._frames = 0
        self._closed = False
        self._handle = path.open("wb")
        self._write_header()

    def write(self, frames: Any) -> None:
        data = frames.astype("<f4", copy=False).tobytes()
        self._handle.write(data)
        self._frames += len(data) // (self._channels * 4)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        data_bytes = self._frames * self._channels * 4
        try:
            self._handle.seek(4)
            self._handle.write(struct.pack("<I", 48 + data_bytes))
            self._handle.seek(44)
            self._handle.write(struct.pack("<I", self._frames))
            self._handle.seek(self.DATA_SIZE_OFFSET)
            self._handle.write(struct.pack("<I", data_bytes))
            self._handle.flush()
            os.fsync(self._handle.fileno())
        finally:
            self._handle.close()

    def abort(self) -> None:
        self._closed = True
        with contextlib.suppress(OSError):
            self._handle.close()
        with contextlib.suppress(FileNotFoundError):
            self._path.unlink()

    def _write_header(self) -> None:
        byte_rate = self._sample_rate * self._channels * 4
        header = bytearray(self.HEADER_BYTES)
        header[0:4] = b"RIFF"
        struct.pack_into("<I", header, 4, 48)
        header[8:12] = b"WAVE"
        header[12:16] = b"fmt "
        struct.pack_into("<I", header, 16, 16)
        struct.pack_into(
            "<HHIIHH", header, 20, 3, self._channels, self._sample_rate, byte_rate, 8, 32
        )
        header[36:40] = b"fact"
        struct.pack_into("<I", header, 40, 4)
        header[48:52] = b"data"
        self._handle.write(header)


def _read_wav_info(path: Path) -> _WavInfo:
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise _InvalidInput(f"{type(exc).__name__}: {exc}") from exc
    with handle:
        header = handle.read(12)
        if len(header) != 12 or header[0:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
            raise _InvalidInput("the input is not a RIFF/WAVE file")
        if header[0:4] == b"RF64":
            raise _InvalidInput("RF64 input is not supported below 30 minutes")
        fmt: tuple[int, int, int, int] | None = None
        data_offset = 0
        data_bytes = 0
        while True:
            prefix = handle.read(8)
            if len(prefix) != 8:
                break
            chunk_id = prefix[0:4]
            size = struct.unpack("<I", prefix[4:8])[0]
            if chunk_id == b"fmt ":
                body = handle.read(size)
                if len(body) != size or size < 16:
                    raise _InvalidInput("the input has a truncated fmt chunk")
                tag, channels, rate, _byte_rate, _align, bits = struct.unpack_from(
                    "<HHIIHH", body, 0
                )
                if tag == 0xFFFE and size >= 40:
                    tag = struct.unpack_from("<H", body, 24)[0]
                fmt = (tag, channels, rate, bits)
                if size & 1:
                    handle.seek(1, os.SEEK_CUR)
            elif chunk_id == b"data":
                if fmt is None:
                    raise _InvalidInput("the data chunk precedes the fmt chunk")
                data_offset = handle.tell()
                data_bytes = size
                if size == 0xFFFFFFFF:
                    raise _InvalidInput("RF64 input is not supported below 30 minutes")
                break
            else:
                handle.seek(size + (size & 1), os.SEEK_CUR)
        if fmt is None or data_offset == 0:
            raise _InvalidInput("the input has no audio data")
        tag, channels, rate, bits = fmt
        if tag != 3:
            raise _InvalidInput(f"the input is not float32 (format tag {tag})")
        if bits != 32:
            raise _InvalidInput(f"the input is {bits}-bit, not float32")
        if rate != SAMPLE_RATE or channels != CHANNELS:
            raise _InvalidInput(f"the input is {rate} Hz / {channels} channels")
        block_align = channels * 4
        if block_align <= 0 or data_bytes % block_align:
            raise _InvalidInput("the data chunk is not a whole number of frames")
        return _WavInfo(
            sample_rate=rate,
            channels=channels,
            frames=data_bytes // block_align,
            data_offset=data_offset,
            data_bytes=data_bytes,
        )


def _validate_input(info: _WavInfo, *, expected_duration_s: float) -> None:
    if info.frames <= 0:
        raise _InvalidInput("the input has no audio frames")
    if info.duration_s > MAX_DURATION_S + DURATION_TOLERANCE_S:
        raise _InvalidInput(f"the input is {info.duration_s:.1f} s, above the 30-minute limit")
    if expected_duration_s > 0 and (
        abs(info.duration_s - expected_duration_s) > DURATION_TOLERANCE_S
    ):
        raise _InvalidInput(
            f"the input is {info.duration_s:.3f} s, expected {expected_duration_s:.3f} s"
        )


def _configure_torch(torch: Any, threads: int) -> None:
    torch.set_num_threads(threads)
    with contextlib.suppress(RuntimeError):
        torch.set_num_interop_threads(1)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="stemforge --worker separation", add_help=True)
    parser.add_argument("--model", default="htdemucs")
    parser.add_argument("--model-revision", default="")
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--expected-duration", type=float, default=0.0)
    parser.add_argument("--block-seconds", type=float, default=DEFAULT_BLOCK_SECONDS)
    parser.add_argument("--crossfade-seconds", type=float, default=DEFAULT_CROSSFADE_SECONDS)
    parser.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP)
    parser.add_argument("--shifts", type=int, default=DEFAULT_SHIFTS)
    parser.add_argument("--segment", type=float, default=None)
    return parser


def _emit(stream: TextIO, event: str, **fields: object) -> None:
    stream.write(format_event({"event": event, **fields}))
    stream.flush()


def _install_signal_handlers(stop: _Stop) -> list[tuple[int, object]]:
    installed: list[tuple[int, object]] = []
    if threading.current_thread() is not threading.main_thread():
        return installed

    def handler(_signum: int, _frame: object) -> None:
        stop.request()

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous = signal.signal(signum, handler)
        except (OSError, ValueError):
            continue
        installed.append((signum, previous))
    return installed


def _restore_signal_handlers(installed: Sequence[tuple[int, object]]) -> None:
    for signum, previous in installed:
        with contextlib.suppress(OSError, ValueError, TypeError):
            signal.signal(signum, previous)  # type: ignore[arg-type]


def _start_stdin_watch(stop: _Stop) -> None:
    """Treat a closed parent pipe as a cancel request (engine sets the env var)."""
    if os.environ.get(STDIN_WATCH_ENV, "") != "1":
        return
    stream = sys.stdin
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return

    def watch() -> None:
        # A raw os.read avoids holding the buffered-reader lock at interpreter
        # shutdown; a daemon thread blocked in BufferedReader.read() aborts the
        # frozen worker with _enter_buffered_busy after it already succeeded.
        try:
            if os.read(fd, 1) == b"":
                stop.request()
        except (OSError, ValueError):
            return

    threading.Thread(target=watch, name="stemforge-worker-stdin", daemon=True).start()


def _pipe_stream(fd: int) -> TextIO | None:
    """Recover stdout/stderr in a Windows windowed frozen build."""
    try:
        return os.fdopen(fd, "w", encoding="utf-8", errors="replace", closefd=False)
    except OSError:
        return None


def _remove_partials(out_dir: Path) -> None:
    for role in WORKER_OUTPUT_NAMES:
        with contextlib.suppress(FileNotFoundError, OSError):
            (out_dir / f"{role}.partial.wav").unlink()


def _tail(text: str, limit: int = ERROR_DETAIL_CHARS) -> str:
    stripped = text.strip()
    return stripped[-limit:] if len(stripped) > limit else stripped


def main(argv: Sequence[str] | None = None) -> int:
    """Module entry point; ``python -m stemforge.workers.separation <args>``."""
    return run(argv)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
