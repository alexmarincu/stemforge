"""Cooperative cancellation for Jobs and the engine adapters that serve them.

``CancelToken`` wraps a ``threading.Event`` and raises :class:`JobCancelled` at
safe checkpoints. It lives in the domain layer rather than
``jobs/job.py`` because the engine protocols take it and the enforced import
contract keeps ``engines`` independent of ``jobs``; ``jobs.job`` re-exports it
for the documented import path.
"""

from __future__ import annotations

import threading

from ..errors import JobCancelled


class CancelToken:
    """A thread-safe flag that turns into ``JobCancelled`` at a checkpoint."""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        """Whether ``cancel()`` has been called."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation; safe to call from any thread, idempotent."""
        self._event.set()

    def check(self) -> None:
        """Raise :class:`JobCancelled` if cancellation was requested."""
        if self._event.is_set():
            raise JobCancelled()
