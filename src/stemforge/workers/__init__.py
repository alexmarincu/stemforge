"""Worker entry points reached only by executable dispatch, never by import.

``workers.separation`` is the only module allowed to import torch or Demucs, and
only inside its run function (ticket 06 §8/§11).
"""

from __future__ import annotations
