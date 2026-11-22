"""Storage policy constants shared by settings and the artifact store.

The default cache cap is a product decision; it lives in the
platform layer so both ``settings`` and ``artifacts`` can use one value without
depending on each other.
"""

from __future__ import annotations

from typing import Final

DEFAULT_CACHE_CAP_BYTES: Final = 8 * 1024**3
