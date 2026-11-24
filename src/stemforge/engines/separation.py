"""``SeparationEngine`` protocol and ``WorkerSeparationEngine``.

The engine runs the separation worker as a subprocess, parses its JSONL stdout,
and maps every exit path to a coded :class:`~stemforge.errors.StemForgeError`.
It never imports torch or Demucs: only ``workers/separation.py`` does, inside
``run()``.

Cancellation closes the child's stdin (the worker treats EOF as a cancel
request), waits briefly, then terminates the whole process group. A hard timeout
does the same and reports ``separation.engine_error`` with ``reason=timeout``.

The adapter is method-aware: the request names the method and its expected
output roles, the ``hello`` must declare that same method and role set, and only
the declared outputs are returned. Model flags and block-plan flags are passed
only for a method that requires Model weights.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.methods import require_method
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.platform import binaries, process
from stemforge.worker_protocol import (
    CODE_CANCELLED,
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
    ProtocolError,
    WorkerEvent,
    parse_event,
)

__all__ = [
    "DEFAULT_BLOCK_SECONDS",
    "DEFAULT_CROSSFADE_SECONDS",
    "DEFAULT_OVERLAP",
    "DEMUCS_VERSION",
    "ProgressSink",
    "SeparationEngine",
    "SeparationOutputs",
    "WorkerSeparationEngine",
]

ProgressSink = Callable[[float, str, str], None]

DEMUCS_VERSION: Final = "4.1.0"
DEFAULT_BLOCK_SECONDS: Final = 480.0
DEFAULT_CROSSFADE_SECONDS: Final = 1.0
DEFAULT_OVERLAP: Final = 0.25
POLL_INTERVAL_S: Final = 0.05
CANCEL_GRACE_S: Final = 2.0
STDERR_TAIL_LINES: Final = 200
STDERR_TAIL_CHARS: Final = 4_000
# Block mode uses shifts=0 so the random equivariant shift cannot
# change timbre across a block boundary (ADR 0008). One pass, not an average.
DEFAULT_SHIFTS: Final = 0
DEFAULT_DEVICE: Final = "cpu"
DEFAULT_MODEL: Final = "htdemucs"


class SeparationEngine(Protocol):
    """The Separation adapter seam."""

    def separate(
        self,
        canonical: Path,
        out_dir: Path,
        *,
        method: str,
        expected_outputs: Sequence[str],
        model_path: Path | None,
        expected_duration_s: float,
        progress: ProgressSink,
        cancel: CancelToken,
        log: logging.Logger,
        threads: int | None = None,
        model_revision: str = "",
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        on_spawn: Callable[[int], None] | None = None,
    ) -> SeparationOutputs: ...


@dataclass(frozen=True, slots=True)
class SeparationOutputs:
    """The declared output WAVs the worker committed inside ``out_dir``."""

    outputs: Mapping[str, Path]

    def path_for(self, stem: str) -> Path | None:
        return self.outputs.get(stem)

    @property
    def stems(self) -> Mapping[str, Path]:
        """The method's Stems; Instrumental is an output, not a Stem (GLOSSARY)."""
        return {role: path for role, path in self.outputs.items() if role != INSTRUMENTAL_NAME}


