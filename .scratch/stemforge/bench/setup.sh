#!/usr/bin/env bash
# One-time environment for the ticket-12 benchmarks. Linux x86_64.
set -euo pipefail
cd "$(dirname "$0")"

uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python "torch==2.15.0+cpu" \
  --index-url https://download.pytorch.org/whl/cpu
uv pip install --python .venv/bin/python \
  "demucs==4.1.0" "numpy==2.5.4" "pyinstaller==6.22.3"

# Warm the pinned weights cache (81 MB) before timing anything.
.venv/bin/python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

./fixtures.sh
echo "ready: run ./bench-quick.sh, ./bench-verify.sh, ./bench-long1.sh, ./bench-long2.sh, ./bench-pyinstaller.sh"
