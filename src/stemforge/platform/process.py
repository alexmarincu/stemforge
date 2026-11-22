"""Subprocess spawning, line readers, tree termination, and captured runs.

No shell and no string argv. Every adapter goes through here so a
Job cancel kills the whole process group on both platforms.

The module is foundation code: it imports no ``stemforge`` module so
``engines`` and ``jobs`` can both use it. Callers pass a ``cancel_check`` that
raises their own cancellation exception; this module only guarantees that the
process tree is terminated before that exception leaves :func:`run_capture`.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import IO, Final

POLL_INTERVAL_S: Final = 0.05
TERMINATE_GRACE_S: Final = 5.0

PathArg = str | os.PathLike[str]
CancelCheck = Callable[[], None]
LineCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CapturedRun:
    """The complete result of a non-interactive child process."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def spawn(
    argv: Sequence[PathArg],
    *,
    stdin: int | None = subprocess.DEVNULL,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
    cwd: PathArg | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Start a child in its own process group with text pipes and no shell."""
    if not argv:
        raise ValueError("argv must contain at least one element")
    return subprocess.Popen(
        [os.fspath(argument) for argument in argv],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=os.name != "nt",
        creationflags=_creation_flags(),
    )


def iter_lines(stream: IO[str]) -> Iterator[str]:
    """Yield the non-empty lines of a text stream without their terminators."""
    for raw in stream:
        line = raw.rstrip("\r\n")
        if line:
            yield line


def terminate_tree(
    proc: subprocess.Popen[str],
    *,
    grace_s: float = TERMINATE_GRACE_S,
) -> None:
    """Terminate the child and every process it started."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
    else:
        _signal_group(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            _signal_group(proc.pid, signal.SIGKILL)
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=grace_s)


def run_capture(
    argv: Sequence[PathArg],
    *,
    timeout_s: float | None = None,
    cancel_check: CancelCheck | None = None,
    cwd: PathArg | None = None,
    env: Mapping[str, str] | None = None,
    on_stdout_line: LineCallback | None = None,
    on_stderr_line: LineCallback | None = None,
) -> CapturedRun:
    """Run a child to completion and capture its text output.

    ``cancel_check`` is called before spawning and on every poll; if it raises,
    the process tree is terminated and the exception propagates. A ``timeout_s``
    overrun terminates the tree and raises :class:`TimeoutError`.
    """
    if cancel_check is not None:
        cancel_check()

    proc = spawn(argv, cwd=cwd, env=env)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    readers = [
        threading.Thread(
            target=_pump, args=(proc.stdout, stdout_lines, on_stdout_line), daemon=True
        ),
        threading.Thread(
            target=_pump, args=(proc.stderr, stderr_lines, on_stderr_line), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    try:
        while True:
            try:
                returncode = proc.wait(timeout=POLL_INTERVAL_S)
                break
            except subprocess.TimeoutExpired:
                if cancel_check is not None:
                    cancel_check()
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"{os.fspath(argv[0])} exceeded {timeout_s:g}s") from None
    finally:
        if proc.poll() is None:
            terminate_tree(proc)
        for reader in readers:
            reader.join(timeout=TERMINATE_GRACE_S)

    return CapturedRun(
        argv=tuple(os.fspath(argument) for argument in argv),
        returncode=returncode,
        stdout="\n".join(stdout_lines),
        stderr="\n".join(stderr_lines),
    )


def _pump(
    stream: IO[str] | None,
    sink: list[str],
    callback: LineCallback | None,
) -> None:
    if stream is None:
        return
    for line in iter_lines(stream):
        sink.append(line)
        if callback is not None:
            callback(line)


def _signal_group(pid: int, sig: int) -> None:
    try:
        process_group = os.getpgid(pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process_group, sig)


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