class WorkerSeparationEngine:
    """Run ``stemforge --worker separation`` and speak its JSONL protocol."""

    def __init__(
        self,
        *,
        argv: Sequence[str] | None = None,
        block_seconds: float = DEFAULT_BLOCK_SECONDS,
        crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS,
        overlap: float = DEFAULT_OVERLAP,
        shifts: int = DEFAULT_SHIFTS,
        segment_seconds: float | None = None,
        poll_interval_s: float = POLL_INTERVAL_S,
        cancel_grace_s: float = CANCEL_GRACE_S,
    ) -> None:
        if block_seconds <= 0:
            raise ValueError("block_seconds must be positive")
        if not 0.0 <= crossfade_seconds < block_seconds:
            raise ValueError("crossfade_seconds must be shorter than the block")
        if not 0.0 <= overlap < 1.0:
            raise ValueError("overlap must be within 0..1")
        if shifts < 0:
            raise ValueError("shifts must not be negative")
        self._argv = tuple(argv) if argv is not None else None
        self._block_seconds = float(block_seconds)
        self._crossfade_seconds = float(crossfade_seconds)
        self._overlap = float(overlap)
        self._shifts = int(shifts)
        self._segment_seconds = segment_seconds
        self._poll_interval_s = poll_interval_s
        self._cancel_grace_s = cancel_grace_s

    @property
    def block_seconds(self) -> float:
        return self._block_seconds

    @property
    def crossfade_seconds(self) -> float:
        return self._crossfade_seconds

    @property
    def overlap(self) -> float:
        return self._overlap

    @property
    def shifts(self) -> int:
        return self._shifts

    @property
    def segment_seconds(self) -> float | None:
        return self._segment_seconds

    def command(
        self,
        canonical: Path,
        out_dir: Path,
        *,
        method: str,
        model_path: Path | None = None,
        expected_duration_s: float,
        threads: int | None = None,
        model_revision: str = "",
    ) -> list[str]:
        """Build the worker argv (exposed for the Job log and tests)."""
        descriptor = require_method(method)
        base = (
            list(self._argv) if self._argv is not None else binaries.worker_command(["separation"])
        )
        base.extend(
            [
                "--method",
                descriptor.id,
                "--input",
                str(canonical),
                "--out",
                str(out_dir),
                "--threads",
                str(_resolved_threads(threads)),
                "--expected-duration",
                f"{expected_duration_s:.3f}",
            ]
        )
        if descriptor.requires_model:
            if model_path is None:
                raise ValueError(f"method {descriptor.id} requires a model path")
            base.extend(
                [
                    "--model",
                    DEFAULT_MODEL,
                    "--model-path",
                    str(model_path),
                    "--model-revision",
                    model_revision,
                    "--device",
                    DEFAULT_DEVICE,
                    "--block-seconds",
                    f"{self._block_seconds:g}",
                    "--crossfade-seconds",
                    f"{self._crossfade_seconds:g}",
                    "--overlap",
                    f"{self._overlap:g}",
                    "--shifts",
                    str(self._shifts),
                ]
            )
            if self._segment_seconds is not None:
                base.extend(["--segment", f"{self._segment_seconds:g}"])
        return base

    def separate(
        self,
        canonical: Path,
        out_dir: Path,
        *,
        method: str,
        expected_outputs: Sequence[str],
        model_path: Path | None,
        expected_duration_s: float,
        progress: ProgressSink,
        cancel: CancelToken,
        log: logging.Logger,
        threads: int | None = None,
        model_revision: str = "",
        env: Mapping[str, str] | None = None,
        timeout_s: float | None = None,
        on_spawn: Callable[[int], None] | None = None,
    ) -> SeparationOutputs:
        """Run one separation and return exactly the requested output paths."""
        cancel.check()
        descriptor = require_method(method)
        declared_roles = tuple(expected_outputs)
        if not declared_roles or len(set(declared_roles)) != len(declared_roles):
            raise ValueError("expected_outputs must be a non-empty unique sequence")
        if descriptor.requires_model and (model_path is None or not Path(model_path).is_file()):
            raise StemForgeError(
                errors.SEPARATION_MODEL_MISSING,
                "the separation weights are missing",
                detail=f"reason=absent path={model_path}",
                context={"path": str(model_path)},
            )
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = self.command(
            Path(canonical),
            out_dir,
            method=descriptor.id,
            model_path=Path(model_path) if descriptor.requires_model and model_path else None,
            expected_duration_s=expected_duration_s,
            threads=threads,
            model_revision=model_revision,
        )
        log.debug("separation: %s", " ".join(argv))
        child_env = dict(env) if env is not None else None
        if child_env is not None:
            child_env.setdefault("PYTHONUNBUFFERED", "1")
            child_env.setdefault("PYTHONUTF8", "1")

        proc = process.spawn(argv, stdin=subprocess.PIPE, env=child_env)
        if on_spawn is not None:
            on_spawn(proc.pid)
        events: queue.Queue[WorkerEvent | _ProtocolFailure | None] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        reader = threading.Thread(
            target=self._pump_stdout,
            args=(proc, events),
            name="stemforge-separation-protocol",
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=_pump_stderr,
            args=(proc, stderr_tail, log),
            name="stemforge-separation-stderr",
            daemon=True,
        )
        reader.start()
        stderr_reader.start()

        outputs: dict[str, Path] = {}
        last_fraction = 0.0
        hello: WorkerEvent | None = None
        done: WorkerEvent | None = None
        reported: WorkerEvent | None = None
        failure: ProtocolError | None = None
        deadline = None if timeout_s is None else time.monotonic() + timeout_s

        try:
            while True:
                if cancel.cancelled:
                    raise JobCancelled()
                if deadline is not None and time.monotonic() >= deadline:
                    raise StemForgeError(
                        errors.SEPARATION_ENGINE_ERROR,
                        "the separation worker timed out",
                        retryable=True,
                        detail=f"reason=timeout timeout={timeout_s:g}s",
                        context={"timeout_s": timeout_s},
                    )
                try:
                    item = events.get(timeout=self._poll_interval_s)
                except queue.Empty:
                    # If the reader thread died without a sentinel, do not hang
                    # on a child that has already exited.
                    if proc.poll() is not None and not reader.is_alive():
                        break
                    continue
                if item is None:
                    break
                if isinstance(item, _ProtocolFailure):
                    failure = item.error
                    break
                if item.event == EVENT_HELLO:
                    if hello is not None:
                        failure = ProtocolError("duplicate hello event")
                        break
                    if item.method != descriptor.id:
                        failure = ProtocolError(
                            f"hello method {item.method!r} does not match {descriptor.id!r}"
                        )
                        break
                    if set(item.declared_outputs) != set(declared_roles):
                        failure = ProtocolError(
                            "hello roles "
                            f"{list(item.declared_outputs)!r} do not match {list(declared_roles)!r}"
                        )
                        break
                    hello = item
                elif item.event == EVENT_STAGE:
                    log.debug(
                        "separation stage %s %s%s",
                        item.stage,
                        item.state,
                        "" if item.seconds is None else f" in {item.seconds:.2f}s",
                    )
                elif item.event == EVENT_PROGRESS:
                    if item.fraction is None:
                        continue
                    if item.fraction > last_fraction:
                        last_fraction = item.fraction
                        progress(
                            item.fraction,
                            _title(item.stage) or "Separate",
                            _progress_detail(item),
                        )
                elif item.event == EVENT_OUTPUT:
                    if hello is None:
                        failure = ProtocolError("output event before hello")
                        break
                    resolved = _resolve_output(out_dir, item.path)
                    if item.stem is None or resolved is None:
                        failure = ProtocolError(f"unsafe output path: {item.path!r}")
                        break
                    if item.stem not in hello.declared_outputs:
                        failure = ProtocolError(f"undeclared output role: {item.stem}")
                        break
                    if item.stem in outputs:
                        failure = ProtocolError(f"duplicate output event: {item.stem}")
                        break
                    if not resolved.is_file():
                        failure = ProtocolError(f"worker declared a missing output: {resolved}")
                        break
                    outputs[item.stem] = resolved
                elif item.event == EVENT_DONE:
                    if hello is None:
                        failure = ProtocolError("done event before hello")
                        break
                    if set(item.outputs) != set(hello.declared_outputs):
                        failure = ProtocolError(
                            "done.outputs does not match the declared roles: "
                            f"{list(item.outputs)!r}"
                        )
                        break
                    done = item
                elif item.event == EVENT_ERROR:
                    reported = item
            cancel.check()
        finally:
            if cancel.cancelled:
                _stop_worker(proc, self._cancel_grace_s)
            if proc.poll() is None:
                if failure is not None:
                    _stop_worker(proc, self._cancel_grace_s)
                else:
                    process.terminate_tree(proc)
            reader.join(timeout=process.TERMINATE_GRACE_S)
            stderr_reader.join(timeout=process.TERMINATE_GRACE_S)

        returncode = proc.returncode
        stderr_text = _tail("\n".join(stderr_tail))

        if cancel.cancelled:
            raise JobCancelled()
        if failure is not None:
            raise _protocol_error(failure, stderr_text, returncode)
        if returncode == EXIT_OK:
            if reported is not None:
                raise _protocol_error(
                    ProtocolError("worker exited 0 after an error event"), stderr_text, returncode
                )
            if hello is None:
                raise _protocol_error(
                    ProtocolError("worker exited 0 without a hello event"),
                    stderr_text,
                    returncode,
                )
            if done is None:
                raise _protocol_error(
                    ProtocolError("worker exited 0 without a done event"),
                    stderr_text,
                    returncode,
                )
            missing = [stem for stem in declared_roles if stem not in outputs]
            if missing:
                raise StemForgeError(
                    errors.SEPARATION_OUTPUT_INVALID,
                    "the separation worker did not produce every output",
                    detail=f"missing={','.join(missing)}",
                    context={"missing": missing, "stderr": stderr_text},
                )
            log.debug(
                "separation: %s method=%s hello=%s outputs=%s",
                returncode,
                descriptor.id,
                hello.engine_version,
                sorted(outputs),
            )
            return SeparationOutputs(outputs={stem: outputs[stem] for stem in declared_roles})
        if returncode == EXIT_HANDLED:
            if reported is None:
                raise _protocol_error(
                    ProtocolError("worker exited 2 without an error event"),
                    stderr_text,
                    returncode,
                )
            if reported.code == CODE_CANCELLED:
                raise JobCancelled()
            raise _worker_error(reported, stderr_text)
        if returncode == EXIT_MODEL_UNAVAILABLE:
            raise StemForgeError(
                errors.SEPARATION_MODEL_MISSING,
                "the separation worker could not load the model",
                retryable=False,
                detail=reported.detail if reported is not None and reported.detail else stderr_text,
                context={"returncode": returncode},
            )
        if returncode == EXIT_INVALID_INPUT:
            raise StemForgeError(
                errors.SEPARATION_ENGINE_ERROR,
                "the separation worker rejected its input",
                retryable=False,
                detail=stderr_text or "reason=input_invalid",
                context={"returncode": returncode},
            )
        raise StemForgeError(
            errors.SEPARATION_WORKER_CRASHED,
            "the separation worker stopped unexpectedly",
            retryable=True,
            detail=stderr_text or f"exit {returncode}",
            context={"returncode": returncode},
        )

    # -- internals ----------------------------------------------------------

    def _pump_stdout(
        self,
        proc: subprocess.Popen[str],
        events: queue.Queue[WorkerEvent | _ProtocolFailure | None],
    ) -> None:
        assert proc.stdout is not None
        try:
            for line in process.iter_lines(proc.stdout):
                try:
                    event = parse_event(line)
                except ProtocolError as exc:
                    events.put(_ProtocolFailure(exc))
                    return
                if event is not None:
                    events.put(event)
        finally:
            events.put(None)


