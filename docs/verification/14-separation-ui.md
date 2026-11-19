# Ticket 14 — Separation UI manual verification

**Date:** 2026-11-17
**Updated:** 2026-11-20 after aligning the UI with ticket 08.
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native mode, real Demucs downloads, audible
output, and the Windows matrix cannot be exercised here; those items are
deferred to real Linux and Windows hosts.

## Verified headlessly

- `tests/ui/test_separation_flow.py` (14 passed, NiceGUI user simulation):
  - the model gate renders `Separation model not installed`, `84 MB`, the
    pinned revision (`cbc8a9b1…`), the licence notice, the download consent
    line, `Download model`, and the code `separation.model_missing`; `Licence
    details` opens Settings & About; the rest of the app (Add audio, Pitch)
    stays usable;
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
  - the Separation card itself lists status, progress, and errors only. Vocals
    and Instrumental are Outputs rows; Drums, Bass, and Other are hidden until
    `Show 3 more stems` is pressed; all five rows play through the one
    `<audio>` element;
  - the rail reports `4 stems` with a green dot after success, `Separating…`
    with an amber dot while the Job runs, and `Separation failed` with a red
    dot after a failed run (ticket 08 §3);
  - the Outputs placeholder shows `Separate` before a run and
    `…appear when Separation finishes` without a button while it runs;
  - every row has an Export icon that opens the format sheet on that artifact;
    the transport Export follows the current listening target. There is no
    per-stem A/B: A/B is the transport's Original | latest-render shortcut;
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
- `uv run pytest -m "unit or ui"`: 1012 passed, 3 skipped.
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
   `Licence details`). Press `Download model` and watch the Job bar progress;
   cancel once and confirm no partial weights remain. Download again over a
   disconnected network, confirm `separation.model_download_failed` and
   `Retry download`, reconnect, and confirm the model installs without a
   restart.
2. **Linux first Separation:** with the model installed, press `Separate
   vocals` on a four-minute track. Confirm block progress, the worker note,
   Cancel, and the 30-second stall warning on a deliberately slow host. After
   success, confirm four Stems plus Instrumental, the primary/secondary split in
   Outputs, per-row playback through one player, and per-artifact Export.
3. **Linux offline startup:** disconnect the network before launch, load and
   preview a cached track, and confirm the gate appears only when Separation is
   invoked; the app remains usable. Press Download once and confirm the
   offline copy; do not retry automatically.
4. **Windows 10/11 + WebView2:** repeat the first-run download, cancel, offline
   retry, Separation progress/stall, row playback, and Export. Record whether
   WebView2 ever double-sounds or fails to swap the single `<audio>` source
   during rapid row switching.
5. **Auto-resubmit:** trigger the pending path (delete the weights with the
   app open, press Separate, then Download) and confirm exactly one Separation
   Job starts after the install; pressing Download or waiting must not start a
   second one.
6. **Coded failures on a real host:** rename the worker/weights to trigger
   `separation.worker_crashed`, `separation.model_missing`, and
   `separation.protocol_mismatch`; confirm the inline copy and the presence or
   absence of Retry matches ticket 14 §4.4.

## Interpretation notes and deviations

- v0.1.0's artifact store keeps one Separation set per Source; a re-separation
  replaces it (ticket 05, ADR 0004/0012). `separation_sets(track)` therefore
  returns at most one set, and the Separation card shows no set selector.
  Older Pitch render sets built from an earlier separation remain labelled and
  selectable in the Outputs tabs (ticket 11).
- Ticket 08 §7 is authoritative: stem rows, the version tabs, and their Export
  icons live in the Outputs card. The Separation card owns the model gate,
  status, progress, stall handling, Retry, and `Separate again`.
- Ticket 08 §9 is authoritative for Export: the entry point (an Outputs row or
  the transport) fixes the source, and the sheet only chooses format and
  destination. The old Separation-row selected-export state was removed.
- Retry is shown when the failed Job's snapshot is retryable. The former
  UI-side `_RETRYABLE_CODES` override was removed; the application/jobs layer
  owns that decision.
- `Licence details` now opens the existing Settings & About modal instead of
  repeating the notice in place (ticket 08 §10).
