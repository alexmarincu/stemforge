# StemForge

StemForge is a local desktop app for splitting a song into stems, shifting pitch, and
exporting the result. Everything runs on your machine: no account, no telemetry, and no
audio leaves the computer.

The repository contains the application, tests, packaging, and release pipeline.

## Development quickstart

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
uv sync
uv run stemforge
scripts/check.sh
```

On Linux, the native window needs GTK 3, WebKitGTK 4.1, and GStreamer installed
system-wide, plus the matching Python bindings in the venv:

```sh
uv pip install pygobject pycairo
```

`scripts/check.sh` syncs with `--inexact`, so it will not remove those bindings.
Without the native stack, `STEMFORGE_BROWSER=1 uv run stemforge` serves the UI at
`http://127.0.0.1:8080`.

`scripts/check.sh` runs the same gate as CI: formatting, lint, strict typing, the import
contracts, and the unit and UI test layers. Integration tests run when `ffmpeg` and
`ffprobe` are available.

## Licence

StemForge is licensed under the GNU General Public License, version 3 only
(`GPL-3.0-only`).