@dataclass(frozen=True, slots=True)
class _ProtocolFailure:
    error: ProtocolError


def _pump_stderr(
    proc: subprocess.Popen[str],
    tail: deque[str],
    log: logging.Logger,
) -> None:
    assert proc.stderr is not None
    for line in process.iter_lines(proc.stderr):
        tail.append(line)
        log.debug("separation worker: %s", line)


def _stop_worker(proc: subprocess.Popen[str], grace_s: float) -> None:
    """Ask via stdin EOF, then terminate the tree if it does not stop."""
    if proc.stdin is not None:
        with contextlib.suppress(OSError):
            proc.stdin.close()
    if proc.poll() is not None:
        return
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    process.terminate_tree(proc)


def _resolve_output(out_dir: Path, relative: str | None) -> Path | None:
    if relative is None:
        return None
    root = out_dir.resolve()
    candidate = (out_dir / relative).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _progress_detail(event: WorkerEvent) -> str:
    if event.segment is not None and event.segments is not None:
        return f"segment {event.segment} of {event.segments}"
    if event.audio_seconds_done is not None and event.audio_seconds_total is not None:
        return f"{event.audio_seconds_done:.0f} of {event.audio_seconds_total:.0f} s"
    return ""


def _title(stage: str | None) -> str:
    if not stage:
        return ""
    return stage.replace("_", " ").title()


