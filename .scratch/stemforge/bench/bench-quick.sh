#!/usr/bin/env bash
# Sequential benchmark driver. One measurement at a time so timings are clean.
set -uo pipefail
cd "$(dirname "$0")"
mkdir -p out
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4

run() {
  local label=$1; shift
  echo "### $label" >&2
  .venv/bin/python measure.py "$label" -- "$@" 2>/dev/null | tee -a results.jsonl
}

FF="ffmpeg -hide_banner -loglevel error -y"

run decode-4m $FF -i audio/master-4m.mp3 -c:a pcm_f32le -ar 44100 -ac 2 out/decode-4m.wav
run preview-192-4m $FF -i audio/master-4m.wav -c:a libmp3lame -b:a 192k out/preview-4m.mp3
run export-wav24-4m $FF -i audio/master-4m.wav -c:a pcm_s24le out/export-4m.wav
run export-flac24-4m $FF -i audio/master-4m.wav -c:a flac -sample_fmt s32 -bits_per_raw_sample 24 out/export-4m.flac
run export-mp3-320-4m $FF -i audio/master-4m.wav -c:a libmp3lame -b:a 320k out/export-4m.mp3
run pitch-plus12-4m rubberband -3 -F -p 12 --ignore-clipping audio/master-4m.wav out/pitch-plus12-4m.wav
run pitch-minus12-4m rubberband -3 -F -p -12 --ignore-clipping audio/master-4m.wav out/pitch-minus12-4m.wav
run separate-4m .venv/bin/demucs --float32 -n htdemucs -d cpu -o out/sep-4m audio/master-4m.wav

echo "done quick+4m"
