"""Worker entry points reached only by executable dispatch, never by import.

``workers.separation`` is the only module allowed to import torch or Demucs, and
only inside its run function (ticket 06 §8/§11). ``dispatch`` is called by
``stemforge.__main__`` for the frozen ``--worker <name>`` form; development runs
``python -m stemforge.workers.<name>`` directly, so both paths share ``run()``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from stemforge.worker_protocol import EXIT_INVALID_INPUT

__all__ = ["dispatch"]


def dispatch(name: str, argv: Sequence[str] = ()) -> int:
    """Run the named worker with ``argv`` and return its process exit code."""
    if name == "separation":
        from stemforge.workers.separation import run

        return run(argv)
    print(f"stemforge: unknown worker: {name!r}", file=sys.stderr)
    return EXIT_INVALID_INPUT
