"""Startup preflight checks for the window stack, storage, and bundled files.

Returns ``CheckResult`` values; the first fatal result wins (ticket 06 §6,
ticket 10). No behaviour is implemented in the scaffold.
"""

from __future__ import annotations
