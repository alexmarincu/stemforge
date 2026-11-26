---
status: accepted
---

# Save original is not an Export

ADR 0001 keeps the imported file untouched so a later "Save original" can return it at its original sample rate and channel count, but Export never reads it: the Original target re-encodes `canonical.wav` to WAV 24, FLAC 24, or MP3 320 at 44.1 kHz stereo, and every other target encodes a derived Artifact. Saving the native file promises the bytes the user imported, unchanged; calling it a fourth Export format would make the Export contract lie about what it writes.

**Decision.** Save original copies a local Source's Native file byte for byte to a user-chosen destination. It is its own `SAVE_ORIGINAL` Job with its own result view and history label, not an `EXPORT` Job. It never encodes, rewrites tags, applies gain, resamples, or downmixes, and it verifies the recorded SHA-256 while copying. It uses the same `.partial`/fsync/rename, collision, cancellation, and free-space guarantees as Export. It is offered only for a local Source, because a YouTube Source's native file is the downloaded media, not a file the user supplied.

**Considered options:** a passthrough flag or a fourth `ExportFormat` member (rejected: the enum names the three transcode formats, `ExportedFile` carries a format, gain, and duration, and the sheet remembers one format per Track); treating the Native file as an Artifact (rejected: an Artifact is derived, and Export's contract is that it never overwrites the Track's original audio); offering the downloaded YouTube container as "original" (rejected: there is no user file to restore); a plain copy without the hash or atomic write (rejected: it cannot prove the bytes are the import, and a cancel or crash would leave a truncated file under the final name).

**Consequences:** a new `JobKind`, result view, notice, and the `export.original_unavailable` error code (`missing`, `changed`, `not_local`); an "Imported file" block in the single-mode Export sheet for the Original target; batch export excludes it; and the v0.1.0 assertions that there is no Save original are removed. The implementation lands after the batch-export change because the block lives in the single-mode branch of the sheet.
