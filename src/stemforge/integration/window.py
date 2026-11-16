"""Native window configuration: title, icon, size, private mode, and downloads.

Module-level ``app.native.*`` configuration so the window child sees it (ticket 06
§7). Ticket 8 implements it; nothing here may import the UI at module scope.
"""

from __future__ import annotations
