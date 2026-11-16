---
status: accepted
---

# Canonical audio is stereo 44.1 kHz float32

Every Source is decoded once at import into stereo 44.1 kHz 32-bit float WAV, and every processing stage reads that copy. One rate and one channel shape keep Demucs (native 44.1 kHz), Rubber Band (tuned for 44.1/48 kHz), validation, and cache keys on a single path; the cost is that a 48 kHz source exported untouched comes out at 44.1 kHz, and multichannel sources are downmixed to stereo. Mono is duplicated to dual-mono. The native Source file is retained untouched, so nothing is destroyed.

**Consequences:** pass-through exports inherit the 44.1 kHz/stereo normalization; a post-v0.1.0 "Save original" can copy the native file to preserve its original rate and channels.
