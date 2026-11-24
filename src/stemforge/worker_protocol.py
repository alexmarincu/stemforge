"""The separation worker's JSONL protocol (stdlib only).

Stdout is protocol only: one UTF-8 JSON object per line, each carrying ``"v": 2``.
Stderr is human log only. This module owns the event names, the exit codes, and
the parser used by :class:`~stemforge.engines.separation.WorkerSeparationEngine`.
It imports nothing outside the standard library because ``platform``,
``domain``, and ``workers`` depend on it.

Version 2 makes the method part of the protocol. ``hello`` declares the method
and the output roles it will emit; the parent rejects an ``output`` whose role
was not declared and a ``done.outputs`` set that differs from the declaration.
A method without Model weights omits the model fields. Event types::

    {"v":2,"event":"hello","engine":"demucs","engine_version":"4.1.0",
     "method":"demucs","outputs":["vocals","instrumental","drums","bass","other"],
     "model":"htdemucs","model_revision":"cbc8a9b","device":"cpu",
     "sample_rate":44100,"threads":8}
    {"v":2,"event":"stage","stage":"model_load","state":"start"}
    {"v":2,"event":"stage","stage":"model_load","state":"end","seconds":1.98}
    {"v":2,"event":"progress","stage":"separate","fraction":0.334,
     "segment":2,"segments":41,"audio_seconds_done":80.2,
     "audio_seconds_total":240.0,"eta_seconds":247.0}
    {"v":2,"event":"output","stem":"vocals","path":"vocals.wav","format":"wav",
     "sample_rate":44100,"channels":2}
    {"v":2,"event":"done","elapsed_seconds":374.2,
     "outputs":["vocals","instrumental","drums","bass","other"]}
    {"v":2,"event":"error","code":"separation.engine_error","message":"…",
     "retryable":false,"detail":"…"}

The Karaoke worker declares ``["vocals","instrumental"]`` and omits the model
fields. Unknown event names are ignored so a newer worker can add events without
breaking an older parent. A ``v`` other than :data:`PROTOCOL_VERSION` is a
protocol mismatch.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final, cast

PROTOCOL_VERSION: Final = 2

EVENT_HELLO: Final = "hello"
EVENT_STAGE: Final = "stage"
EVENT_PROGRESS: Final = "progress"
EVENT_OUTPUT: Final = "output"
EVENT_DONE: Final = "done"
EVENT_ERROR: Final = "error"

KNOWN_EVENTS: Final[frozenset[str]] = frozenset(
    {
        EVENT_HELLO,
        EVENT_STAGE,
        EVENT_PROGRESS,
        EVENT_OUTPUT,
        EVENT_DONE,
        EVENT_ERROR,
    }
)

STEM_NAMES: Final[tuple[str, ...]] = ("vocals", "drums", "bass", "other")
INSTRUMENTAL_NAME: Final = "instrumental"
WORKER_OUTPUT_NAMES: Final[tuple[str, ...]] = (*STEM_NAMES, INSTRUMENTAL_NAME)
WORKER_OUTPUTS: Final[frozenset[str]] = frozenset(WORKER_OUTPUT_NAMES)

# The worker cannot import ``stemforge.domain``; this mapping is its copy of
# the method registry's output roles and versions, kept identical by a contract
# test. The role order is presentation order.
METHOD_OUTPUT_ROLES: Final[MappingProxyType[str, tuple[str, ...]]] = MappingProxyType(
    {
        "demucs": ("vocals", "instrumental", "drums", "bass", "other"),
        "karaoke": ("vocals", "instrumental"),
    }
)

METHOD_VERSIONS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        "demucs": 1,
        "karaoke": 4,
    }
)

# 0 success, 2 handled failure, 3 model unavailable, 4 invalid input.
EXIT_OK: Final = 0
EXIT_HANDLED: Final = 2
EXIT_MODEL_UNAVAILABLE: Final = 3
EXIT_INVALID_INPUT: Final = 4

# Worker-side codes for the handled exits. The parent maps unknown codes to
# ``separation.engine_error``; ``cancelled`` becomes a cancelled Job.
CODE_CANCELLED: Final = "cancelled"
CODE_ENGINE_ERROR: Final = "separation.engine_error"
CODE_MODEL_MISSING: Final = "separation.model_missing"

STAGE_START: Final = "start"
STAGE_END: Final = "end"


class ProtocolError(ValueError):
    """A line that cannot be a valid protocol event."""

    def __init__(self, message: str, *, line: str = "") -> None:
        super().__init__(message)
        self.line = line


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    """One parsed event. Only the fields that belong to ``event`` are set."""

    event: str
    fraction: float | None = None
    stage: str | None = None
    state: str | None = None
    seconds: float | None = None
    detail: str | None = None
    segment: int | None = None
    segments: int | None = None
    audio_seconds_done: float | None = None
    audio_seconds_total: float | None = None
    eta_seconds: float | None = None
    stem: str | None = None
    path: str | None = None
    output_format: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    elapsed_seconds: float | None = None
    outputs: tuple[str, ...] = ()
    declared_outputs: tuple[str, ...] = ()
    engine: str | None = None
    engine_version: str | None = None
    method: str | None = None
    model: str | None = None
    model_revision: str | None = None
    device: str | None = None
    threads: int | None = None


def parse_event(line: str) -> WorkerEvent | None:
    """Parse one stdout line; ``None`` means an unknown, ignorable event.

    Raises :class:`ProtocolError` for malformed JSON, a different protocol
    version, or a known event with invalid fields. Empty lines are malformed:
    stdout is protocol only, so a blank line means a stray ``print``.
    """
    text = line.strip()
    if not text:
        raise ProtocolError("empty protocol line", line=line)
    try:
        raw: object = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ProtocolError(f"non-JSON protocol line: {type(exc).__name__}", line=line) from exc
    if not isinstance(raw, dict):
        raise ProtocolError("protocol line is not a JSON object", line=line)
    payload = cast("dict[str, object]", raw)

    version = payload.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("protocol line has no integer v", line=line)
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {version}", line=line)

    name = payload.get("event")
    if not isinstance(name, str) or not name:
        raise ProtocolError("protocol line has no event name", line=line)
    if name not in KNOWN_EVENTS:
        return None

    if name == EVENT_HELLO:
        return _parse_hello(payload, line)
    if name == EVENT_STAGE:
        return _parse_stage(payload, line)
    if name == EVENT_PROGRESS:
        return _parse_progress(payload, line)
    if name == EVENT_OUTPUT:
        return _parse_output(payload, line)
    if name == EVENT_DONE:
        return _parse_done(payload, line)
    return _parse_error(payload, line)


def format_event(payload: dict[str, object]) -> str:
    """Serialise one event for stdout, adding ``v`` and a trailing newline."""
    event = dict(payload)
    event["v"] = PROTOCOL_VERSION
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def is_safe_relative_output(path: str) -> bool:
    """Whether ``path`` is a bare relative path that cannot escape ``--out``."""
    if not path or "\\" in path or ":" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute() or len(pure.parts) != 1:
        return False
    return pure.name not in {"", ".", ".."} and pure.suffix == ".wav"


def block_count(*, total_frames: int, block_frames: int, fade_frames: int) -> int:
    """The block plan's count, shared by the worker and the manifest."""
    if total_frames <= 0:
        return 0
    if block_frames <= 0 or fade_frames < 0 or fade_frames >= block_frames:
        raise ValueError("invalid block or fade length")
    if total_frames <= block_frames:
        return 1
    return 1 + math.ceil((total_frames - block_frames) / (block_frames - fade_frames))


