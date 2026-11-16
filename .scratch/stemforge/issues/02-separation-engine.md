# 02 — Separation engine and invocation

**Type:** research
**Status:** resolved
**Blocked by:** none

## Question

Which local stem-separation engine ships in StemForge v0.1.0, and how is it invoked from the app?

Resolve for Demucs 4.1 and `htdemucs`:

- Maintenance status; Python 3.12/3.13/3.14 support; PyTorch wheel availability on Linux and Windows CPU.
- Weight size, license, download URL, and cache path; Python API vs CLI; `--two-stems=vocals`; stem names; output format.
- Progress reporting, CPU time and RAM for 3–4 minute tracks, and chunking options.
- PyInstaller/Nuitka bundling of torch: hidden imports, DLLs, and bundle-size estimate.
- CPU-only vs CUDA torch distribution for v0.1.0.

Compare demucs.cpp, python-audio-separator (UVR/MDX), Spleeter, and MDX-Net on maturity, Python compatibility, model size, CPU speed, quality, and packaging. Recommend an exact engine and a subprocess-worker invocation with a progress protocol. Research report: `../research/02-separation-engine.md`.

## Answer

**Recommendation: `demucs==4.1.0` + `htdemucs`, CPU-only torch, CPython 3.13, behind a subprocess worker with a JSONL stdout protocol.**

Measured on a 4-core Haswell, 23 GB RAM, no GPU; full report: `../research/02-separation-engine.md`.

Key findings:

- **Quality:** best locally runnable option (~9.0 dB SDR overall), 84 MB single safetensors weight (`adefossez/HTDemucs`, pin revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`). `htdemucs_ft` is 336 MB and 4x slower for marginal gain; not v0.1.0.
- **Runtime:** RTF 1.54; a 4-minute track takes 6 min 10 s on the research box and peaks at 1.81 GB RSS. Plan for ~2–4 minutes on a modern 8–16 core desktop and a 4 GB free-RAM precondition.
- **Python/torch:** 3.10–3.14 work; 3.13 recommended for wheel coverage; 3.15 blocked by sphn. `torch==2.15.0+cpu` (209.5 MB wheel Linux / 126.6 MB Windows; 760/484 MB installed). CPU-only in v0.1.0; a CUDA build is a separate torch environment (~2.5 GB installed) and cannot be switched on later.
- **Release blocker:** demucs 4.1.0 does not declare numpy on Linux/Windows, and `from demucs.api import Separator` fails with `ModuleNotFoundError: numpy`. Pin `numpy==2.5.4` explicitly and smoke-test the import in CI.
- **Weights licence:** code is MIT, but the author explicitly says the weights' licence is unclear (MUSDB research data). Bundling them in a GPL-3.0 installer is a legal grey area; `htdemucs_6s` is likely non-commercial. Do not bundle until ticket 13 resolves it; on-demand download with notice is the safe fallback.
- **Packaging:** PyInstaller onedir of a minimal worker = 881 MB on disk / 197 MB compressed, 3 min build, no runtime penalty in the test. Never onefile. Hidden imports `demucs.htdemucs`, `demucs.hdemucs`, `demucs.repo`, `demucs.hf`; `--collect-data demucs`.
- **API:** `demucs.api.Separator` exposes a progress callback with `segment_offset`, `audio_length`, and `state`; derive fraction from `state=="end"` events. `--two-stems` is not in the API; compute `no_vocals` from the four stems. Output is 44.1 kHz stereo; use float32 to match canonical WAV.
- **Bundle estimate:** ~230–280 MB compressed with weights on demand; ~310–360 MB bundled. Optional CUDA pack adds 1.2–1.5 GB compressed.

Open items: weights licence (13), model-weight acquisition (10), Python 3.13 confirmation (06), and GPU scope (10).
