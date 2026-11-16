"""``WorkspaceService``: the only API the UI calls (ticket 06 §2).

Ticket 08 owns the implementation. This module boundary is fixed now so the UI can
never reach past it into jobs, engines, or pipeline code.
"""

from __future__ import annotations


class WorkspaceService:
    """Owns the Workspace and orchestrates Jobs on behalf of the UI."""