def _parse_hello(payload: dict[str, object], line: str) -> WorkerEvent:
    engine = _required_text(payload, "engine", line)
    engine_version = _required_text(payload, "engine_version", line)
    method = _required_text(payload, "method", line)
    declared_outputs = _required_names(payload, "outputs", line)
    sample_rate = _required_int(payload, "sample_rate", line)
    threads = _required_int(payload, "threads", line)
    return WorkerEvent(
        event=EVENT_HELLO,
        engine=engine,
        engine_version=engine_version,
        method=method,
        declared_outputs=declared_outputs,
        model=_optional_text(payload, "model", line),
        model_revision=_optional_text(payload, "model_revision", line),
        device=_optional_text(payload, "device", line),
        sample_rate=sample_rate,
        threads=threads,
    )


def _parse_stage(payload: dict[str, object], line: str) -> WorkerEvent:
    stage = _required_text(payload, "stage", line)
    state = _required_text(payload, "state", line)
    if state not in {STAGE_START, STAGE_END}:
        raise ProtocolError("stage.state must be start or end", line=line)
    seconds = _optional_number(payload, "seconds", line)
    return WorkerEvent(event=EVENT_STAGE, stage=stage, state=state, seconds=seconds)


def _parse_progress(payload: dict[str, object], line: str) -> WorkerEvent:
    stage = _required_text(payload, "stage", line)
    fraction = payload.get("fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ProtocolError("progress.fraction must be a number", line=line)
    value = float(fraction)
    if not 0.0 <= value <= 1.0:
        raise ProtocolError("progress.fraction must be within 0..1", line=line)
    segment = _optional_index(payload, "segment", line)
    segments = _optional_index(payload, "segments", line)
    if segment is not None and segments is not None and segment > segments:
        raise ProtocolError("progress.segment exceeds progress.segments", line=line)
    return WorkerEvent(
        event=EVENT_PROGRESS,
        fraction=value,
        stage=stage,
        segment=segment,
        segments=segments,
        audio_seconds_done=_optional_number(payload, "audio_seconds_done", line),
        audio_seconds_total=_optional_number(payload, "audio_seconds_total", line),
        eta_seconds=_optional_number(payload, "eta_seconds", line),
    )


def _parse_output(payload: dict[str, object], line: str) -> WorkerEvent:
    stem = payload.get("stem")
    if not isinstance(stem, str) or not stem:
        raise ProtocolError(f"invalid output stem: {stem!r}", line=line)
    path = payload.get("path")
    if not isinstance(path, str) or not is_safe_relative_output(path):
        raise ProtocolError(f"unsafe output path: {path!r}", line=line)
    output_format = payload.get("format")
    if output_format != "wav":
        raise ProtocolError("output.format must be wav", line=line)
    sample_rate = _required_int(payload, "sample_rate", line)
    channels = _required_int(payload, "channels", line)
    return WorkerEvent(
        event=EVENT_OUTPUT,
        stem=stem,
        path=path,
        output_format="wav",
        sample_rate=sample_rate,
        channels=channels,
    )


def _parse_done(payload: dict[str, object], line: str) -> WorkerEvent:
    elapsed = _optional_number(payload, "elapsed_seconds", line)
    if elapsed is None:
        raise ProtocolError("done.elapsed_seconds is required", line=line)
    outputs = _required_names(payload, "outputs", line)
    return WorkerEvent(event=EVENT_DONE, elapsed_seconds=elapsed, outputs=outputs)


def _parse_error(payload: dict[str, object], line: str) -> WorkerEvent:
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        raise ProtocolError("error event has no code", line=line)
    retryable = payload.get("retryable", False)
    if not isinstance(retryable, bool):
        raise ProtocolError("error.retryable must be a boolean", line=line)
    return WorkerEvent(
        event=EVENT_ERROR,
        code=code,
        detail=_optional_text(payload, "detail", line),
        message=_optional_text(payload, "message", line),
        retryable=retryable,
    )


def _required_names(payload: dict[str, object], key: str, line: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(item, str) and item for item in raw)
    ):
        raise ProtocolError(f"{key} must be a non-empty list of names", line=line)
    names = tuple(cast("list[str]", raw))
    if len(set(names)) != len(names):
        raise ProtocolError(f"{key} must not duplicate a name", line=line)
    return names


def _optional_text(payload: dict[str, object], key: str, line: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"{key} must be a string", line=line)
    return value


def _required_text(
    payload: dict[str, object], key: str, line: str, *, allow_empty: bool = False
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        event = payload.get("event")
        name = event if isinstance(event, str) and event else "protocol"
        raise ProtocolError(f"{name} event is missing {key}", line=line)
    return value


def _optional_number(payload: dict[str, object], key: str, line: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{key} must be a number", line=line)
    number = float(value)
    if number < 0:
        raise ProtocolError(f"{key} must not be negative", line=line)
    return number


def _required_int(payload: dict[str, object], key: str, line: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProtocolError(f"{key} must be a positive integer", line=line)
    return value


def _optional_index(payload: dict[str, object], key: str, line: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProtocolError(f"{key} must be a positive integer", line=line)
    return value
