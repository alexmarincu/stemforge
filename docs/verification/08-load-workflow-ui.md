# Ticket 08 — load workflow manual verification

**Date:** 2026-11-17
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native mode therefore cannot be launched on
this host.

## Verified headlessly

- `tests/ui/test_load_flow.py` (NiceGUI user simulation, 13 passed):
  empty state, dialog-driven import through a fake media tool, track rows and
  source facts (duration/sample rate/channels), selection, one Job bar with
  stage/progress/cancel, cancellation, coded import failure copy and recovery,
  Remove confirmation with cached-audio deletion and untouched source file,
  recent-list re-import, missing-recent notice, multi-file import, drop path
  filtering, recovery banner and session dismissal, clean-sweep banner absence,
  the window title following selection/removal, and the initial signal path
  (`Source` done, `Separation · not run`, `Pitch · not rendered`).
- `tests/unit/test_open_dialog.py` (13 passed): exact pywebview filter grammar,
  multi-select request, cancel, case/whitespace extension matching, unsupported
  extension coded error, fake-path rejection, drop filtering.
- `tests/unit/test_run_ui.py`: `_run_ui` passes `native=True`, `reload=False`,
  `show=False`, the 1280x820 window size, and registers startup/shutdown.
- `uv run python -m stemforge --smoke-test`: exit 0, HTTP 200 on `/`.
- `uv run python -m stemforge --self-test`: exit 0.
- `uv run pytest -m "unit or ui"`: 1012 passed, 3 skipped.
- `ruff format --check`, `ruff check`, `mypy src/stemforge`, `lint-imports`:
  green.

## Release-qualification items that need real devices

1. Linux with GTK 3 + WebKitGTK 4.1: open the native window; click Add audio;
   confirm multi-select, the `Audio (*.wav;*.mp3;*.flac;*.m4a)` filter, cancel,
   and that a selection becomes a Track.
2. Windows 10/11 with WebView2: same dialog checks, plus the known pywebview
   cross-thread `ShowDialog` risk (ticket 01, research 01 §4.3/R3).
3. Native drop fidelity on both backends: confirm `pywebviewFullPath` is
   supplied; a browser-like `C:\fakepath`/`file://` value must only produce the
   quiet hint, never an import.
4. Native window geometry at the 1024x700 minimum, icon/title, and close
   behaviour (no close prompt, no veto, runner shutdown).
5. Play a real WAV/MP3/FLAC/M4A through the UI with the final GStreamer/WebView2
   stack (playback itself belongs to a later ticket; this one verifies import
   only).

The headless tests use injected media and dialog backends, so no real subprocess
or native window is exercised by `pytest -m "unit or ui"`.