def _resolved_threads(threads: int | None) -> int:
    if threads is None:
        # Set torch/OMP threads to physical cores. The usual 2-way
        # SMT ratio is the portable estimate without a native dependency.
        return max(1, (os.cpu_count() or 2) // 2)
    if threads < 1:
        raise ValueError("threads must be positive")
    return threads


def _tail(text: str, limit: int = STDERR_TAIL_CHARS) -> str:
    stripped = text.strip()
    return stripped[-limit:] if len(stripped) > limit else stripped


def _protocol_error(error: ProtocolError, stderr_text: str, returncode: int) -> StemForgeError:
    detail = str(error) if not error.line else f"{error}: {error.line}"
    if stderr_text:
        detail = f"{detail}\n{stderr_text}"
    return StemForgeError(
        errors.SEPARATION_PROTOCOL_MISMATCH,
        "the separation worker broke its protocol",
        retryable=False,
        detail=_tail(detail),
        context={"returncode": returncode, "reason": str(error)},
    )


def _worker_error(event: WorkerEvent, stderr_text: str) -> StemForgeError:
    code = event.code or errors.SEPARATION_ENGINE_ERROR
    if code not in _KNOWN_WORKER_CODES:
        code = errors.SEPARATION_ENGINE_ERROR
    detail = event.detail or event.message or stderr_text or None
    return StemForgeError(
        code,
        event.message or "the separation worker reported an error",
        retryable=(
            True
            if code in {errors.SEPARATION_ENGINE_ERROR, errors.SEPARATION_OUTPUT_INVALID}
            else event.retryable
        ),
        detail=_tail(detail) if detail else None,
        context={"worker_code": event.code},
    )


_KNOWN_WORKER_CODES: Final[frozenset[str]] = frozenset(
    {
        errors.SEPARATION_MODEL_MISSING,
        errors.SEPARATION_ENGINE_ERROR,
        errors.SEPARATION_OUTPUT_INVALID,
        errors.SEPARATION_PROTOCOL_MISMATCH,
        errors.SEPARATION_WORKER_CRASHED,
    }
)
