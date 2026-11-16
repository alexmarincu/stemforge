---
status: accepted
---

# Recovery discards unfinished work and never deletes finished work

A Job writes only into `staging/<job_id>/` and promotes its set with one atomic directory rename after the manifest is written; a failure, cancellation, or crash therefore leaves the cache's committed artifacts untouched. Startup recovery sweeps staging, tmp, and partial files, and reports that it did so, but never resumes a Job and never treats zero live references as garbage. Disk-full and cache-full failures also delete nothing: they report the needed and free space and offer Remove or Clear cached audio, so the automatic deletions remain exactly the three the cache ADR names (Remove, LRU under the cap, Clear cache).

**Considered options:** resume interrupted Jobs from a checkpoint journal (rejected: it needs per-stage state in every pipeline, and a resumed Demucs run still costs minutes); auto-evict non-live cache entries on volume-full (rejected: it silently deletes work the user did not ask to lose, breaking the reuse promise the cache exists for); prompt a modal before every failure (rejected: it turns recovery into a decision the user cannot evaluate).

**Consequences:** an interrupted Separation or Render is simply re-run; the startup banner is the only trace of a crash; the cache can fill the disk until the user acts; the artifact store is the only checkpoint, and the in-memory Job history is gone on exit.
