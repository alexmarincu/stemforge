# StemForge v1 performance and resource envelope

Benchmark report for ticket 12. Rig, method, and scripts: [README.md](README.md).
Raw rows: [results.jsonl](results.jsonl).

## Headline

| Question | Answer |
|---|---|
| Separation speed | **RTF 0.57–0.72** measured (0.68 at 60 min); **RTF 1.54** on the slower research-02 run. Plan **1.5× track duration** for user estimates. |
| Separation memory | **1.8 GiB** at 4 min, **2.9 GiB** at 10, **8.5 GiB** at 30, **17.2 GiB** at 60. Fits **~278 MiB/min + ~355 MiB**; Demucs buffers the full track and all stems. |
| Pitch speed | **0.10–0.20× duration** per stem measured here; research 0.52×. Plan **0.5×** per stem, so a 4-stem render can cost ~2× track duration. |
| Other stages | Decode, preview, and all three exports are **seconds** for a 4-minute track; never the bottleneck. |
| Bundle | **916 MB / 2,940 files** frozen; **199 MB** tar.zst; `torch/test` + `torch/bin` trim saves **168 MB**. |
| Frozen startup | **1.9 s** to torch+Demucs imports-ready (warm disk, Linux). Research 04 measured 2.2 s to HTTP 200 for the full app. |
| Max duration | **30 minutes local** (YouTube 15). Chunk Separation into **8-minute blocks** to hold peak near 2.7 GiB; one full long-track workflow fits the existing 8 GiB cache. |

## Rig and methodology

See [README.md](README.md). In short: i7-4800MQ (4C/8T Haswell, ~2.9 GHz sustained),
23 GB RAM, SSD bench volume, Linux, Python 3.13, torch 2.15 CPU, Demucs 4.1.0,
Rubber Band 4.0.0, FFmpeg 9.0.2. Fixtures are deterministic synthetic audio;
Demucs compute is content-independent, and the exact research-03 pitch fixture
was run to check content sensitivity.

## Media pipeline (4-minute track)

| Stage | Wall | CPU | Peak RSS | Output |
|---|---:|---:|---:|---:|
| Decode MP3 320 → canonical f32 WAV | 0.45 s | 0.63 s | 62 MB | 84.7 MB |
| Preview WAV → MP3 192 | 2.34 s | 2.55 s | 64 MB | 5.8 MB |
| Export WAV 24-bit | 0.09 s | 0.20 s | 61 MB | 63.5 MB |
| Export FLAC 24-bit | 0.39 s | 0.51 s | 66 MB | 21.6 MB |
| Export MP3 320 | 2.59 s | 2.85 s | 64 MB | 9.6 MB |

Import decode, preview transcode, and export are each a few seconds on this
hardware and scale linearly. They need no progress UI beyond a spinner; the
4-minute numbers are a safe upper bound for the 15-minute YouTube ceiling.

## Pitch shift (4-minute stereo)

| Run | Wall | CPU | Peak RSS |
|---|---:|---:|---:|
| Rubber Band R3 + formant, +12 st | 47.6 s | 47.4 s | 16 MB |
| Rubber Band R3 + formant, −12 st | 23.3 s | 23.2 s | 16 MB |
| Research-03 fixture (210 s), +12 st | 42.6 s | 42.3 s | 16 MB |
| Research-03 published result (210 s), +12 st | 108.8 s | 91.0 s | 16 MB |

Pitch is single-threaded and memory-trivial. Upward shifts cost about twice
downward shifts on this fixture. The exact same fixture runs 2.55× faster here
than in research 03, so the difference is the host state, not the audio.

## Separation (`htdemucs`, CPU)

| Duration | Wall | RTF | CPU | Peak RSS (MiB) | Stems on disk |
|---|---:|---:|---:|---:|---:|
| 4 min | 173.4 s | 0.72 | 614 s | 1,934 | 339 MB |
| 4 min (repeat) | 173.5 s | 0.72 | 625 s | 1,825 | 339 MB |
| 10 min | 339.0 s | 0.57 | 1,231 s | 2,864 | 677 MB |
| 30 min | 1,120.7 s | 0.62 | 4,113 s | 8,495 | 2.37 GB |
| 60 min | 2,444.8 s | 0.68 | 8,881 s | 17,178 | 5.08 GB |

Time is close to linear above the 4-minute point (least-squares slope
~42 s/min, RTF 0.57–0.72); the 4-minute RTF is worse because model load is
amortized over less audio, and the 60-minute run is slower because a 40-minute
job reaches a lower sustained clock. CPU/wall stays at ~3.5–3.6 cores, so the
extra time is not thread starvation.

Peak RSS is the binding constraint. A least-squares fit over all four points
gives **~278 MiB per minute plus ~355 MiB of fixed torch/Demucs overhead**:
1.8 GiB at 4 min, 2.9 GiB at 10, 8.5 GiB at 30, and **17.2 GiB at 60** (the fit
predicts 17.1 GiB). Demucs buffers the full-track input and all four float32
stems in RAM before writing; the model's 7.8-second internal segment size does
not bound this. The 60-minute run completed under an 18 GiB cgroup cap with
about 1.2 GiB to spare.

