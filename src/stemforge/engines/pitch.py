"""``PitchEngine`` protocol and the Rubber Band CLI adapter.

The only Rubber Band adapter in the application. It runs the
vendored ``rubberband`` 4.0.0 CLI in R3/offline/formant mode with tempo
unchanged, writes ``<name>.partial.wav`` inside the caller's directory, validates
the result with ffprobe, and only then renames it into place. Cancellation kills
the whole process group; every failure leaves no partial behind and surfaces as a
coded :class:`~stemforge.errors.StemForgeError`:

``pitch.engine_missing``
    the CLI is absent or not executable.
``pitch.render_failed``
    the CLI exited non-zero, reported no progress, or the Instrumental sum or
    peak measurement failed.
``pitch.output_invalid``
    the produced WAV is unreadable or is not 44.1 kHz stereo float32 with the
    input's duration (±50 ms).

Zero semitones is not a render: ``Render`` is disabled at ``0`` and the
Original is shown instead, so the adapter rejects ``0`` as a caller error
(``ValueError``) rather than producing a redundant ``+0`` set.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final, Protocol

from stemforge import errors
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import RenderScope
from stemforge.engines.media import CANONICAL_CHANNELS, CANONICAL_SAMPLE_RATE, FfmpegTool
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.platform import binaries, process

MIN_SEMITONES: Final = -12
MAX_SEMITONES: Final = 12

ENGINE_NAME: Final = "rubberband"
# The one render quality: R3 engine, offline, formant on,
# ignore clipping, default window. No draft mode and no formant toggle.
ENGINE_PRESET: Final = "r3-formant-offline-ignore-clipping"
FORMANT_PRESERVED: Final = True
WINDOW: Final = "default"

DURATION_TOLERANCE_S: Final = 0.050
PROGRESS_STUDY_START: Final = 0.02
PROGRESS_STUDY_END: Final = 0.45
PROGRESS_PROFILE: Final = 0.47
PROGRESS_PROCESS_START: Final = 0.50
PROGRESS_PROCESS_END: Final = 0.96
PROGRESS_VALIDATING: Final = 0.98
PROGRESS_DONE: Final = 1.0

ERROR_TAIL_CHARS: Final = 2_000
VERSION_TIMEOUT_S: Final = 30.0

# ``ProgressSink`` is ``(fraction, stage, detail) -> None``.
ProgressSink = Callable[[float, str, str], None]


# ---------------------------------------------------------------------------
# Pure math and validation
# ---------------------------------------------------------------------------


def validate_semitones(semitones: int) -> int:
    """Return ``semitones`` or raise ``ValueError`` for an unusable shift.

    The UI offers ``-12...+12`` and disables the action at ``0``;
    the application never asks for a ``0`` render because the Original already
    is that view.
    """
    if isinstance(semitones, bool) or not isinstance(semitones, int):
        raise ValueError(f"semitones must be an integer: {semitones!r}")
    if not MIN_SEMITONES <= semitones <= MAX_SEMITONES:
        raise ValueError(
            f"semitones must be between {MIN_SEMITONES} and {MAX_SEMITONES}: {semitones}"
        )
    if semitones == 0:
        raise ValueError("0 semitones is not a render; the Original is the 0-semitone view")
    return semitones


def semitone_ratio(semitones: int) -> float:
    """The equal-temperament frequency ratio for ``semitones``."""
    if isinstance(semitones, bool) or not isinstance(semitones, int):
        raise ValueError(f"semitones must be an integer: {semitones!r}")
    return math.pow(2.0, semitones / 12.0)


def shifted_frequency(frequency_hz: float, semitones: int) -> float:
    """The frequency ``frequency_hz`` becomes after shifting by ``semitones``."""
    if not isinstance(frequency_hz, (int, float)) or isinstance(frequency_hz, bool):
        raise ValueError(f"frequency must be a number: {frequency_hz!r}")
    number = float(frequency_hz)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"frequency must be positive and finite: {frequency_hz!r}")
    return number * semitone_ratio(semitones)


def render_key_for(
    *,
    source_id: str,
    scope: RenderScope | str,
    semitones: int,
    input_hashes: Sequence[str],
    engine_version: str,
    sample_rate: int = CANONICAL_SAMPLE_RATE,
    channels: int = CANONICAL_CHANNELS,
    formant: bool = FORMANT_PRESERVED,
    window: str = WINDOW,
    engine: str = ENGINE_NAME,
    preset: str = ENGINE_PRESET,
    separation_method: str | None = None,
) -> str:
    """The content-addressed identity of one render set.

    The key covers the source identity, the scope, the semitones, the sorted
    input hashes, and the engine version/preset. Scope is part of the identity,
    so a master render can never satisfy a stems request even when both exist at
    the same semitone. A stems key also names the Separation method, so two
    methods at one semitone cannot collide; an original render has no method.
    """
    validate_semitones(semitones)
    resolved_scope = RenderScope(scope)
    if not source_id:
        raise ValueError("source_id is required for a render key")
    hashes = sorted(input_hashes)
    if not hashes or any(not isinstance(digest, str) or not digest for digest in hashes):
        raise ValueError("a render key needs at least one non-empty input hash")
    if resolved_scope is RenderScope.STEMS:
        if not separation_method:
            raise ValueError("a stems render key needs a Separation method")
    else:
        separation_method = None
    payload: dict[str, object] = {
        "engine": engine,
        "engine_version": engine_version,
        "formant": formant,
        "inputs": hashes,
        "sample_rate": sample_rate,
        "channels": channels,
        "scope": resolved_scope.value,
        "semitones": semitones,
        "source_id": source_id,
        "window": window,
        "preset": preset,
    }
    if separation_method is not None:
        payload["separation_method"] = separation_method
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(encoded.encode("utf-8"), digest_size=32).hexdigest()


# ---------------------------------------------------------------------------
# Engine contract
# ---------------------------------------------------------------------------


class PitchEngine(Protocol):
    """The pitch adapter the render Job composes."""

    @property
    def version(self) -> str: ...

    def shift(
        self,
        src: Path,
        dst: Path,
        *,
        semitones: int,
        cancel: CancelToken,
        log: logging.Logger,
        progress: ProgressSink | None = None,
    ) -> None: ...

    def sum_stems(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None: ...

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float: ...


# ---------------------------------------------------------------------------
# Small helpers shared by the adapter
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    """The SHA-256 of ``path`` as lowercase hex (used for manifests and keys)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(text: str, limit: int = ERROR_TAIL_CHARS) -> str:
    stripped = text.strip()
    return stripped[-limit:] if len(stripped) > limit else stripped


