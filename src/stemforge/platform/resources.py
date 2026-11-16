"""Bundled assets and binaries: the dev root and the two frozen roots.

Code and package data resolve from ``sys._MEIPASS`` when frozen; ``vendor/**`` and
the licence tree resolve from the Bundle root (ADR 0010). Ticket 10 implements it.
"""

from __future__ import annotations
