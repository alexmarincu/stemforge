#!/usr/bin/env bash
# Deterministic synthetic fixtures. Content does not affect Demucs/Rubber Band
# compute time; musicality is irrelevant to the envelope measurements.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p audio

SR=44100
MIN=240

# 4-minute stereo master, f32 canonical form.
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "sine=frequency=110:duration=${MIN}:sample_rate=${SR}" \
  -f lavfi -i "sine=frequency=220:duration=${MIN}:sample_rate=${SR}" \
  -f lavfi -i "sine=frequency=330:duration=${MIN}:sample_rate=${SR}" \
  -f lavfi -i "anoisesrc=color=pink:duration=${MIN}:sample_rate=${SR}:amplitude=0.15" \
  -filter_complex "[0]volume=0.25[a];[1]volume=0.18[b];[2]volume=0.10[c];[3]volume=0.10[d];[a][b][c][d]amix=inputs=4:normalize=0,pan=stereo|c0=c0|c1=c0,alimiter=limit=0.89" \
  -c:a pcm_f32le -ar "$SR" -ac 2 audio/master-4m.wav

# 4-minute MP3 320 proxy for a downloaded source, used to time decode.
ffmpeg -hide_banner -loglevel error -y -i audio/master-4m.wav \
  -c:a libmp3lame -b:a 320k audio/master-4m.mp3

# Long tracks: loop the master. 10/30/60 minutes.
for m in 10 30 60; do
  loops=$((m / 4))
  ffmpeg -hide_banner -loglevel error -y -stream_loop $((loops - 1)) \
    -i audio/master-4m.wav -t $((m * 60)) -c:a copy audio/master-${m}m.wav
done

ls -lh audio
