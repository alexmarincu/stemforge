---
status: accepted
---

# Demucs weights are downloaded, never bundled

The `htdemucs` weights have no granted licence. The Demucs README says the code is MIT but "the license of the weights is not clear", because MusDB contained data usable only for research, and that FAIR models are "likely" CC-NC-BY; the Hugging Face model card carries no licence tag. StemForge v1 therefore does not bundle, mirror, or redistribute the weights: the first Separation triggers an explicit download from `adefossez/HTDemucs` at revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`, verified by SHA-256 `d9fa1413…76dd` (84,025,440 bytes), with a licence notice and a user-pressed Download button. The installer stays weight-free and the app remains fully offline for local audio once the weights are cached.

**Considered options:** bundling `htdemucs` in every artifact (rejected — redistribution of questionably licensed, possibly non-commercial weights for 84 MB), hosting an optional "offline model pack" release asset (rejected — hosting it is still redistribution), and downloading silently on first use (rejected — no consent, and the licence warning would be buried).

**Consequences:** a first Separation needs a network connection and an 84 MB download, so the "self-contained installer" principle is deliberately relaxed for this one artifact; the artifact, revision, size, and SHA-256 are pinned so the download is reproducible; "Clear cache" may delete the weights and the user can re-download; and the About modal plus the model gate must state that the weights are not covered by StemForge's GPL-3.0 licence. If the author or Meta ever grants a licence, a fully offline bundle can be revisited as a fresh decision — not by quietly adding the file back.
