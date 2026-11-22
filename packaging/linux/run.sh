#!/bin/sh
# Thin launcher for the Linux Bundle.
#
# The dependency probe lives in Python, not here: `--preflight --quiet` returns
# 3 when GTK/WebKitGTK are missing and 4 when the GStreamer preview stack is
# missing. Those two cases fall back to browser mode; every other fatal result
# stops the launch so a broken installation is not hidden behind a fallback.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

# Rubber Band links the co-located libsndfile; the app also sets this for the
# worker through platform.binaries.tool_environment(). Keeping it here makes a
# direct `run.sh` launch behave the same as one started from the desktop entry.
if [ -d "$here/vendor/rubberband" ]; then
    LD_LIBRARY_PATH="$here/vendor/rubberband${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_LIBRARY_PATH
fi

# The browser escape hatch skips the native probe entirely.
case "${STEMFORGE_BROWSER:-}" in
    1 | true | TRUE | yes | YES | on | ON) exec "$here/StemForge" "$@" ;;
esac

# Diagnostics and headless modes never open the workspace window. Run them
# directly so `run.sh --self-test` (and the AppImage extraction check) is exact.
for argument in "$@"; do
    case "$argument" in
        --self-test | --smoke-test | --smoke-separation | --preflight | --worker | \
            --import | --install-model | --report | --help | -h)
            exec "$here/StemForge" "$@"
            ;;
    esac
done

if "$here/StemForge" --preflight --quiet; then
    exec "$here/StemForge" "$@"
else
    status=$?
fi

case "$status" in
    3 | 4) ;;
    *) exit "$status" ;;
esac

echo "StemForge: native window unavailable; starting in browser mode." >&2
STEMFORGE_BROWSER=1
export STEMFORGE_BROWSER
exec "$here/StemForge" "$@"
