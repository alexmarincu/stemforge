"""``JobRunner``: the single background thread that owns Jobs.

Ticket 08 owns the queue, dedupe, watchdog, cancellation, and snapshots.
"""

from __future__ import annotations


class JobRunner:
    """Runs at most one Job at a time and publishes immutable snapshots."""
