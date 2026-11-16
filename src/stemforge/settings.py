"""``SettingsStore``: recent sources, export directory, and first-run flags.

A JSON file under the app data directory with atomic replacement; never browser or
NiceGUI storage (ticket 06 §5, ticket 07).
"""

from __future__ import annotations


class SettingsStore:
    """Reads and writes the single settings JSON document atomically."""
