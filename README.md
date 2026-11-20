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

`scripts/check.sh` runs the same gate as CI: formatting, lint, strict typing, the import
contracts, and the unit and UI test layers. Integration tests run when `ffmpeg` and
`ffprobe` are available.

## Licence

StemForge is licensed under the GNU General Public License, version 3 only
(`GPL-3.0-only`).
