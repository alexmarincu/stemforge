"""One function per Job kind; each composes engines and the ArtifactStore.

Pipeline code validates every output, writes the manifest last, and promotes the
staging directory. No UI or NiceGUI imports (ticket 06 §2).
"""

from __future__ import annotations
