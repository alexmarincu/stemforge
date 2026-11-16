"""The root page: composition, client-local selection and A/B, and the poll timer.

Ticket 8 owns the implementation. All state changes go through ``WorkspaceService``;
this module must never import engines, workers, pipeline, or jobs.
"""

from __future__ import annotations
