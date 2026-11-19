# Ticket 11 — Pitch UI manual verification

**Date:** 2026-11-17
**Updated:** 2026-11-20 after aligning the UI with ticket 08.
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native mode and audible output cannot be
exercised here; the A/B listening items are deferred to real Linux and Windows
hosts.

## Verified headlessly

- `tests/ui/test_pitch_flow.py` (9 passed, NiceGUI user simulation):
  - the stepper is a request: stepping to `+3` leaves `now-playing` on
    `Original`, the player source empty, and no Outputs version tab present;
    the view moves only after the Render Job succeeds;
  - the primary action follows the current scope: it renders the source master
    before a Separation and all four Stems plus the Instrumental after one;
    there is no separate stems action (ticket 08 §5);
  - Render is disabled at `0`, enables at a non-zero request, disables while a
    Job runs, and is replaced by the passive “Viewing this render” state once
    the requested set is active;
  - version tabs live in the Outputs header. A single-scope set is labelled
    `+3`; after a stems set exists at the same semitone the tabs are
    `+3 master` and `+3 stems`. Selecting a tab loads the first row of that
    version into the one `<audio>` element without autoplaying;
  - A/B is always `Original | latest render`; B names the newest set and is
    disabled when no render exists. Selecting an older tab does not repurpose
    B (ticket 08 §7);
  - a failed Render shows the inline coded error (`pitch.render_failed`), keeps
    the requested `+3` and the Original view, and Retry submits the same spec
    again (two `render_pitch` Jobs, error cleared, tab appears);
  - after a re-separation the old stems set remains listed, labelled
    `based on an earlier separation`, and remains selectable; the new set is
    not labelled and loads a different preview;
  - a degraded preview preflight keeps the stepper and Render usable, creates
    the tab, switches the view, and leaves the player source empty with A/B
    disabled.
- `tests/unit/test_pitch_ui_service.py` (11 passed): tab order is creation
  order (oldest first). A single scope stays bare; a same-semitone collision
  adds `master`/`stems`; a non-colliding stems set is not relabelled. Scope
  lookup never returns the other scope at the same semitone, and `+3` stems is
  not found before a separation exists. A master set previews
  `original_preview.mp3`; a stems set exposes a preview for every member
  (`vocals_preview.mp3`, …, `instrumental_preview.mp3`). A stems set built from
  earlier Stems is flagged, is no longer offered as the `Open` hit for the
  current Stems, and the new set is selected once committed. `render_pitch`
  submits exactly one `render_pitch` Job, rejects `0` and a stems request
  without a separation before submitting, and fails cleanly without an engine.
- `tests/ui/test_preview.py` (8 passed): ticket 08 §11 still holds — playback,
  seek, per-row play, and A/B stay enabled during a Job while second-Job actions
  disable; the transport advances from `timeupdate`; switching artifacts resets
  the position; degraded preview keeps Pitch usable.
- `uv run pytest -m "unit or ui"`: 1012 passed, 3 skipped.
- `uv run python -m stemforge --self-test`: exit 0.
- `uv run python -m stemforge --smoke-test`: exit 0, HTTP 200 on `/`.
- `ruff format --check .`, `ruff check .`, `mypy src/stemforge`,
  `lint-imports`: green.

No UI or unit test spawns Rubber Band or FFmpeg: the pitch adapter and media
tool are fakes that write stand-ins under `tmp_path`.

## Release-qualification items that need real devices

1. **Linux, GTK 3 + WebKitGTK 4.1, Rubber Band 4.0.0 + GStreamer good
   plugins:** import a track, Render `+3`, and listen. Confirm the shifted audio
   plays through the one player, tempo is unchanged, and the passive
   “Viewing this render” state replaces the Render action. Step to `+5`; confirm
   the playing audio and the active Outputs tab do **not** move until Render
   succeeds. Seek, press A/B → B, and confirm one stream switches to the latest
   render at the start of the file (ticket 08 §7 resets position on a version
   switch), then A → Original.
2. **Windows 10/11 + WebView2:** repeat the same A/B and Render-listening
   checks; record whether seeking and A/B ever double-sound or leave a stale
   buffer in WebView2.
3. **Master and stems confusion:** before Separation, render `+3` from the
   master; separate, then render `+3` again. Confirm the tabs are `+3 master`
   and `+3 stems`, that A/B names the newest set, and that the stems tab loads
   its Vocals row while the master tab loads its Shifted master row. Re-separate
   and confirm the old stems set stays visible, labelled
   `based on an earlier separation`, and still plays.
4. **Render error on a real host:** hide the Rubber Band binary (or rename
   `rubberband`) and press Render. Confirm the inline `pitch.engine_missing`
   error and Retry; restore the binary and confirm Retry completes without a
   duplicate tab.
5. **Degraded host:** remove `gstreamer1.0-plugins-good`, launch, and confirm
   the Pitch card remains usable and silent (no player source), with A/B
   disabled and the degraded banner present.

## Interpretation notes

- Ticket 08 §5 is authoritative: the primary Render action uses the current
  scope (Stems when cached, otherwise the master). Ticket 11's older
  master-first reading is no longer implemented.
- An Outputs version tab loads the first artifact of its set: Shifted master for
  a master render, Vocals for a stems render. Per-artifact play buttons select
  any other row.
- A/B is a shortcut to the newest committed set; it does not remember the last
  selected render. Switching versions resets playback position.