def _partial_path(dst: Path) -> Path:
    if dst.suffix:
        return dst.with_name(f"{dst.stem}.partial{dst.suffix}")
    return dst.with_name(f"{dst.name}.partial")


def _remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _engine_missing(detail: str, path: Path | None) -> StemForgeError:
    return StemForgeError(
        errors.PITCH_ENGINE_MISSING,
        "the Rubber Band CLI is missing or not executable",
        detail=_tail(detail) or None,
        context={"path": str(path)} if path is not None else None,
    )


def _render_failed(detail: str, *, path: Path | None = None) -> StemForgeError:
    return StemForgeError(
        errors.PITCH_RENDER_FAILED,
        "Rubber Band could not render the pitch shift",
        detail=_tail(detail) or None,
        context={"path": str(path)} if path is not None else None,
        retryable=True,
    )


def _output_invalid(path: Path, detail: str) -> StemForgeError:
    return StemForgeError(
        errors.PITCH_OUTPUT_INVALID,
        "the pitch render did not pass validation",
        retryable=True,
        detail=_tail(detail) or None,
        context={"path": str(path)},
    )


# ---------------------------------------------------------------------------
# Rubber Band CLI adapter
# ---------------------------------------------------------------------------


class _ProgressReader:
    """Parse the CLI's carriage-return progress stream into one fraction."""

    def __init__(self, sink: ProgressSink | None) -> None:
        self._sink = sink
        self._state = ""
        self._last = 0.0
        self.saw_percentage = False

    def feed(self, chunk: str) -> None:
        text = chunk.strip()
        if not text:
            return
        if text.startswith("Pass 1"):
            self._state = "study"
            self._emit(PROGRESS_STUDY_START, "Studying")
        elif text.startswith("Calculating profile"):
            self._emit(PROGRESS_PROFILE, "Calculating profile")
        elif text.startswith("Pass 2"):
            self._state = "process"
            self._emit(PROGRESS_PROCESS_START, "Processing")
        elif text.endswith("%") and text[:-1].strip().isdigit():
            percent = min(100, max(0, int(text[:-1].strip())))
            self.saw_percentage = True
            if self._state == "study":
                span = PROGRESS_STUDY_END - PROGRESS_STUDY_START
                self._emit(PROGRESS_STUDY_START + span * percent / 100.0, "Studying")
            elif self._state == "process":
                span = PROGRESS_PROCESS_END - PROGRESS_PROCESS_START
                self._emit(PROGRESS_PROCESS_START + span * percent / 100.0, "Processing")

    def validating(self) -> None:
        self._emit(PROGRESS_VALIDATING, "Validating")

    def done(self) -> None:
        self._emit(PROGRESS_DONE, "Done")

    def _emit(self, fraction: float, stage: str) -> None:
        fraction = min(1.0, max(self._last, fraction))
        self._last = fraction
        if self._sink is not None:
            self._sink(fraction, stage, "")


