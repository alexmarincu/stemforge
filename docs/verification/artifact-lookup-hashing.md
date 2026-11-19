# Post-separation freezes and "connection lost"

Fixed on `fix/artifact-lookup-hashing`, based on `integration/v1` `504fd45`
(2026-11-19). After a Separation, the window became almost unresponsive, NiceGUI
reported **Connection lost**, and the Vocals/Instrumental play and export
actions appeared unusable.

## Root cause

`ArtifactStore` re-hashed every file of an artifact set on **every** manifest
lookup: `_read_manifest` -> `_parse_manifest_file` -> `_sha256_file`. A
Separation set is five WAVs plus five MP3 previews, so one lookup read the whole
set from disk.

The UI performs many lookups per detail refresh:

- `WorkspaceService.preview_targets` calls `iter_renders` and `find_separation`;
- the Separation card calls `separation_sets`, `export_targets`, and one
  `separation_preview_target` per row;
- `page.poll` calls `preview_targets` every 0.4 s while audio is playing;
- the startup sweep re-hashed the whole cache before the window opened.

Measured against a real cached Separation (a 2:49 YouTube track, 368 MB of
stems):

| Call | Before | After |
|---|---:|---:|
| `separation_sets` (one `find_separation`) | 804 ms | 2 ms |
| `preview_targets` | 1964 ms | 8 ms |
| `export_targets` | 2324 ms | 2 ms |
| `separation_preview_target` | 1804 ms | 1–2 ms |
| Whole detail-page path | **17.2 s** | **15 ms** |
| Export Vocals / Instrumental | 13.6 s / 14.4 s | 4.7 s / 4.5 s |

The 17 s synchronous block runs on the NiceGUI event loop, so the websocket
heartbeat starves, the client drops, and the reconnect re-renders the same
17 s page. The stem preview requests and the native export dialog never get a
stable window; both work once the loop is free.

## Fix

Manifest lookup is metadata-only again:

- structure, identity, file existence, and the recorded size are always checked;
- recorded SHA-256 values are checked only by `verify_hashes=True`, or when a
  set is committed.

Deep verification remains at the seams where it protects the audio:

- `run_separation_job` verifies the Source before starting the worker;
- `run_pitch_job` verifies its Source or Stems before rendering;
- `run_export_job` verifies the target set before encoding;
- `run_youtube_import` verifies a cache hit before opening it, so a native or
  derived file that no longer matches its record is repaired or re-downloaded;
- `commit_source` and `commit_render` verify an existing set before reusing it,
  so a damaged cache entry is replaced instead of returned;
- `ArtifactStore.find_source`, `find_separation`, and `find_render` expose the
  flag for recovery and tests.

Routine UI lookups never read audio bytes. The startup sweep validates manifest
structure, file existence, and recorded sizes; hashes are not re-read there, so
startup stays proportional to the manifest count rather than the cache size.

## Regression tests

- `tests/unit/test_artifact_store.py::test_a_routine_lookup_never_reads_audio_bytes`
  fails if a lookup calls `_sha256_file`.
- `test_a_hash_mismatch_only_fails_deep_verification` keeps the integrity
  guarantee on both sides of the new seam, and
  `test_hash_verification_covers_separation_sets` /
  `test_hash_verification_covers_render_sets` cover the flag's pass-through.
- `test_a_truncated_file_still_makes_the_set_invalid` shows a cheap size check
  still rejects a truncated file.
- `tests/ui/test_separation_flow.py::test_rendering_committed_stems_never_rehashes_audio`
  builds the real card for a committed Separation with `_sha256_file` armed to
  fail, and asserts Vocals/Instrumental play and export are enabled.

## Verification

| Check | Result |
|---|---|
| Detail-page service calls, real cache | 17.2 s -> 15 ms |
| `pytest -m "unit or ui"` | 1005 passed, 2 skipped |
| `pytest -m integration` | 133 passed, 7 skipped |
| `ruff format --check` / `ruff check` | clean |
| `mypy src/stemforge` | clean |
| `lint-imports` | 2 contracts kept |
| `generate_notices.py --check` | 201 files match |
| `generate_notices.py --check-release` | ok |

Vocals and Instrumental exported through the real cache end to end: both
`MP3 320` Jobs succeeded and produced playable 6.7 MB files.

The already-running app has the old code in memory and must be restarted.
