"""Logging configuration: the app log and the per-Job log handlers.

Stdlib ``logging`` only, root logger ``stemforge`` (ticket 06 §9). Ticket 14 owns
rotation, sizes, and the final configuration.
"""

from __future__ import annotations

import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the ``stemforge`` logger tree for the current process."""
