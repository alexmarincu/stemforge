---
status: accepted
---

# Separation runs in bounded 8-minute blocks; local tracks are capped at 30 minutes

Demucs buffers a track's full input and all four float32 stems in RAM before writing. Benchmarks on a 4-core Haswell put that at **~278 MiB per minute plus ~355 MiB** of fixed torch/Demucs overhead: 1.8 GiB at 4 minutes, 8.5 GiB at 30, and 17.2 GiB at 60. A full-track 30-minute Separation therefore misses the 4 GB-free-RAM precondition that research 02 set for the app, and 8 GB machines fail well before that.

So the Separation worker will process **8-minute blocks with a 1-second linear crossfade**, holding peak RSS near **2.7 GiB regardless of track length**. `htdemucs` only ever sees 7.8-second internal segments, so app-level blocks do not reduce model context; the only new edge is the stitch. Block mode uses `shifts=0` so Demucs' random equivariant shift cannot change timbre between blocks. Progress is reported per block and per internal segment, not per stem.

v0.1.0 accepts **local tracks up to 30 minutes** (YouTube stays at 15) and rejects longer files after probe with a field error. The full 30-minute workflow — source, Separation, and a stem Pitch render — needs **7.0 GiB** of cache (7.6 GiB with an original Pitch render too), so the existing 8 GiB default fits one long track with 0.4–1.0 GiB to spare; additional long tracks evict older sets by LRU. Minimum hardware is a 4-core AVX2 x86_64, 8 GB RAM, and 20 GB free disk; 16 GB and an SSD are recommended.

**Consequences:** a track over 30 minutes is refused rather than slowly processed; 60-minute support would need a 14–15 GiB cache on top of block mode, so it is deferred; each block boundary costs a 1-second crossfade and one extra model pass over that second; a second 30-minute track evicts older cached sets; user-facing estimates stay conservative at 1.5× duration for Separation and 0.5× per stem for Pitch even though this host measures 0.57–0.72× and 0.10–0.20×.
