#!/usr/bin/env bash
# The local pre-PR gate. CI runs the same checks on Linux and Windows.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv sync --frozen --inexact
uv run ruff format --check .
uv run ruff check .
uv run mypy src/stemforge
uv run lint-imports
uv run python scripts/generate_notices.py --check-release
uv run python scripts/generate_notices.py --check
uv run pytest -m "unit or ui or licensing"

self_test_home="$(mktemp -d "${TMPDIR:-/tmp}/stemforge-selftest.XXXXXX")"
trap 'rm -rf "$self_test_home"' EXIT
STEMFORGE_HOME="$self_test_home" uv run stemforge --self-test

# The pinned vendor set is optional locally, but if present it must
# match vendor.lock.json before the integration layer uses it.
if [[ -x vendor/ffmpeg/ffmpeg || -x vendor/deno/deno ]]; then
  scripts/vendor-fetch.sh --verify-only
fi

# Integration tests need the real tools: use PATH, or the vendored set when present.
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  uv run pytest -m integration
elif [[ -d vendor ]]; then
  uv run pytest -m integration
else
  echo "check.sh: ffmpeg/ffprobe not found; skipping integration tests" >&2
fi
