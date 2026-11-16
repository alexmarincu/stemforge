# Ticket 09 — preview manual verification

**Date:** 2026-11-17
**Host:** Linux development host without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display, so the native window and audible playback
cannot be exercised here.

## Verified headlessly

- `tests/integration/test_media_route.py` (28 passed) against a real uvicorn
  server on an ephemeral 127.0.0.1 port: GET 200 with `Accept-Ranges`,
  `Content-Length`, and `audio/mpeg`; open-ended, bounded, first-byte, last-byte,
  and clamped ranges return 206 with exact bytes and `Content-Range`; suffix and
  multi-range 416; HEAD 405 with `Allow: GET`; missing file, directory, literal,
  encoded, absolute, and symlink escapes 404.
- `tests/unit/test_range_parser.py` (21 passed): no header, `bytes=N-`,
  `bytes=N-M`, `bytes=0-0`, end clamping, whitespace/case tolerance, suffix,
  multi-range, invalid unit, empty/malformed, start past EOF, inverted range,
  empty file, and overlong integers.
- `tests/ui/test_preview.py` (6 passed): exactly one `<audio>` element across
  per-row play, rail play, transport play, track switching, and A/B; A/B swaps
  the source without a second player; missing preview is shown unavailable with
  no dead URL; degraded preflight shows the persistent banner, disables
  transport/seek/A-B/per-row play, and leaves Add audio, Separation, Pitch, and
  Export enabled; play controls disable while a mutating Job runs and re-enable
  when it finishes.
- `uv run python -m stemforge --smoke-test`: exit 0, HTTP 200 on `/`.
- `uv run python -m stemforge --self-test`: exit 0.
- `ruff format --check`, `ruff check`, `mypy src/stemforge`, `lint-imports`:
  green.

## Release-qualification items that need real devices

1. **Linux, GTK 3 + WebKitGTK 4.1 + GStreamer good plugins:** import a track,
   press Play, and hear it through the system sink. Pause, resume, drag the
   seek slider to the middle and near the end, switch Tracks while playing, and
   confirm the previous sound stops and only one player sounds.
2. **Linux range behaviour in the real engine:** with the WebKit inspector
   open, confirm seek requests are `Range: bytes=N-` and receive 206; confirm
   the engine does not probe with HEAD or suffix ranges (ticket 01 Q1).
3. **Windows 10/11, WebView2:** repeat item 1 and confirm the same single-player
   and seek behaviour; record whether WebView2 issues HEAD or suffix ranges.
4. **A/B with a real render (after ticket 11):** render `+3`, start playback on
   Original, press B, and confirm one player swaps to the render without a
   second stream, then back to Original. Seek first and confirm the position is
   preserved when feasible.
5. **Degraded host:** remove `gstreamer1.0-plugins-good` (or run on a host
   without it), launch, and confirm the banner, disabled transport/play, and
   usable Separation, Pitch, and Export entry points. Fix the host and confirm
   the banner disappears on the next launch.

The headless tests inject the preflight report and media backend, so no real
GStreamer, WebView2, or audible output is exercised by `pytest -m "unit or ui"`.
