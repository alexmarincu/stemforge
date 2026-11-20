# Ticket 08 decision alignment

**Date:** 2026-11-20
**Scope:** Bring the Workspace UI in line with the resolved ticket 08 answer and
its amendments, then fix the concrete interaction defects found by the review.

## Behaviour now pinned to ticket 08

- **Outputs owns versions and artifacts.** `Original` plus every committed Pitch
  set are tabs in the Outputs header, in creation order, labelled `+3` or
  `+3 master` / `+3 stems` on collision. The Pitch card has no tabs.
- **Rows follow the active version.** Original shows the source row and, after
  Separation, Vocals and Instrumental with Drums, Bass, and Other behind
  `Show 3 more stems`. A stems render shows its five shifted rows with the same
  disclosure; a master render shows one `Shifted master` row. Every row has
  play and Export, and an unavailable row names its repair (re-import, separate
  again, or render again).
- **A/B is `Original | latest render`.** It switches the active version and
  resets playback; it no longer follows the last stem played or the last
  selected set.
- **Pitch primary action uses the current scope.** With Stems cached it renders
  stems; without them it renders the master. The separate stems button was
  removed.
- **Separation owns only the gate and Job state.** Model gate, progress, stall,
  retry, and inline errors stay in the card. Licence details opens About.
- **One player, no position carry-over.** A different artifact always starts at
  zero; the transport advances from the media element's `timeupdate`; pause and
  resume keep the loaded artifact.
- **Live chrome.** The signal path reflects source/Stems/Pitch state, the rail
  dot and meta reflect separating/stems/failure, and the window title follows
  selection, import, and removal.
- **Detail header and geometry.** The header carries Settings and Remove;
  Pitch and Separation sit two-up at ≥1150 px and stack below.
- **Banner cap.** At most two banners stack, degraded first.
- **About copy.** Storage gains `Open log folder`; Behaviour gains the
  close/shutdown contract and the force-quit `.partial` note.
- **Export entry points are authoritative.** The sheet chooses format and
  destination only; it no longer re-lists every export target.

## Service seam

The page and components no longer reach into `runner`, `artifacts`, or
`settings`. `WorkspaceService` now exposes:

- `pitch_render_preview_target(track, render_key, role)` for per-member render
  previews;
- `cache_usage_bytes()`, `cache_cap_bytes()`, `logs_dir()`;
- `preferred_export_dir()`, `remember_export_dir(path)`;
- `jobs()`, `job_snapshot(job_id)`, `cancel_job(job_id)`.

Output rows resolve their Export key from `WorkspaceService.export_targets`
instead of spelling the key grammar in the UI. A log-folder failure now has its
own `logging.open_failed` code rather than reusing a licensing code.

## Tests

- `tests/ui/test_pitch_flow.py`: version tabs moved to Outputs; current-scope
  Render; A/B latest render.
- `tests/ui/test_separation_flow.py`: rows and export moved to Outputs; per-stem
  A/B and the Separation set selector removed; rail status and licence link.
- `tests/ui/test_export_flow.py`: format-only sheet with a read-only source.
- `tests/ui/test_preview.py`: title, signal path, time progress, position reset,
  banner cap, unavailable row.
- `tests/ui/test_about_licensing.py`: open-log-folder and behaviour copy.

`uv run pytest -m "unit or ui"`: 1012 passed, 3 skipped. `ruff format --check`,
`ruff check`, `mypy src/stemforge`, and `lint-imports` are green.

## State correctness corrections

The UI-alignment review found six state defects; all are fixed and covered by
the UI suite:

- A queued batch import no longer masks the running Job: `_active_job()` prefers
  the RUNNING snapshot, so the Job bar reports the actual stage and Cancel
  targets that Job.
- Settings & About stays open when a Job finishes and refreshes the rail footer,
  and both `Clear cached audio` and `Clear YouTube cache` are disabled while any
  Job runs (ticket 14 §6).
- Import failures render Retry only when the Job snapshot is retryable, and the
  stored request re-submits the identical local path or YouTube URL, without a
  dialog (ticket 14 §8).
- A checksum failure on `separation.model_missing` renders the checksum headline
  and hint in the model gate, and Details links that Job (ticket 14 §4.3).
- Pitch Retry follows `StemForgeError.retryable` like Separation (ticket 14 §8).
- A playback failure switches the active version to Original, or the next set
  with an available preview, and loads it at zero without autoplay (ticket 14
  §4.8).

`uv run pytest -m "unit or ui"`: 1025 passed, 3 skipped.

## Remaining structural debt

The ticket 08 §14 component map is still not fully matched: the page shell,
rail, track detail, transport, Outputs, and Job bar remain in `page.py`; the
existing component modules are named `pitch.py`, `separation.py`, and
`export.py` rather than `*_panel.py` / `export_dialog.py`. This is a module
split, not a behaviour change, and is left for a separate refactor.
