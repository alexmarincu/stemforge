"""The separation worker's JSONL protocol (stdlib only; ticket 06 §4, ticket 13).

Stdout is protocol only: one UTF-8 JSON object per line, each carrying ``"v": 1``.
Stderr is human log only. This module owns the event names, the exit codes, and
the parser used by :class:`~stemforge.engines.separation.WorkerSeparationEngine`.
It imports nothing outside the standard library because ``platform`` and
``domain`` modules depend on it (ticket 06 §1).

Event types (ticket 13)::

    {"v":1,"event":"ready","engine":"demucs","engine_version":"4.1.0",...}
    {"v":1,"event":"block","index":1,"total":4,"fraction":0.0}
    {"v":1,"event":"progress","stage":"Separate","fraction":0.25,
     "block":1,"blocks":4,"detail":"segment 7 of 60"}
    {"v":1,"event":"stem","stem":"vocals","path":"vocals.wav"}
    {"v":1,"event":"done","outputs":["bass","drums","other","vocals"]}
    {"v":1,"event":"error","code":"separation.engine_error","detail":"..."}

Unknown event names are ignored so a newer worker can add events without
breaking an older parent. A ``v`` other than :data:`PROTOCOL_VERSION` is a
protocol mismatch. Exit codes are fixed by ticket 06 §4.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, cast

PROTOCOL_VERSION: Final = 1

EVENT_READY: Final = "ready"
EVENT_BLOCK: Final = "block"
EVENT_PROGRESS: Final = "progress"
EVENT_STEM: Final = "stem"
EVENT_DONE: Final = "done"
EVENT_ERROR: Final = "error"

KNOWN_EVENTS: Final[frozenset[str]] = frozenset(
    {
        EVENT_READY,
        EVENT_BLOCK,
        EVENT_PROGRESS,
        EVENT_STEM,
        EVENT_DONE,
        EVENT_ERROR,
    }
)

STEM_NAMES: Final[tuple[str, ...]] = ("vocals", "drums", "bass", "other")
WORKER_OUTPUTS: Final[frozenset[str]] = frozenset(STEM_NAMES)

# Ticket 06 §4: 0 success, 2 handled failure, 3 model unavailable, 4 invalid input.
EXIT_OK: Final = 0
EXIT_HANDLED: Final = 2
EXIT_MODEL_UNAVAILABLE: Final = 3
EXIT_INVALID_INPUT: Final = 4

# Worker-side codes for the handled exits. The parent maps unknown codes to
# ``separation.engine_error``; ``cancelled`` becomes a cancelled Job.
CODE_CANCELLED: Final = "cancelled"
CODE_ENGINE_ERROR: Final = "separation.engine_error"
CODE_MODEL_MISSING: Final = "separation.model_missing"


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
    detail: str | None = None
    block: int | None = None
    blocks: int | None = None
    stem: str | None = None
    path: str | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    outputs: tuple[str, ...] = ()
    engine: str | None = None
    engine_version: str | None = None
    model: str | None = None


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

    if name in (EVENT_READY, EVENT_ERROR):
        return _parse_status_event(name, payload, line)
    if name == EVENT_PROGRESS:
        return _parse_progress(payload, line)
    if name == EVENT_BLOCK:
        return _parse_block(payload, line)
    if name == EVENT_STEM:
        return _parse_stem(payload, line)
    return _parse_done(payload, line)


def block_count(*, total_frames: int, block_frames: int, fade_frames: int) -> int:
    """The block plan's count, shared by the worker and the manifest."""
    if total_frames <= 0:
        return 0
    if block_frames <= 0 or fade_frames < 0 or fade_frames >= block_frames:
        raise ValueError("invalid block or fade length")
    if total_frames <= block_frames:
        return 1
    return 1 + math.ceil((total_frames - block_frames) / (block_frames - fade_frames))


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


def _parse_status_event(name: str, payload: dict[str, object], line: str) -> WorkerEvent:
    if name == EVENT_ERROR:
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            raise ProtocolError("error event has no code", line=line)
        retryable = payload.get("retryable", False)
        if not isinstance(retryable, bool):
            raise ProtocolError("error.retryable must be a boolean", line=line)
        detail = _optional_text(payload, "detail", line)
        message = _optional_text(payload, "message", line)
        return WorkerEvent(
            event=name,
            code=code,
            detail=detail,
            message=message,
            retryable=retryable,
        )
    return WorkerEvent(
        event=name,
        engine=_optional_text(payload, "engine", line),
        engine_version=_optional_text(payload, "engine_version", line),
        model=_optional_text(payload, "model", line),
    )


def _parse_progress(payload: dict[str, object], line: str) -> WorkerEvent:
    fraction = payload.get("fraction")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
        raise ProtocolError("progress.fraction must be a number", line=line)
    value = float(fraction)
    if not 0.0 <= value <= 1.0:
        raise ProtocolError("progress.fraction must be within 0..1", line=line)
    stage = _optional_text(payload, "stage", line)
    detail = _optional_text(payload, "detail", line)
    block = _optional_index(payload, "block", line)
    blocks = _optional_index(payload, "blocks", line)
    if block is not None and blocks is not None and block > blocks:
        raise ProtocolError("progress.block exceeds progress.blocks", line=line)
    return WorkerEvent(
        event=EVENT_PROGRESS,
        fraction=value,
        stage=stage,
        detail=detail,
        block=block,
        blocks=blocks,
    )


def _parse_block(payload: dict[str, object], line: str) -> WorkerEvent:
    index = _optional_index(payload, "index", line)
    total = _optional_index(payload, "total", line)
    if index is None or total is None:
        raise ProtocolError("block event needs index and total", line=line)
    if index > total:
        raise ProtocolError("block.index exceeds block.total", line=line)
    fraction = payload.get("fraction")
    value: float | None = None
    if fraction is not None:
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            raise ProtocolError("block.fraction must be a number", line=line)
        value = float(fraction)
        if not 0.0 <= value <= 1.0:
            raise ProtocolError("block.fraction must be within 0..1", line=line)
    return WorkerEvent(event=EVENT_BLOCK, block=index, blocks=total, fraction=value)


def _parse_stem(payload: dict[str, object], line: str) -> WorkerEvent:
    stem = payload.get("stem")
    if not isinstance(stem, str) or stem not in WORKER_OUTPUTS:
        raise ProtocolError(f"unknown stem name: {stem!r}", line=line)
    path = payload.get("path")
    if not isinstance(path, str) or not is_safe_relative_output(path):
        raise ProtocolError(f"unsafe stem path: {path!r}", line=line)
    return WorkerEvent(event=EVENT_STEM, stem=stem, path=path)


def _parse_done(payload: dict[str, object], line: str) -> WorkerEvent:
    raw = payload.get("outputs", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ProtocolError("done.outputs must be a list of names", line=line)
    outputs = tuple(cast("list[str]", raw))
    unknown = set(outputs) - WORKER_OUTPUTS
    if unknown:
        raise ProtocolError(f"done.outputs has unknown names: {sorted(unknown)}", line=line)
    return WorkerEvent(event=EVENT_DONE, outputs=outputs)


def _optional_text(payload: dict[str, object], key: str, line: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError(f"{key} must be a string", line=line)
    return value


def _optional_index(payload: dict[str, object], key: str, line: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProtocolError(f"{key} must be a positive integer", line=line)
    return value
