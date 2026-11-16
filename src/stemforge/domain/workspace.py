"""The ``Workspace`` aggregate and its invariants.

Ticket 08 owns the implementation: one Track per Source, ordered Tracks, and
reference release on removal.
"""

from __future__ import annotations


class Workspace:
    """The ordered set of Tracks visible in the UI."""
