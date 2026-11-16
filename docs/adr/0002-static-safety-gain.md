---
status: accepted
---

# Gain is never normalized; a shared static gain protects full scale

Pitch renders use `--ignore-clipping` so the relative level of the stems is preserved, but Rubber Band can overshoot full scale, and summing stems into the Instrumental can do the same. Rather than normalize each artifact (which would break the mix) or run a limiter (which would colour transients), each artifact set (import, separation, pitch render) records one static gain only when its loudest sample exceeds 0 dBFS, bringing the peak to −1 dBFS. The gain is stored in the manifest and applied when previews and exports are encoded; master float32 WAVs keep their headroom and are never rewritten.

**Consequences:** A/B may have a level difference when the gain engages unless the player volume-matches; loudness normalization, dynamic limiting, and per-stem normalization are deliberately absent from v0.1.0.
