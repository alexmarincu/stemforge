# Ticket 16 — YouTube import manual verification

**Date:** 2026-11-17
**Host:** Linux development container; Deno 2.9.8 and FFmpeg 9.0.2 are on
`PATH`. The live check is **manual and opt-in**: it never runs in CI and was not
run while implementing this ticket.

## Automated, offline evidence

- `tests/unit/test_youtube_options.py` — the exact `YoutubeDL` option table from
  ticket 09 §4 (audio-only, no playlist, no postprocessors, no cookies/accounts/
  proxy, `outtmpl`/`paths.home` and `cachedir` under `<cache>/yt-dlp/`,
  quiet/no-progress), plus the exception → code mapping.
- `tests/integration/test_youtube_job.py` — a stubbed `YoutubeDL` writes a
  generated WAV; the real FFmpeg decodes it, builds the preview, records the
  native hash, and commits `sources/yt-<id>/`. A second import over the same
  home with a `YoutubeDL` factory that raises on construction succeeds with zero
  network calls and zero pacing waits. Cancel mid-download leaves no staging or
  `.part` file. "Clear YouTube cache" removes only `<cache>/yt-dlp/`; sources,
  renders, the model store, the Deno cache, and recent files survive.
- `tests/ui/test_youtube_job.py` — a valid URL submits, reports in the single
  Job bar (`Checking video` / `Waiting for YouTube · N s` / `Downloading audio` /
  `Decoding audio` / `Finishing`), disables the row while it runs, selects the
  new Track on success, and shows the coded copy on failure. An invalid URL never
  reaches the service. Toggle-off hides the row while a local import still works.
  Settings & About → Storage → **Clear YouTube cache** deletes only the tool cache.

All automated tests block sockets, and the two adapter suites replace the
default `YoutubeDL` factory, so no test can construct a real one.

## Optional live check (do not run in CI)

Preconditions: a public, non-age-gated video no longer than 15:00; Deno 2.9.8
(or `STEMFORGE_DENO` pointing at it); FFmpeg/ffprobe on `PATH` or vendored.
Network access is required for the first import only.

```bash
cd /path/to/stemforge
STEMFORGE_HOME="$(mktemp -d)" uv run python - <<'PY'
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.engines.media import FfmpegTool
from stemforge.settings import SettingsStore
from stemforge.platform.environment import configure

url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # replace with a video you may use
paths = configure()
service = WorkspaceService(
    SettingsStore(paths),
    paths=paths,
    engines=ServiceEngines(media=FfmpegTool()),
)
service.runner.start()
try:
    job_id = service.submit_youtube(url)
    assert service.runner.wait_idle(600)
    snapshot = service.runner.snapshot(job_id)
    print(snapshot.state.value, snapshot.error_code, snapshot.result_keys)
finally:
    service.runner.stop()
PY
```

A GUI launch (`STEMFORGE_HOME="$(mktemp -d)" uv run stemforge`) is the other
option; accept the one-time consent, paste the link, and press Import.

### Expected network behaviour

- Exactly two yt-dlp extractions for a miss: a metadata-only resolve
  (`extract_info(download=False)`) and the download (`download=True`).
- A 5 s politeness wait after resolve and a second 5 s wait after the download,
  injected in tests and real in the application. The Job bar counts down; no
  other network request is made during either wait.
- After the Job commits, stop the network (or re-run with a `YoutubeDL` factory
  that raises) and import the same link again: it opens from
  `sources/yt-<id>/` immediately, with zero network calls.
- No cookies, account, proxy, PO-token provider, thumbnail, or update check is
  used, and nothing is sent to a telemetry endpoint.

### What a human should see

- On the first import only: the **Import from YouTube** consent modal with the
  exact ticket 09 §7 text, then never again.
- The Job bar: `Importing · Checking video` while resolving; `Waiting for
  YouTube · 5 s` before the download; `Importing · Downloading audio` with a
  byte/speed detail and a moving percentage; `Waiting for YouTube · 5 s` after
  the download; `Decoding audio`; `Finishing`; then the bar disappears.
- The new Track is selected automatically, titled from the video, labelled
  `YouTube`, with its duration/sample rate/channels; the preview plays.
- The recent list gains the canonical `https://www.youtube.com/watch?v=<id>`
  entry. A second import of the same link selects the same Track without a
  progress bar or network activity.
- Failures show the ticket 09 §8 headline and action (`age`, `unavailable`,
  `live`, `too long`, `rate limited`, `extractor outdated`, `network`,
  `download failed`), never a traceback or raw extractor text.
- Settings & About → Storage shows the `<cache>/yt-dlp/` size; clearing it leaves
  Tracks, cached audio, the model, and local files untouched.