class RubberBandEngine:
    """The :class:`PitchEngine`: Rubber Band 4.0.0 CLI in R3/formant mode."""

    def __init__(
        self,
        *,
        rubberband_path: Path | None = None,
        media: FfmpegTool | None = None,
    ) -> None:
        self._rubberband_path = rubberband_path
        self._media: FfmpegTool = media if media is not None else FfmpegTool()
        self._version: str | None = None

    @property
    def media(self) -> FfmpegTool:
        """The media tool used for validation, summing, and peak measurement."""
        return self._media

    @property
    def path(self) -> Path:
        """The resolved Rubber Band executable."""
        if self._rubberband_path is None:
            try:
                resolved = binaries.rubberband()
            except binaries.BundleDamagedError:
                # A frozen Bundle with a missing vendor binary is a preflight
                # failure, not a render-time engine absence.
                raise
            except FileNotFoundError as error:
                raise _engine_missing(str(error), None) from error
            if resolved is None:
                raise _engine_missing(
                    f"rubberband is neither vendored nor on PATH ({binaries.RUBBERBAND_ENV})", None
                )
            self._rubberband_path = resolved
        return self._rubberband_path

    @property
    def version(self) -> str:
        """The CLI version, used in render keys and manifests."""
        if self._version is None:
            self._version = self._detect_version()
        return self._version

    def _detect_version(self) -> str:
        executable = self.path
        try:
            run = process.run_capture(
                [str(executable), "--version"],
                timeout_s=VERSION_TIMEOUT_S,
                env=binaries.tool_environment(executable),
            )
        except OSError as error:
            raise _engine_missing(str(error), executable) from error
        except TimeoutError as error:
            raise _render_failed(f"rubberband --version timed out: {error}", path=executable) from (
                error
            )
        if not run.ok:
            raise _engine_missing(_tail(run.stderr) or "rubberband --version failed", executable)
        version = (run.stdout.strip() or run.stderr.strip()).splitlines()
        if not version or not version[0].strip():
            raise _render_failed("rubberband reported no version", path=executable)
        return version[0].strip()

    def shift(
        self,
        src: Path,
        dst: Path,
        *,
        semitones: int,
        cancel: CancelToken,
        log: logging.Logger,
        progress: ProgressSink | None = None,
    ) -> None:
        validate_semitones(semitones)
        cancel.check()
        executable = self.path
        argv = [
            str(executable),
            "-3",
            "-F",
            "-p",
            str(semitones),
            "--ignore-clipping",
            str(src),
        ]
        partial = _partial_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _remove(partial)
        reader = _ProgressReader(progress)
        log.debug("pitch: %s %s", " ".join(argv), partial)
        try:
            returncode, stderr = self._run(argv, partial, cancel=cancel, reader=reader)
            if returncode != 0:
                cancel.check()
                raise _render_failed(_tail(stderr) or f"rubberband exited {returncode}", path=src)
            if not reader.saw_percentage:
                raise _render_failed("rubberband produced no progress output", path=src)
            reader.validating()
            self._validate(src, partial)
            cancel.check()
            os.replace(partial, dst)
            reader.done()
        except BaseException:
            _remove(partial)
            raise

    def sum_stems(
        self,
        srcs: Sequence[Path],
        dst: Path,
        *,
        cancel: CancelToken,
        log: logging.Logger,
    ) -> None:
        cancel.check()
        try:
            self._media.sum_wavs(srcs, dst, cancel=cancel, log=log)
        except JobCancelled:
            raise
        except StemForgeError as error:
            raise _render_failed(
                f"the shifted Instrumental could not be summed: {error.code} {error.detail or ''}",
                path=dst,
            ) from error

    def peak_dbfs(self, path: Path, *, cancel: CancelToken, log: logging.Logger) -> float:
        cancel.check()
        try:
            return self._media.peak_dbfs(path, cancel=cancel, log=log)
        except JobCancelled:
            raise
        except StemForgeError as error:
            raise _render_failed(
                f"the shifted peak could not be measured: {error.code} {error.detail or ''}",
                path=path,
            ) from error

    # -- internals ----------------------------------------------------------

    def _run(
        self,
        argv: Sequence[str],
        partial: Path,
        *,
        cancel: CancelToken,
        reader: _ProgressReader,
    ) -> tuple[int, str]:
        try:
            proc = process.spawn(
                [*argv, str(partial)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=binaries.tool_environment(Path(argv[0])),
            )
        except OSError as error:
            raise _engine_missing(str(error), Path(argv[0])) from error
        stderr_lines: list[str] = []

        def pump() -> None:
            stream = proc.stderr
            if stream is None:  # pragma: no cover - stderr is always a pipe here
                return
            buffer = ""
            while True:
                char = stream.read(1)
                if not char:
                    break
                if char in "\r\n":
                    if buffer:
                        reader.feed(buffer)
                        stderr_lines.append(buffer)
                        buffer = ""
                else:
                    buffer += char
            if buffer:
                reader.feed(buffer)
                stderr_lines.append(buffer)

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        try:
            while True:
                try:
                    returncode = proc.wait(timeout=process.POLL_INTERVAL_S)
                    break
                except subprocess.TimeoutExpired:
                    cancel.check()
        finally:
            if proc.poll() is None:
                process.terminate_tree(proc)
            thread.join(timeout=process.TERMINATE_GRACE_S)
        return returncode, "\n".join(stderr_lines)

    def _validate(self, src: Path, partial: Path) -> None:
        try:
            expected = self._media.probe(src)
        except JobCancelled:
            raise
        except StemForgeError as error:
            raise _output_invalid(partial, f"unreadable input: {error.code}") from error
        try:
            actual = self._media.probe(partial)
        except JobCancelled:
            raise
        except StemForgeError as error:
            raise _output_invalid(partial, f"unreadable output: {error.code}") from error

        problems: list[str] = []
        if actual.codec != "pcm_f32le":
            problems.append(f"codec={actual.codec}")
        if actual.sample_rate != CANONICAL_SAMPLE_RATE:
            problems.append(f"sample_rate={actual.sample_rate}")
        if actual.channels != CANONICAL_CHANNELS:
            problems.append(f"channels={actual.channels}")
        if expected.duration_s > 0.0:
            drift = abs(actual.duration_s - expected.duration_s)
            if drift > DURATION_TOLERANCE_S:
                problems.append(f"duration_drift={drift:.3f}")
        if problems:
            raise _output_invalid(partial, ", ".join(problems))
