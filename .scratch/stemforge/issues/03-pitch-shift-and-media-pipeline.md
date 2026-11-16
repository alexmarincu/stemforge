# 03 — Pitch-shift engine and media pipeline

**Type:** research
**Status:** resolved
**Blocked by:** none

## Question

Which pitch-shift engine and media pipeline let StemForge shift ±12 semitones with tempo preserved, entirely locally, under GPL-3.0?

Resolve:

- Rubber Band Library (CLI/library, license, quality modes, formant/tempo preservation, Python bindings) vs `pedalboard.PitchShift` (verify Rubber Band use, GPL implications, bundling) vs librosa vs soundtouch vs ffmpeg's `rubberband` filter.
- CPU time for 3–4 minute tracks, progress strategy, memory, and semitone range.
- ffmpeg/ffprobe binary sources for Linux and Windows, GPL vs LGPL builds, and size; canonical intermediate format; preview transcode; export encoders (WAV/FLAC/MP3); metadata.
- yt-dlp 2026.10.26 Python API vs binary: audio-only format selection, ffmpeg postprocessing, progress hooks, caching, age/cookie limits, and filename sanitization.

Recommend an exact stack with version pins. Research report: `../research/03-pitch-shift-and-media-pipeline.md`.

## Answer

**Recommendation: Rubber Band 4.0.0 CLI in R3/offline/formant mode; FFmpeg/ffprobe 9.0.2 GPLv3 static binaries; yt-dlp Python API with bundled Deno.**

Measured on a 4-core Haswell; full report: `../research/03-pitch-shift-and-media-pipeline.md`.

Key findings:

- **Pitch:** `rubberband -3 -F -p <semitones> --ignore-clipping in.wav out.wav`; draft via `--window-short`. R3 + formant took 108.8 s wall / 91.0 s CPU for 210 s stereo; a 4-minute stem extrapolates to ~124 s wall / ~104 s CPU; draft ~34 s. Memory ~16 MB. Tempo is preserved by construction; ±12 is well inside the practical range.
- **Rejected:** `pedalboard.PitchShift` uses Rubber Band but only R2 real-time, with no formant control or offline study pass; librosa used 3.9 GB RSS on a 3.5-minute stereo track; SoundTouch quality is lower; torchaudio is in maintenance mode with no quality gain.
- **Media:** canonical stereo 44.1 kHz 32-bit float WAV; preview MP3 192 kbps; export WAV 24-bit, FLAC 24-bit, MP3 320. A 4-minute canonical stem is 84.7 MB; four stems ~339 MB.
- **FFmpeg/ffprobe:** 9.0.2 GPLv3. Linux BtbN GPL archive 145.6 MB; Windows gyan essentials 34 MB (includes librubberband). Static binaries invoked as subprocesses; pin, mirror, and record SHA256. LGPL builds exclude librubberband.
- **yt-dlp:** Python API (`yt-dlp[default]`) plus bundled Deno, pinned `yt-dlp==2026.10.26`, `yt-dlp-ejs==0.8.0`, `deno==2.9.8`. Use `format: bestaudio/best`, `noplaylist: True`, keep the native file, decode to canonical WAV, and cache by video ID. YouTube is best-effort: age-restricted content needs opt-in cookies, PO tokens cannot be generated, and the ToS restricts downloading.
- **Licensing:** all recommended components are GPL-3.0-compatible. Rubber Band is GPL-2.0-or-later (no commercial licence needed); FFmpeg GPLv3 requires a source offer; libsndfile is LGPL, so keep `sndfile.dll` dynamic. See ticket 13.
- **Pipeline:** separate first, then pitch per stem; cache original stems. Research recommends 44.1 kHz canonical audio, formant on by default with a toggle, draft mode labelled and never used for export, and `--ignore-clipping` with a shared gain/limiter policy at export.
- **Bundle:** FFmpeg/Rubber Band/Deno add roughly 35–190 MB depending on platform and build, on top of the ~197 MB Demucs runtime.

Open items: canonical sample rate (44.1 vs preserve 48), clipping/gain policy, metadata and cover-art scope, preview bitrate (192 vs 256), yt-dlp update/PO-token posture, and Windows runtime verification.
