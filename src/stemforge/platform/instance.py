"""The single-instance OS advisory lock at ``<config>/instance.lock``.

The handle stays open for the life of the process; the OS releases it when the
process exits, so a crash cannot leave a stale lock. A second instance gets
``acquire() is False`` and the entry point shows the native "already running"
message instead of starting a sweep or touching artifacts.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import TracebackType
from typing import BinaryIO, Self


class InstanceAlreadyRunning(RuntimeError):
    """Raised when entering an :class:`InstanceLock` another process holds."""


class InstanceLock:
    """An OS advisory lock held by one process for its whole lifetime."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._handle: BinaryIO | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Try to take the lock without blocking; return false if another copy holds it."""
        if self._handle is not None:
            return True
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        try:
            if sys.platform.startswith("win"):
                _lock_windows(handle)
            else:
                _lock_posix(handle)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """Release the lock; safe to call more than once."""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            if sys.platform.startswith("win"):
                _unlock_windows(handle)
            else:
                _unlock_posix(handle)
        finally:
            handle.close()

    def __enter__(self) -> Self:
        if not self.acquire():
            raise InstanceAlreadyRunning(f"another StemForge instance holds {self._path}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def _lock_posix(handle: BinaryIO) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_posix(handle: BinaryIO) -> None:
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_windows(handle: BinaryIO) -> None:
    msvcrt = importlib.import_module("msvcrt")

    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock_windows(handle: BinaryIO) -> None:
    msvcrt = importlib.import_module("msvcrt")

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
