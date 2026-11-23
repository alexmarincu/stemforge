#!/usr/bin/env bash
# Fail before CI does: the default runs the single-machine checks in CI's
# marker split; --fast keeps the commit hook quick.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() {
  echo "usage: scripts/check.sh [--fast]" >&2
}

fast=0
if [[ $# -gt 1 ]]; then
  usage
  exit 2
fi
case "${1:-}" in
  --fast) fast=1 ;;
  -h | --help) usage; exit 0 ;;
  "") ;;
  *)
    usage
    exit 2
    ;;
esac

if (( ! fast )); then
  uv lock --check
fi

uv sync --frozen --inexact
uv run ruff format --check .
uv run ruff check .
uv run mypy src/stemforge
uv run lint-imports

if (( fast )); then
  exit 0
fi

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x scripts/*.sh packaging/linux/*.sh .githooks/pre-push .githooks/pre-commit
else
  echo "check.sh: shellcheck not found; skipping the shell checks" >&2
fi

# A broken interpreter is as useless as a missing one, and its own error is
# opaque; probe it before the parser turns it into a failed push.
if command -v pwsh >/dev/null 2>&1 &&
  pwsh -NoProfile -NonInteractive -Command 'exit 0' >/dev/null 2>&1; then
  uv run --frozen --no-sync python scripts/check_powershell.py
else
  echo "check.sh: pwsh is not usable; skipping the PowerShell syntax check" >&2
fi

uv run python scripts/generate_notices.py --check-release
uv run python scripts/generate_notices.py --check
uv run pytest -m licensing
uv run pytest -m unit
uv run pytest -m ui

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
