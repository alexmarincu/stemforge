# Ticket 11 — Pitch UI manual verification

**Date:** 2026-11-17
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native mode and audible output cannot be
exercised here; the A/B listening item is deferred to real Linux and Windows
hosts.

## Verified headlessly

- `tests/ui/test_pitch_flow.py` (8 passed, NiceGUI user simulation):
  - the stepper is a request: stepping to `+3` leaves `now-playing` on
    `Original`, the player source empty, and only the Original tab present;
    the view moves to `+3` only after the Render Job succeeds;
  - Render is disabled at `0`, enables at a non-zero request, disables while a
    Job runs, and is replaced by the passive “Viewing this render” state once
    the requested set is active;
  - a single-scope set is labelled `+3`; after a stems set exists at the same
    semitone the tabs are `+3 master` and `+3 stems`; selecting each previews
    the correct `/media/renders/<key>/…` URL in the one `<audio>` element;
  - A/B flips Original ↔ the selected set (or the newest set), the B label
    names that set, and exactly one `<audio>` element exists throughout;
  - the stems scope is absent before a Separation, offered as an explicit
    disabled action at `0`, and renders all four Stems plus the Instrumental
    when pressed;
  - a failed Render shows the inline coded error (`pitch.render_failed`), keeps
    the requested `+3` and the Original view, and Retry submits the same spec
    again (two `render_pitch` Jobs, error cleared, tab appears);
  - after a re-separation the old stems set remains listed, labelled
    `based on an earlier separation`, and remains selectable; the new set is
    not labelled and plays a different preview;
  - a degraded preview preflight keeps the stepper and Render usable, creates
    the tab, switches the view, and leaves the player source empty with A/B
    disabled.
- `tests/unit/test_pitch_ui_service.py` (11 passed): tab order is creation
  order (oldest first). A single scope stays bare; a same-semitone collision
  adds `master`/`stems`; a non-colliding stems set is not relabelled. Scope
  lookup never returns the other scope at the same semitone, and `+3` stems is
  not found before a separation exists. A master set previews
  `original_preview.mp3`; a stems set previews `instrumental_preview.mp3`.
  A stems set built from earlier Stems is flagged, is no longer offered as the
  `Open` hit for the current Stems, and the new set is selected once committed.
  `render_pitch` submits exactly one `render_pitch` Job, rejects `0` and a stems
  request without a separation before submitting, and fails cleanly without an
  engine.
- `tests/ui/test_preview.py` (7 passed): ticket 08 §11 still holds — playback,
  seek, per-row play, and A/B stay enabled during a Job while second-Job actions
  disable; degraded preview keeps Pitch usable.
- `uv run pytest -m "unit or ui"`: 504 passed, 100 deselected.
- `uv run pytest -m "integration and not slow"`: 100 passed, 504 deselected.
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
   the playing audio and the active tab do **not** move until Render succeeds.
   Seek, press A/B → B, and confirm one stream switches to the render at the
   start of the file (ticket 08 §7 resets position on a version switch), then
   A → Original.
2. **Windows 10/11 + WebView2:** repeat the same A/B and Render-listening
   checks; record whether seeking and A/B ever double-sound or leave a stale
   buffer in WebView2.
3. **Master and stems confusion:** after a Separation, render `+3` from the
   master action, then `+3` stems. Confirm two tabs, `+3 master` and `+3 stems`,
   that A/B names whichever was selected, and that each sounds like its own set
   (the stems tab previews the shifted Instrumental). Re-separate and confirm
   the old stems set stays visible, labelled `based on an earlier separation`,
   and still plays.
4. **Render error on a real host:** hide the Rubber Band binary (or rename
   `rubberband`) and press Render. Confirm the inline `pitch.engine_missing`
   error and Retry; restore the binary and confirm Retry completes without a
   duplicate tab.
5. **Degraded host:** remove `gstreamer1.0-plugins-good`, launch, and confirm
   the Pitch card remains usable and silent (no player source), with A/B
   disabled and the degraded banner present.

## Interpretation notes

- Ticket 11 overrides ticket 08 §5's “stems cached implies stems scope” for v0.1.0:
  the primary Render action always renders the master; the stems scope is a
  separate explicit action shown only after a Separation.
- A version tab for a stems set previews that set's shifted Instrumental: it is
  the one primary single-file output a stems set always carries, and v0.1.0 has no
  per-stem listening rows yet (ticket 13's surface).
- Page state remembers the last selected render per Track for A/B, so flipping
  to Original and back resumes the same comparison instead of jumping to a
  newer set; with no remembered set, B is the newest render.