The same 4-minute separation took 370 s in research 02 and 173 s here. The
research-03 Rubber Band fixture reproduces a 2.1–2.5× gap on this host,
so the difference is sustained CPU clocks (research box likely power-limited
or contended), not the fixture. Treat **RTF 1.5 as the conservative planning
number** and 0.6–0.7 as what a healthy 4-core Haswell does at full turbo.

## Frozen bundle

| Measurement | Result |
|---|---:|
| PyInstaller build (`--onedir`, torch + Demucs stub) | 134.9 s |
| Bundle on disk | 916.4 MB / 2,940 files |
| `tar + zstd -19` | 199.4 MB |
| `torch/test` trim | 113.9 MB |
| `torch/bin` trim | 54.5 MB |
| Frozen startup (imports-ready, warm disk, 3 runs) | 1.89 s / 1.89 s / 1.89 s, 239 MB RSS |

The 916 MB stub is within 4% of research 04's 881 MB measurement; the
tar.zst (199 MB) matches its 197 MB. Trimming `torch/test` + `torch/bin` saves
**168 MB**, slightly more than the 162 MB estimate.

Vendor payloads are not in the stub: FFmpeg + ffprobe are ~160–200 MB
(research 04 measured 79.8 + 79.7 MB for a minimal static build), Deno is
**96 MB** extracted (42 MB zip), and Rubber Band + yt-dlp/EJS are ~5 MB. So the
shipped Linux onedir is **~1.17–1.22 GB before trimming and ~1.00–1.05 GB
after** the torch trim. Research 04's "~1.0 GB Linux" figure only holds after
that trim.

Startup here is imports-ready, not the full NiceGUI path. Research 04 measured
2.2 s to HTTP 200 for the full app; this host's faster CPU suggests that number
is achievable, but it is not re-measured because the app does not exist yet.

## Envelope and recommendations

**Planning numbers.** Use **RTF 1.5** for user-facing Separation estimates
(`ceil(1.5 × track minutes)`) even though this host measures 0.57–0.72; the
slower research-02 run is the conservative end. Use **0.5× duration per stem**
for Pitch (a 4-stem render ≈ 2× track duration); measured here is 0.10–0.20×
per stem. Decode, Preview, and Export are seconds and need only a spinner.

**Memory.** Demucs' full-track buffering costs **~278 MiB/min plus ~355 MiB
fixed**, so full-track Separation is ~1.8 GiB at 4 min, ~2.9 GiB at 10, ~8.5 GiB
at 30, and ~17.2 GiB at 60. To keep the 4 GB-free-RAM precondition from research
02 for every supported length, the worker must separate in **8-minute blocks
with a 1-second overlap crossfade**: peak ≈ 2.7 GiB, independent of track
length. Because `htdemucs` only ever sees 7.8-second segments, blocks do not
reduce model context; only the stitch needs care. Use `shifts=0` (or one fixed
seed for every block) in block mode so the random equivariant shift does not
change timbre at block boundaries.

**Supported duration.** Cap local imports at **30 minutes** (YouTube stays at
15). At 30 minutes: ~19 min Separation measured here, ~46 min on the
conservative RTF; **7.0 GiB** for the full source + Separation + stem-Pitch
workflow (7.6 GiB if an original Pitch render is also kept); ~8.5 GiB peak RSS
if unchunked. 60 minutes works (40.8 min measured, 17.2 GiB) but doubles the
cache to 13.9–15.2 GiB, which is not worth it for v1.

**Cache consequence.** One full 30-minute workflow is 7.0–7.6 GiB, so ticket
07's 8 GiB default fits a single long track with 0.4–1.0 GiB to spare; a second
long track evicts older sets by LRU, as designed. No cap change is needed for
v1.

**Hardware.** Minimum: 4-core x86_64 with AVX2, **8 GB RAM**, 20 GB free disk,
SSD strongly recommended (Separation writes ~113 MB/min). Recommended: 8+
cores, 16 GB RAM, 30 GB free. Keep torch at physical-core threads (4 on this
host); `OMP_NUM_THREADS`/`torch.set_num_threads()` must be set explicitly.

**Bundle.** 916 MB / 2,940 files frozen, 199 MB tar.zst, 1.9 s to
torch+Demucs imports-ready. Trimming `torch/test` + `torch/bin` saves 168 MB.
These confirm research 04's 875–881 MB estimate. The full NiceGUI-to-HTTP-200
startup and all Windows numbers remain unmeasured here.

## Limitations

- Synthetic fixtures; Rubber Band is content-sensitive, so the pitch range is
  reported against both fixtures.
- Linux only. Windows startup, Defender scanning, and Defender-induced cold
  starts are unmeasured; research 04 supplies estimates only.
- No GPU: the available Quadro K610M has 973 MiB VRAM and v1 ships CPU torch.
- Frozen startup here is imports-ready, not the full NiceGUI HTTP-200 path.
- The 60-minute run is the only data point beyond the v1 30-minute cap; it
  exists to validate the linear memory model, not to promise support.
