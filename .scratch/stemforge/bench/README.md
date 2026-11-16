# Ticket 12 benchmark harness

Reproducible measurements behind the v1 performance envelope (`.scratch/stemforge/issues/12-performance-and-resource-envelope.md`).

## Rig

- Intel Core i7-4800MQ (Haswell, 4 cores / 8 threads, 2.7 GHz base, 3.7 GHz turbo;
  observed 2.0–3.5 GHz, ~2.9 GHz average during the runs)
- 23 GB RAM, Linux 7.3.4-arch1-1 (`schedutil` governor)
- Bench files on a Kingston SA400 SATA SSD (`/home`); the repo itself is on a
  spinning disk, so it is not used for artifacts
- NVIDIA Quadro K610M, 973 MiB VRAM — **not used**; CUDA torch is out of scope
  for v1 and the model does not fit
- Python 3.13.18, `torch==2.15.0+cpu`, `demucs==4.1.0`, `numpy==2.5.4`,
  Rubber Band 4.0.0 (system), FFmpeg 9.0.2, PyInstaller 6.22.3
- `htdemucs` weights from `adefossez/HTDemucs`, revision
  `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003` (81 MB cache)

## Method

- `measure.py <label> -- <cmd>` runs one subprocess and records wall time,
  child CPU time, and peak child RSS via `resource.getrusage(RUSAGE_CHILDREN)`.
  It is the only measurement active at the time.
- Fixtures are synthetic but deterministic: 4-minute stereo 44.1 kHz float32
  WAV (sines + pink noise + limiter); long tracks loop that master. Demucs
  compute is content-independent; Rubber Band is content-sensitive, so the
  exact research-03 fixture is also run as a cross-check.
- Demucs: CLI `--float32 -n htdemucs -d cpu` (defaults: shifts=1, overlap=0.25,
  segment 7.8 s, jobs=0). The CLI delegates to `demucs.api.Separator`, the same
  class the worker will use. `OMP_NUM_THREADS=MKL_NUM_THREADS=4`.
- Separation wall time includes model load (~3.3 s warm) and float32 stem writes.
- Rubber Band: `-3 -F -p ±12 --ignore-clipping`, canonical float32 input.
- Peak disk is measured with `du -sb` on the output directory; Demucs writes no
  temporary audio besides the stems.
- Frozen startup is a stub that imports `torch` + `demucs.api` and prints a
  ready marker; it is not the NiceGUI-to-HTTP-200 measurement from research 04.

## Scripts

- `setup.sh` — create the venv, install the pinned stack, warm weights, build fixtures
- `fixtures.sh` — generate the audio fixtures
- `measure.py` — one-subprocess timing/RSS wrapper
- `bench-quick.sh` — decode, preview, export, pitch, 4-minute Separation
- `bench-verify.sh` — repeat 4-minute Separation + research-03 pitch fixture
- `bench-long1.sh` / `bench-long2.sh` — 10/30/60-minute Separation scaling
  (`bench-long2.sh` needs Linux + a systemd user session for the 18 GiB cap)
- `bench-pyinstaller.sh` — frozen onedir size, trim saving, startup

Run the scripts from a working copy outside the repo (they create `audio/`,
`out/`, `.venv/`, and `dist/`, all ignored here). Raw results are in
`results.jsonl`; `freq.log` holds sustained-clock samples from the repeat run
and `disk.txt` the output sizes. See `REPORT.md` for the analysis.
