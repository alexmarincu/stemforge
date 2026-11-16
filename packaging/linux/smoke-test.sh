#!/usr/bin/env bash
# Run the frozen bundle's diagnostic flags and keep the JSON reports.
# Ticket 11 §4: --self-test plus --smoke-test, no weights, exit nonzero on failure.
# Ticket 20 adds --with-separation and the AppImage extraction check.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$repo_root"

bundle="${1:-$repo_root/dist/StemForge}"
reports="$repo_root/build/reports"

if [[ ! -x "$bundle/StemForge" ]]; then
    echo "smoke-test.sh: $bundle/StemForge is missing; run packaging/linux/build.sh" >&2
    exit 1
fi

mkdir -p "$reports"
# Keep the smoke run's writable state inside build/, never a real user profile.
export STEMFORGE_HOME="${STEMFORGE_HOME:-$repo_root/build/smoke-home}"

status=0
"$bundle/StemForge" --self-test --report "$reports/self-test.json" || status=1
"$bundle/StemForge" --smoke-test --report "$reports/smoke-test.json" || status=1
exit "$status"
