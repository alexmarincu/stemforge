#!/usr/bin/env bash
# Long-track scaling part 2: 60 minutes, capped at 18 GiB so an overshoot
# cannot destabilize the host. Linux + systemd user session only.
set -uo pipefail
cd "$(dirname "$0")"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

systemd-run --user --scope -p MemoryMax=18G -p MemorySwapMax=0 \
  --setenv=OMP_NUM_THREADS=4 --setenv=MKL_NUM_THREADS=4 \
  "$PWD/.venv/bin/python" "$PWD/measure.py" separate-60m -- \
  "$PWD/.venv/bin/demucs" --float32 -n htdemucs -d cpu \
  -o "$PWD/out/sep-60m" "$PWD/audio/master-60m.wav" \
  2>/dev/null | tee -a results.jsonl

du -sb out/sep-60m >> disk.txt
echo "done long part 2"
