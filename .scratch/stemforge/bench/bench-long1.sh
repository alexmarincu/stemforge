#!/usr/bin/env bash
# Long-track scaling part 1: 10 and 30 minutes, sequential.
set -uo pipefail
cd "$(dirname "$0")"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

for m in 10 30; do
  echo "### separate-${m}m $(date -Is)" >&2
  .venv/bin/python measure.py "separate-${m}m" -- \
    .venv/bin/demucs --float32 -n htdemucs -d cpu -o "out/sep-${m}m" "audio/master-${m}m.wav" \
    2>/dev/null | tee -a results.jsonl
  du -sb "out/sep-${m}m" | tee -a disk.txt
done
echo "done long part 1"
