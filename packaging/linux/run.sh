#!/bin/sh
# Thin launcher: ask the Python preflight, fall back to browser mode.
# Ticket 10 §5. The probe itself lives in platform/preflight.py (ticket 14).
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if "$here/StemForge" --preflight --quiet; then
    exec "$here/StemForge" "$@"
fi
"$here/StemForge" --preflight || true
echo "StemForge: native window unavailable; starting in browser mode." >&2
STEMFORGE_BROWSER=1 exec "$here/StemForge" "$@"
