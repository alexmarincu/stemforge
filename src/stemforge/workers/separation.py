"""The Separation worker: reads one canonical WAV, writes five WAVs as JSONL events.

Ticket 4 owns the implementation. torch and Demucs are imported lazily inside
``run()``, never at module scope.
"""

from __future__ import annotations
