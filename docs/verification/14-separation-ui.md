# Ticket 14 — Separation UI manual verification

**Date:** 2026-11-17
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native mode, real Demucs downloads, audible
output, and the Windows matrix cannot be exercised here; those items are
deferred to real Linux and Windows hosts.

## Verified headlessly

- `tests/ui/test_separation_flow.py` (14 passed, NiceGUI user simulation):
  - the model gate renders `Separation model not installed`, `84 MB`, the
    pinned revision (`cbc8a9b1…`), the licence notice, the download consent
    line, `Download model`, and the code `separation.model_missing`; the rest
    of the app (Add audio, Pitch) stays usable;
  - download progress appears in the Job bar (`Downloading model · Download`,
    a non-zero percent) and in the card; Cancel leaves the gate and installs
    nothing;
  - a persistent offline transport failure shows
    `separation.model_download_failed` with `Retry download`; Retry installs
    the model and returns to the not-separated action;
  - a missing model renders the gate instead of crashing and never starts a
    Job;
  - after a model disappears between renders, pressing Separate shows the
    gate, latches the request, and a successful install starts exactly **one**
    Separation; further polls do not chain another one;
  - a checksum failure shows the gate, latches one resubmit, and a reinstall
    starts exactly one Separation;
  - a running Separation reports progress in the card and Job bar, shows the
    worker/safe-to-cancel note, and displays the 30-second stall warning with
    `Keep waiting` and `Cancel` when the injected clock advances; Cancel
    returns to the not-separated state with no inline failure and empty
    staging;
  - Vocals and Instrumental are visible; Drums, Bass, and Other are hidden
    until `Show 3 more stems` is pressed; all five rows play through the one
    `<audio>` element;
  - every row has A/B against the original, and the transport A/B follows the
    stem that is playing; exactly one `<audio>` element exists throughout;
  - every row offers an export target; selecting one records the target and
    marks the row;
  - older Separation sets are labelled `based on an earlier separation` and
    stay selectable; the newest set is the default and plays first;
  - `separation.worker_crashed`, `separation.engine_error`, and
    `separation.output_invalid` render inline with Retry, which resubmits the
    same spec; `separation.protocol_mismatch` renders inline without Retry
    (ticket 14 §4.4/§8); a failed re-separation keeps the last successful
    Stems visible and playable.
- `tests/unit/test_separation_ui_service.py` (10 passed): set enumeration
  returns the committed set with ordered stem roles; preview lookup addresses
  each stem and the Instrumental and refuses unknown keys/roles; a
  re-separation replaces the set and its key; export targets cover Vocals,
  Instrumental, Drums, Bass, and Other; `separate` enforces the model gate
  before submitting, latches one pending resubmit, consumes the latch only for
  the pending Track, submits one Job when the model is installed, and never
  latches a job-level model failure twice.
- `tests/ui/test_preview.py` and `tests/ui/test_pitch_flow.py` still pass;
  playback/A-B stay enabled during Jobs, and the Separation card's model gate
  is the disabled second-Job action while an import runs.
- `uv run pytest -m "unit or ui"`: 638 passed, 129 deselected.
- `uv run pytest -m "integration and not slow"`: 127 passed, 640 deselected.
- `uv run python -m stemforge --self-test`: exit 0.
- `uv run python -m stemforge --smoke-test`: exit 0, HTTP 200 on `/`.
- `ruff format --check .`, `ruff check .`, `mypy src/stemforge`,
  `lint-imports`: green.

No UI or unit test spawns Demucs, torch, Rubber Band, FFmpeg, or a real
network connection: the separation adapter and media tool are fakes that write
stand-ins under `tmp_path`, and the model transport reads bytes from memory.

## Release-qualification items that need real devices

1. **Linux, GTK 3 + WebKitGTK 4.1, real Demucs weights:** with no model
   installed, open a track and confirm the inline gate (84 MB, revision,
   licence details). Press `Download model` and watch the Job bar progress;
   cancel once and confirm no partial weights remain. Download again over a
   disconnected network, confirm `separation.model_download_failed` and
   `Retry download`, reconnect, and confirm the model installs without a
   restart.
2. **Linux first Separation:** with the model installed, press `Separate
   vocals` on a four-minute track. Confirm block progress, the worker note,
   Cancel, and the 30-second stall warning on a deliberately slow host. After
   success, confirm four Stems plus Instrumental, the primary/secondary split,
   per-row playback through one player, per-stem A/B, and export-target
   selection.
3. **Linux offline startup:** disconnect the network before launch, load and
   preview a cached track, and confirm the gate appears only when Separation is
   invoked; the app remains usable. Press Download once and confirm the
   offline copy; do not retry automatically.
4. **Windows 10/11 + WebView2:** repeat the first-run download, cancel, offline
   retry, Separation progress/stall, A/B, and export-target selection. Record
   whether WebView2 ever double-sounds or fails to swap the single `<audio>`
   source during rapid stem A/B.
5. **Auto-resubmit:** trigger the pending path (delete the weights with the
   app open, press Separate, then Download) and confirm exactly one Separation
   Job starts after the install; pressing Download or waiting must not start a
   second one.
6. **Coded failures on a real host:** rename the worker/weights to trigger
   `separation.worker_crashed`, `separation.model_missing`, and
   `separation.protocol_mismatch`; confirm the inline copy and the presence or
   absence of Retry matches ticket 14 §4.4.

## Interpretation notes and deviations

- v1's artifact store keeps one Separation set per Source; a re-separation
  replaces it (ticket 05, ADR 0004/0012). `separation_sets(track)` therefore
  returns at most one set. The card still renders a set selector and labels
  older sets `based on an earlier separation`; that path is covered by the UI
  test with injected set views, while the service test pins the real
  single-set replacement. Older Pitch render sets built from an earlier
  separation remain labelled and selectable in the Pitch card (ticket 11).
- Ticket 17's `export_targets` merged before this branch reported. The card
  consumes `WorkspaceService.export_targets(track)` and filters
  `scope == "separation"`; the selected row's stable key is what a later format
  sheet / Export Job should pass to `submit_export`. No stem path is derived in
  the UI.
- Retry is shown when the failed Job's snapshot is retryable, plus the
  Separation codes ticket 14 §4.4 marks retryable (`output_invalid`,
  `media.validation_failed`, storage). `separation.protocol_mismatch` and
  `separation.model_missing` do not show Retry; the gate offers the download
  instead.
- `Licence details` renders the `model_licence_copy` notice inline. The About
  deep-link named by ticket 08 §10 belongs to ticket 22 and is not added here.
