#!/usr/bin/env bash
# Re-run the 4-minute Separation (repeatability + sustained clock), and run the
# exact pitch fixture from research 03 to reconcile its 108.8 s with our 47.6 s.
set -uo pipefail
cd "$(dirname "$0")"
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4

# Research 03's fixture: 210 s, 44.1 kHz stereo, pink noise + 220 Hz sine, s16.
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "anoisesrc=color=pink:duration=210:sample_rate=44100:amplitude=0.3" \
  -f lavfi -i "sine=frequency=220:duration=210:sample_rate=44100" \
  -filter_complex "[0][1]amix=inputs=2,pan=stereo|c0=c0|c1=c0" \
  -c:a pcm_s16le audio/pitch-ref-210s.wav

(
  while true; do
    awk '/cpu MHz/{s+=$4;n++} END{if(n) printf "%d %.0f\n", systime(), s/n}' /proc/cpuinfo
    sleep 3
  done
) > freq.log &
MON=$!
trap 'kill $MON 2>/dev/null' EXIT

.venv/bin/python measure.py separate-4m-rerun -- \
  .venv/bin/demucs --float32 -n htdemucs -d cpu -o out/sep-4m-rerun audio/master-4m.wav \
  2>/dev/null | tee -a results.jsonl

.venv/bin/python measure.py pitch-ref-210s -- \
  rubberband -3 -F -p 12 --ignore-clipping audio/pitch-ref-210s.wav out/pitch-ref-210s.wav \
  2>/dev/null | tee -a results.jsonl

kill $MON 2>/dev/null || true
trap - EXIT
echo "done verify"
