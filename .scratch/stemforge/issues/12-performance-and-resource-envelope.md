# 12 — Performance and resource envelope

**Type:** task
**Status:** resolved
**Blocked by:** 02

## Question

Establish the v1 performance envelope on realistic hardware:

- Benchmark `htdemucs` Separation on 3–4 minute tracks on CPU (and GPU if available): wall time, RAM, and peak disk. Research 02 measured RTF 1.54 and 1.81 GB RSS on a 4-core Haswell; verify on target hardware and formalize the envelope.
- Benchmark Pitch shift, decode, Preview transcode, and Export. Research 03 measured R3 + formant at ~124 s wall / ~104 s CPU per 4-minute stem, draft ~34 s, and four stems ~104 s CPU each on four cores; verify on target hardware.
- Test long tracks (10/30/60 minutes) for time and memory scaling; define the maximum supported duration and chunking.
- Measure bundle size without weights (downloaded on demand), with ffmpeg/Deno; research 04 estimates ~1.0 GB onedir Linux / ~830 MB Windows, and trimming `torch/test` + `torch/bin` saves 162 MB.
- Measure frozen startup: research 04 measured 2.2 s to HTTP 200 with torch eager, and `import torch` at 3.8 s warm / 12.1 s cold; verify on Windows.
- Define minimum/recommended hardware and the estimates shown to users.

The answer records measured numbers, methodology, and the supported envelope. This is a task because it requires running the engine; it unblocks packaging and roadmap decisions.

## Answer

**Envelope: Separation RTF 0.57–0.72 measured (plan 1.5), ~278 MiB/min + ~355 MiB RSS, split into 8-minute blocks with a 1 s crossfade (~2.7 GiB peak); 30-minute local cap (YouTube stays 15); 8 GB RAM / 20 GB free disk minimum.**

Measured on a 4-core/8-thread i7-4800MQ (Haswell, ~2.9 GHz sustained), 23 GB RAM, Linux 7.3.4, bench files on SSD, CPU-only `torch==2.15.0`, `demucs==4.1.0`, Rubber Band 4.0.0, FFmpeg 9.0.2. Method, rig, scripts, and raw rows: [bench/REPORT.md](../bench/REPORT.md), [bench/results.jsonl](../bench/results.jsonl). Fixtures are deterministic synthetic audio; Demucs compute is content-independent, and the exact research-03 pitch fixture was rerun to separate content effects from host effects.

### Measured

| Stage | Result |
|---|---|
| Separation 4 min | 173.4 s wall, RTF 0.72, 1.93 GiB peak RSS, 339 MB stems (repeat: 173.5 s, 1.83 GiB) |
| Separation 10 min | 339.0 s, RTF 0.57, 2.86 GiB, 677 MB stems |
| Separation 30 min | 1,120.7 s, RTF 0.62, 8.49 GiB, 2.37 GB stems |
| Separation 60 min | 2,444.8 s, RTF 0.68, 17.18 GiB, 5.08 GB stems (under an 18 GiB cgroup cap) |
| Pitch 4 min, +12 st | 47.6 s, 16 MB RSS |
| Pitch 4 min, −12 st | 23.3 s, 16 MB RSS |
| Research-03 pitch fixture, +12 st | 42.6 s here vs 108.8 s published |
| Decode 4 min MP3 → f32 WAV | 0.45 s, 62 MB |
| Preview 4 min → MP3 192 | 2.34 s, 64 MB, 5.8 MB out |
| Export 4 min WAV24 / FLAC24 / MP3 320 | 0.09 s / 0.39 s / 2.59 s |
| Frozen onedir stub | 916.4 MB / 2,940 files; 199.4 MB tar.zst; 1.89 s imports-ready |
| `torch/test` + `torch/bin` trim | 168 MB saved |

Research 02's 370 s and research 03's 108.8 s are ~2.1–2.5× slower than the same work here; the research host was slower, probably power-limited or contended. Both ends are real, so **use RTF 1.5 for user-facing estimates** and treat 0.57–0.72 as what a healthy 4-core Haswell at full turbo does. Peak RSS is 1.8 GiB at 4 min, 8.5 GiB at 30, and 17.2 GiB at 60; above 10 minutes it fits **~278 MiB/min + ~355 MiB**.

### Decisions

1. **Estimates shown to users:** Separation `ceil(1.5 × duration_min)`; Pitch `ceil(0.5 × duration_min)` per stem (all four stems ≈ 2× duration); decode/preview/export show a spinner, not an ETA. Progress is per block/segment, not per stem.
2. **Chunking:** separate in 8-minute blocks with a 1-second linear crossfade; peak stays near 2.7 GiB at any length. `htdemucs` only sees 7.8-second segments, so block mode loses no model context; use `shifts=0` so the random equivariant shift does not change timbre at boundaries. ADR [0008](../../../docs/adr/0008-separation-blocks-and-30-minute-cap.md).
3. **Maximum duration:** 30 minutes for local imports, rejected after probe with `import.too_long` (mirrors `import.youtube_too_long`); YouTube stays at 15. 30 minutes is ~19 min measured / ~46 min conservative Separation and **7.0 GiB** for the full source + Separation + stem-Pitch workflow (7.6 GiB with an original Pitch render too).
4. **Cache:** keep the 8 GiB default from [07](07-workspace-storage-and-lifecycle.md). One 30-minute full workflow is 7.0–7.6 GiB, so it fits with 0.4–1.0 GiB to spare; a second long track evicts older sets by LRU as designed. No cap change.
5. **Hardware:** minimum 4-core x86_64 with AVX2, 8 GB RAM, 20 GB free disk, SSD strongly recommended (Separation writes ~113 MB/min); recommended 8+ cores, 16 GB RAM, 30 GB free. Set torch/OMP threads explicitly to physical cores.
6. **Bundle:** 916 MB / 2,940 files and 199 MB tar.zst confirm research 04. Add FFmpeg/ffprobe (~160–200 MB), Deno (96 MB), and Rubber Band/yt-dlp (~5 MB); trim `torch/test` + `torch/bin` (−168 MB) to land at **~1.0–1.05 GB onedir Linux**, ~300 MB compressed (estimated). The 84 MB weights are excluded; 13/10 still decide whether they ship in the installer or download on first run.
7. **GPU:** not in v1 and not measurable here — the host GPU has 973 MiB VRAM and the stack is CPU torch. This leaves ticket 02's CPU-only recommendation intact.

### Not measured

Windows startup/memory, the full NiceGUI-to-HTTP-200 path, and GPU. Research 04's 2.2 s HTTP-200 and ~830 MB Windows estimate remain the planning numbers; [16](16-windows-performance-verification.md) captures Windows figures once 10 produces a bundle. Frozen startup here is imports-ready, not the app.

**Impact:** 10 gets the post-trim size budget and the startup target; 11 can gate the frozen 1-second separation smoke test at <60 s and <2 GB RSS; 15 gets the hardware baseline, 30-minute cap, and user-facing estimate formulas; 16 verifies the Windows side.
