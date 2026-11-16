"""Presentation gain: one static gain per artifact set (ADR 0002).

Every stage that produces an artifact set (import, separation, pitch render)
measures the set's sample peak once it is complete. If the loudest sample exceeds
0 dBFS, the whole set records one gain that brings that peak to -1.0 dBFS;
otherwise the gain is ``0``. The gain is metadata only: preview and export
encoders apply it, and master float32 WAVs are never attenuated in place
(ticket 05 §5).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Final

PEAK_TARGET_DBFS: Final = -1.0


def presentation_gain(peaks_dbfs: Iterable[float]) -> float:
    """Return the one gain a set needs, or ``0.0`` when it already fits.

    ``peaks_dbfs`` holds one sample-peak measurement per master artifact in the
    set. Silence may be ``-inf``; an empty or all-quiet set needs no gain.
    """
    loudest = float("-inf")
    for peak in peaks_dbfs:
        value = float(peak)
        if math.isnan(value):
            raise ValueError("peak must not be NaN")
        if value > loudest:
            loudest = value
    if loudest <= 0.0:
        return 0.0
    return PEAK_TARGET_DBFS - loudest
