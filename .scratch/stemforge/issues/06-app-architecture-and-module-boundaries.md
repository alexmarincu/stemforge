# 06 — Application architecture and module boundaries

**Type:** grilling
**Status:** resolved
**Blocked by:** 01, 02, 03, 05

## Question

Define the app's architecture before any implementation:

- Package/module layout (UI, domain, services, engines, platform abstraction) and dependency direction.
- Long-running Job model: in-process asyncio vs subprocess workers; queue and concurrency; progress events; cancellation semantics; crash isolation.
- Worker protocol if subprocess-based: CLI arguments, stdout JSON lines, heartbeat, exit codes, and temp-file handoff.
- State model for Workspace, Tracks, and Artifacts, and how UI reactivity binds to it.
- Platform abstraction: bundled-resource resolution, app-data directories, binary/model paths, and the Linux vs Windows seams.
- Local server security (bind address, file-access scope) in native mode.
- Python baseline (research 02 recommends 3.13), uv/package management, lockfile, and dev vs production dependency split; pin `numpy` explicitly because demucs 4.1.0 omits it.
- Error taxonomy and logging boundaries.
- Native integration seams: a dialog wrapper (strict filter grammar, save-extension normalization), one media route mounted for the preview cache (`add_media_files`, not per-file routes), and startup preflight hooks for WebView2/GStreamer/webkit.
- Engine adapters: `PitchEngine` (Rubber Band CLI, CR-delimited progress), FFmpeg/ffprobe subprocess wrapper, and yt-dlp Python API plus bundled Deno; isolate each behind a typed interface with cancellation and output validation.
- Frozen-build constraints: `freeze_support()` first in the main guard, `nicegui-pack`/`collect_submodules('nicegui')` for lazy imports, environment variables (`NICEGUI_STORAGE_PATH`, `HF_HOME`) set before imports, and lazy torch/Demucs import so the window appears before the heavyweight stack loads.

The answer must include a directory tree and explicit module responsibilities. Consult `codebase-design` for deep-module vocabulary.

## Answer

**One policy: a single `src/stemforge` package with a one-way dependency rule; the NiceGUI server process orchestrates one background `JobRunner` thread and one active Job at a time; only Separation crosses a process boundary as a Python worker (the frozen binary re-invoked with `--worker separation`), while Pitch, FFmpeg, and yt-dlp are adapters called by the runner; cross-thread state travels as immutable snapshots polled by the UI; artifacts are discovered from filesystem manifests, never a database; the HTTP server binds localhost only and mounts one cache-rooted media route.**

### 1. Package tree

```text
src/stemforge/
├── __main__.py            # freeze_support → env bootstrap → --worker dispatch → ui.run
├── application.py         # WorkspaceService: the only API the UI calls
├── artifacts.py           # ArtifactStore: layout, staging/commit, manifests, lookup, sweep
├── settings.py            # SettingsStore: JSON + atomic replace + lock
├── errors.py              # StemForgeError, code constants, worker/exit mapping
├── worker_protocol.py     # JSONL event types and protocol version (stdlib only)
├── logging_setup.py       # logging config, app log, per-Job log handler
├── domain/
│   ├── models.py          # Source, Track, ArtifactRef, ArtifactSet, JobState, RenderRequest
│   └── workspace.py       # Workspace aggregate and invariants
├── jobs/
│   ├── job.py             # Job, JobSnapshot, JobKind, JobSpec, JobContext, CancelToken
│   └── runner.py          # JobRunner thread: FIFO, dedupe, one active, watchdog
├── pipeline/
│   ├── import_job.py      # local + YouTube import
│   ├── separation_job.py  # engine → validate → gain → preview → manifest → promote
│   ├── pitch_job.py       # render set across original or four Stems
│   ├── export_job.py      # encode → validate → atomic write at destination
│   ├── model_job.py       # pinned model download + SHA-256 verification
│   └── gain.py            # set peak → one presentation gain
├── engines/
│   ├── separation.py      # SeparationEngine protocol + WorkerSeparationEngine
│   ├── pitch.py           # PitchEngine protocol + RubberBandCliEngine
│   ├── media.py           # FfmpegTool: probe, decode, encode, sum, peak
│   └── youtube.py         # YouTubeImporter protocol + YtDlpImporter
├── workers/
│   ├── __init__.py        # dispatch(name, argv)
│   └── separation.py      # the only module that imports torch/demucs
├── platform/
│   ├── paths.py           # AppPaths (data, cache, logs, models, config, webview)
│   ├── resources.py       # bundled assets/binaries in dev vs frozen
│   ├── binaries.py        # ffmpeg/ffprobe/rubberband/deno + worker_command()
│   ├── process.py         # Popen, line readers, terminate_tree, run_capture
│   ├── preflight.py       # CheckResult list, WebView2/WebKitGTK/GStreamer/binary checks
│   └── environment.py     # resolve paths, create dirs, set env vars before imports
├── integration/
│   ├── window.py          # module-level app.native.*, icon, title, downloads off
│   ├── dialogs.py         # native open/save wrappers, filters, extension normalization
│   └── media.py           # one /media mount over the cache root
└── ui/
    ├── page.py            # root page, client-local selection/A-B, poll timer
    ├── formatting.py      # user-facing labels, error messages, remediation
    └── components/        # empty_state, track_list, track_detail, separation_panel,
                           # pitch_panel, export_dialog, job_indicator, first_run, about
```

Tests mirror the seams: `tests/unit/` (domain, runner, manifests), `tests/integration/` (real FFmpeg fixtures, SQLite-free store), `tests/smoke/` (a 1-second Demucs run behind a marker, frozen-build launch). Ticket 11 owns the matrix.

**Dependency rule** (enforced by `import-linter` contracts in CI):

```text
ui → integration, application, domain
integration → platform, errors
application → jobs, pipeline, artifacts, settings, domain, errors
pipeline → jobs (JobContext only), engines, artifacts, domain, platform, errors
jobs → domain, platform, errors
engines → platform, errors
artifacts → domain, platform, errors
workers → worker_protocol, platform, errors
platform, domain, errors, worker_protocol → stdlib only
```

`ui` must not import `engines`, `workers`, `pipeline`, or `jobs`. `workers` is reached only by executable dispatch, never by import from the server process.

### 2. Module responsibilities

| Module | Responsibility |
|---|---|
| `__main__.py` | `freeze_support()` first, then `platform.environment.configure()`, then `--worker` dispatch, then import the app and call `ui.run`. The only module that knows about `sys.argv`. |
| `application.py` | `WorkspaceService`: import local/YouTube, open cached source, separate, render, export, cancel, remove Track, recent files, model status. Dedupes by Source and Job key; builds `JobSpec`s; the UI's sole entry point. |
| `artifacts.py` | Owns the cache layout from ticket 05. `stage(job_id)`, `commit_source`, `commit_separation`, `commit_render`, `find_source`, `find_separation`, `find_render`, `sweep_stale`, `release_source`, `release_render`. Validates manifests; the only writer of artifact files. |
| `settings.py` | `SettingsStore`: recent sources, preferred export directory, first-run flags; JSON at `AppPaths.config/settings.json`, `os.replace`, one lock. No browser storage. |
| `domain/` | Pure dataclasses and invariants. No I/O. One Track per Source per Workspace; removing a Track releases its source/render references. |
| `jobs/` | `JobRunner` owns the thread, queue, active Job, dedupe, watchdog, cancellation and snapshots. `JobContext` exposes `progress()`, `cancel`, `log`, and the `deps` bundle. It never imports pipeline code: `application` supplies the `run(ctx)` callable. |
| `pipeline/` | One function per Job kind. Each composes engines and `ArtifactStore`, holds its stage weights, validates every output, writes the manifest last, and promotes staging. No UI or NiceGUI imports. |
| `engines/` | Typed adapters over external tools. They take `CancelToken` and `ProgressSink`, validate their own output with ffprobe, raise coded `StemForgeError`s, and never touch manifests or the Workspace. |
| `workers/` | The frozen/venv entry for the Separation worker. Imports torch/demucs lazily inside `run()`, writes five WAVs into `--out`, emits JSONL, and writes nothing outside `--out`. |
| `platform/` | OS seams as plain modules: paths, bundled resources, binary resolution, process spawning/tree-kill, preflight checks, and pre-import environment setup. |
| `integration/` | The three NiceGUI-native seams: window configuration, dialogs, and the single media route. |
| `ui/` | Presentation only: page composition, refreshable regions, a 400 ms poll timer, client-local selection and A/B mode. All state changes go through `WorkspaceService`. |

### 3. Job execution model

- **One `JobRunner` daemon thread**, started in `app.on_startup` and stopped in `app.on_shutdown` (with an `atexit` fallback). It is a thread, not asyncio tasks, because every engine is blocking, `Popen` + reader threads is portable across Windows event-loop policies, and the UI is poll-based.
- **One active Job at a time**, FIFO. `submit(spec) -> job_id` is non-blocking and thread-safe. Jobs are deduped by `(kind, track_id)`; `model_install` dedupes by model name. A duplicate submit returns the existing job id.
- **Job kinds:** `import_local`, `import_youtube`, `separate`, `render_pitch`, `export`, `model_install`.
- **State machine:** `queued → running → succeeded | failed | cancelled`. There is no `cancelling` state; `cancel_requested` is a flag and the runner records `cancelled` when the engine exits. Finished jobs stay in memory, capped at 20, for error visibility.
- **Progress:** one fraction `0.0–1.0` plus `stage` and `detail` ("stem 2 of 4"). Each pipeline module owns its stage weights; adapters normalize their tool's signal into `JobContext.progress()`.
- **Watchdog:** reader threads touch `last_event_at` on any stdout or stderr line. After 30 s of silence the snapshot sets `stalled=True`; the UI warns and offers Cancel. It never auto-kills.
- **Cancellation:** queued → removed immediately; running → `cancel_requested`, engine process group terminated, 5 s grace, then tree kill. The Job's partials and staging are deleted. A cancelled Job is not a failure and is not logged as one.
- **Crash isolation:** Separation, Pitch, and FFmpeg run as subprocesses, so a segfault or OOM kills the Job, not the app. yt-dlp runs in-process on the runner thread; its exceptions fail the Job. The UI server is never blocked by engine work.
- **Shutdown:** the runner cancels the active Job, kills its process tree, joins for up to 5 s, removes partials, and saves settings. The window cannot veto close, so there is no close prompt.
- **Track removal** cancels that Track's queued/running Jobs and releases its source and render references.
- **No job chaining:** a Separation with no model fails `separation.model_missing`; the UI offers the download, and after `model_install` succeeds it re-submits Separation once.

### 4. Worker protocol (Separation only)

Invocation:

```text
# frozen
stemforge --worker separation --model htdemucs --device cpu \
  --input <cache>/sources/<source_id>/canonical.wav \
  --out <cache>/staging/<job_id> \
  --threads <physical cores> --expected-duration <seconds>

# development
python -m stemforge.workers.separation <same arguments>
```

Worker environment: `HF_HOME=<AppPaths.models>`, `HF_HUB_OFFLINE=1`, `OMP_NUM_THREADS=<physical cores>`, `PYTHONUNBUFFERED=1`, `PYTHONUTF8=1`. The model is downloaded by `model_install` before this worker runs; the worker never downloads.

Stdout is protocol only: one UTF-8 JSON object per line, each carrying `"v": 1`. Stderr is human log only.

```json
{"v":1,"event":"hello","engine":"demucs","engine_version":"4.1.0","model":"htdemucs","model_revision":"cbc8a9b…","device":"cpu","sample_rate":44100,"threads":8}
{"v":1,"event":"stage","stage":"model_load","state":"start"}
{"v":1,"event":"stage","stage":"model_load","state":"end","seconds":1.98}
{"v":1,"event":"progress","stage":"separate","fraction":0.334,"segment":2,"segments":41,"audio_seconds_done":80.2,"audio_seconds_total":240.0,"eta_seconds":247.0}
{"v":1,"event":"output","stem":"vocals","path":"vocals.wav","format":"wav","sample_rate":44100,"channels":2}
{"v":1,"event":"done","elapsed_seconds":374.2,"outputs":["vocals","drums","bass","other","instrumental"]}
{"v":1,"event":"error","code":"separation.engine_error","message":"…","retryable":false,"detail":"…"}
```

Rules:

- Unknown event types are ignored; a `v` other than `1` fails the Job as `separation.protocol_mismatch`.
- `output.path` is relative to `--out`; the parent rejects any path that escapes it.
- The worker writes `<stem>.partial.wav` inside `--out`, then renames within `--out`. It writes nothing outside `--out`.
- The worker writes `instrumental.wav` as the float32 sum of `drums + bass + other` while stems are in memory (ticket 05 §3).
- **Exit codes:** `0` success (must follow `done`); `2` handled failure (must follow `error`); `3` model unavailable; `4` invalid input; anything else is a crash, mapped to `separation.worker_crashed` with the stderr tail. A `2` carrying `code: "cancelled"` is treated as a cancelled Job.
- **Heartbeat:** Demucs emits roughly every 8 s and the model-load stage emits start/end. The parent's 30 s watchdog is the only liveness rule.
- **Temp handoff:** the parent validates all five WAVs (`pcm_f32le`, 44100 Hz, stereo, duration within 50 ms) before it measures peak, encodes previews, writes the manifest, and promotes the staging directory. Any validation failure deletes staging and fails the Job as `media.validation_failed`.

### 5. State model and UI reactivity

- `Source` is immutable: `source_id` (BLAKE2b-256 of native bytes, or `yt-<video_id>`), origin, native/canonical/preview/cover paths, canonical metadata record, content hash.
- `Track` is the session handle: `track_id` (UUID), `Source`, title, requested `semitones` (integer, default 0), `created_at`. Requested semitones are UI state until a Render Job creates an artifact set.
- `ArtifactRef` is immutable: `artifact_id` (BLAKE2b-256 of the master WAV), `source_id`, optional `render_key`, kind (`original`, `stem`, `instrumental`, `render`), name, master path, preview path, `gain_db`, manifest path.
- `ArtifactSet` groups the artifacts one Job produced, with its provenance and single presentation gain.
- `Workspace` holds an ordered list of Tracks. One Track per Source: importing content already loaded focuses the existing Track. Removing a Track releases its references; the cache subtree is deleted only when no Track references it (ticket 07 owns the refcount policy).
- **Reactivity:** one app-wide `Workspace` singleton inside `WorkspaceService`, so state survives page reloads. Each client holds only selection and A/B mode. Reads go through immutable `WorkspaceSnapshot` / `JobSnapshot` values taken under a lock; one `ui.timer(0.4)` refreshes `@ui.refreshable` regions. No event bus, no `ui.bind` on domain objects, no push.
- **Settings:** `SettingsStore` holds recent sources `{source_id, title, origin, last_opened}`, the preferred export directory, and first-run flags. It is never `app.storage.user` or browser storage; pywebview private mode makes that a trap.

### 6. Platform abstraction

Plain modules, not a `Platform` ABC. Only behaviour that genuinely diverges gets two implementations selected once at startup.

- `AppPaths`: `data`, `cache`, `logs`, `models`, `config`, `webview`, built with platformdirs. Ticket 07 owns the exact OS strings, disk caps, and retention.
- `resources.py`: dev root is the repo's `vendor/` plus `src/stemforge/assets/`; frozen root is `Path(sys._MEIPASS)` (the onedir `_internal` directory). `__file__` traversal is forbidden in frozen code.
- `binaries.py`: `ffmpeg()`, `ffprobe()`, `rubberband()`, `deno()`, and `worker_command(argv)`. Frozen: `sys.executable --worker <name> …`. Development: `sys.executable -m stemforge.workers.<name> …`.
- `process.py`: `Popen` with list argv and no shell; `CREATE_NEW_PROCESS_GROUP` on Windows; line reader threads; `terminate_tree()` using `os.killpg` on Linux and `taskkill /PID <pid> /T /F` on Windows; `run_capture()` for ffprobe.
- `preflight.py`: returns `CheckResult(name, status, message, fatal)` for bundled binaries, writable cache, Windows WebView2 (registry `pv` check), and Linux WebKitGTK 4.1 + `gst-plugins-good` (`mpg123audiodec`, `wavparse`). Fatal results show an error page; non-fatal results show a dismissible banner.
- `environment.py`: computes `AppPaths`, creates directories, and sets `NICEGUI_STORAGE_PATH` and `HF_HOME` **before** NiceGUI or Demucs is imported. `HF_HUB_OFFLINE=1` is set only in the worker's environment; the parent needs the network for `model_install`.
- Linux/Windows seams: the preflight checks, the app-data roots, the process-tree kill, and the window icon mechanism. Everything else is shared code.

### 7. Native integration, local server, security

- **Window:** `integration/window.py` sets `app.native.window_args` (`resizable=True`, `min_size=(960, 640)`), `app.native.start_args['private_mode'] = False`, `start_args['storage_path']` under app data, and `app.native.settings['ALLOW_DOWNLOADS'] = False` at module level, outside the main guard. `--worker` is dispatched before this module is imported.
- **Dialogs:** `integration/dialogs.py` wraps `app.native.main_window.create_file_dialog`. Filters use the strict grammar (description `[\w ]+`, patterns `*.wav;*.mp3` with no spaces). Save always appends or corrects the extension; the UI is disabled while a dialog is open.
- **Media:** one `app.add_media_files('/media', AppPaths.cache)` route, registered before `ui.run`. Starlette's `{filename:path}` supports nested paths, so URLs are `/media/sources/<source_id>/original.mp3` and `/media/renders/<render_key>/<artifact>.mp3`. NiceGUI confines paths to the root with `is_relative_to`; `.mp3` is registered with `mimetypes` at startup.
- **Server:** binds `127.0.0.1` explicitly with `native.find_open_port()`, `reload=False`, `show=False`, `fastapi_docs=False`, `endpoint_documentation='none'`, and a per-install `storage_secret` (32 random bytes hex, stored under `AppPaths.config`). No external listener, no telemetry.
- **Security posture:** no authentication in v1. Any local process can reach the random localhost port and fetch any file under the cache while the app runs. This is accepted and documented; the app exposes no destructive unauthenticated GET endpoints, and the media route is confined to the cache. If the posture needs tightening later, replace the mount with an MP3-only range route.
- **Lifecycle:** closing the window ends the app immediately; there is no veto. `app.on_shutdown` cancels the active Job, kills children, removes partials, and saves settings; `atexit` repeats the child-kill and partial cleanup in case shutdown events do not run.
- **Startup order:** `freeze_support()` → `environment.configure()` → `--worker` dispatch → import app modules → preflight → module-level native config → `/media` route → `ui.run(native=True)` → `app.on_startup`: start `JobRunner`, load settings, sweep stale staging/partials, build the Workspace.

### 8. Python baseline and dependency policy

- CPython **3.13.x**, pinned in `.python-version` and `requires-python = ">=3.13,<3.14"`. Research 02's 3.13 recommendation stands; 3.15 is blocked by sphn.
- One `pyproject.toml` and one `uv.lock`. `uv sync` for dev; `uv export` for the build. Dependency groups: `dev` (ruff, mypy, pytest) and `build` (pyinstaller). No second worker environment: the frozen app is one runtime, and the worker reuses it.
- `torch==2.15.0+cpu` from the CPU wheel index via `[tool.uv.sources]`. `numpy==2.5.4` is pinned explicitly because demucs 4.1.0 omits it; CI smoke-tests `from demucs.api import Separator`.
- Runtime pins: `nicegui==4.0.0`, `pywebview==6.2.1`, `demucs==4.1.0`, `yt-dlp==2026.10.26`, `yt-dlp-ejs==0.8.0`, `sphn==0.2.1`, `huggingface-hub==2.4.0`, `safetensors==0.8.0`. Vendored binaries: FFmpeg/ffprobe 9.0.2, Rubber Band 4.0.0, Deno 2.9.8; each recorded with source URL and SHA-256 in `packaging/`.
- Production code must not import torch at module scope anywhere outside `workers/separation.py`.

### 9. Error taxonomy and logging

`StemForgeError(code, message, retryable=False, detail=None, context=None)` is the only exception the UI sees. Codes are stable dotted strings in namespaces:

- `import.*`: `no_audio_stream`, `unsupported_source`, `download_failed`, `youtube_unavailable`, `youtube_age_restricted`
- `decode.*`: `decode.failed`
- `separation.*`: `model_missing`, `model_download_failed`, `worker_crashed`, `engine_error`, `protocol_mismatch`, `output_invalid`
- `pitch.*`: `engine_missing`, `render_failed`, `output_invalid`
- `media.*`: `probe_failed`, `encode_failed`, `validation_failed`
- `export.*`: `write_failed`
- `storage.*`: `disk_full`, `permission_denied`
- `internal.*`: `unexpected`

Worker `error` events carry the same codes; unknown ones map to `separation.engine_error` with the original code in `detail`. Engines raise codes, never user-facing prose; `ui/formatting.py` maps code to message and remediation. The UI shows the message and, under "Details", the code and the Job log path.

Logging uses stdlib `logging` only. Root logger `stemforge`, app log `AppPaths.logs/stemforge.log` (rotation and size owned by ticket 07), per-Job `logs/jobs/<job_id>.log`. Worker stdout JSONL is logged at DEBUG; worker and CLI stderr are copied verbatim into the Job log. Uvicorn logs at `warning`. No telemetry, no network logging.

### 10. Engine and adapter interfaces

```python
class SeparationEngine(Protocol):
    def separate(self, canonical: Path, out_dir: Path, *,
                 progress: ProgressSink, cancel: CancelToken, log: Logger) -> SeparationOutputs: ...

class PitchEngine(Protocol):
    def render(self, src: Path, dst: Path, *, semitones: int,
               progress: ProgressSink, cancel: CancelToken, log: Logger) -> None: ...

class MediaTool(Protocol):
    def probe(self, path: Path) -> MediaInfo: ...
    def decode_canonical(self, src: Path, dst: Path, *, cancel, log) -> None: ...
    def encode_preview(self, src: Path, dst: Path, *, gain_db, title, artist, cancel, log) -> None: ...
    def encode_export(self, src: Path, dst: Path, *, fmt, metadata, gain_db, cover, cancel, log) -> None: ...
    def sum_wavs(self, srcs: Sequence[Path], dst: Path, *, cancel, log) -> None: ...
    def peak_dbfs(self, path: Path, *, cancel, log) -> float: ...

class YouTubeImporter(Protocol):
    def download(self, url: str, dest: Path, *,
                 progress: ProgressSink, cancel: CancelToken, log: Logger) -> DownloadedSource: ...
```

`CancelToken` wraps a `threading.Event` and raises `JobCancelled` at safe checkpoints. `ProgressSink` is `(fraction, stage, detail) -> None`. Every adapter validates what it produced with ffprobe before returning, and none of them writes manifests or mutates the Workspace. v1 adapters: `WorkerSeparationEngine`, `RubberBandCliEngine`, `FfmpegTool`, `YtDlpImporter`. The interfaces exist because the tests use fakes and research 03 leaves a library-backed pitch adapter open for post-v1; they are not speculative framework layers.

### 11. Frozen-build constraints

- `freeze_support()` is the first statement in the main guard; `app.native.*` configuration stays at module scope so the window child sees it. Both are mandatory (research 01/04).
- `__main__` imports only stdlib and `platform.environment` before it inspects `--worker`; torch is imported only inside `workers/separation.py.run()`. The UI window must appear before the heavyweight stack loads.
- `NICEGUI_STORAGE_PATH` and `HF_HOME` are set before any NiceGUI/Demucs import; the PyInstaller spec adds a runtime hook that asserts they are set.
- The separation worker is the same executable (`sys.executable --worker separation`), never a second PyInstaller bundle; `sys.executable` is the app binary in onedir builds.
- PyInstaller onedir only, never onefile. The custom spec keeps nicegui-pack's whole-package-as-data trick, collects `nicegui`, `demucs`, and `yt_dlp` submodules, adds the WebKit2 typelib hook, vendors the binaries, trims `torch/test` and `torch/bin`, and sets `console=False`, the `.ico`, and Windows-only pywebview platforms.
- `reload=False` in shipped builds. No `multiprocessing` use of our own beyond NiceGUI's window spawn.

### 12. Manifest and artifact identity

The on-disk layout is ticket 05's. Manifests store paths **relative to their own directory** so the cache is movable.

- `source.json`: schema, `source_id`, origin, native/canonical/preview/cover filenames, canonical metadata record, content hash, `created_at`, app version.
- `stems/manifest.json`: `source_id`, source hash, engine `demucs 4.1.0`, model + revision + weights SHA-256, device, `gain_db`, `peak_dbfs`, artifacts (`name`, `role`, `file`, `sha256`, sample rate, channels, duration), validation.
- `renders/<render_key>/manifest.json`: `render_key`, `source_id`, semitones, engine `rubberband 4.0.0`, options (formant on, default window), input artifact hashes, `gain_db`, `peak_dbfs`, artifacts, validation.

`artifact_id` is the BLAKE2b-256 of the master WAV's bytes, consistent with `source_id` and `render_key`. Filenames are for humans and never parsed to infer state. The manifest is written last and the directory is promoted with one atomic rename; no `fsync`, because the cache is rebuildable.

### 13. Test seams

- `WorkspaceService` is constructible without NiceGUI by injecting `JobRunner`, `ArtifactStore`, `SettingsStore`, and engine fakes. Domain and pipeline tests run headless.
- Engine protocols have fake adapters that emit scripted progress and synthesize valid/invalid WAVs.
- `FfmpegTool` is tested against tiny fixture files with the real bundled FFmpeg; `ArtifactStore` is tested against a temp cache with real manifest files.
- One 1-second Demucs smoke test runs behind a `slow` marker in CI; the post-freeze smoke test launches the bundle, waits for HTTP readiness, and runs a 1-second separation.
- `import-linter` contracts are a test, so dependency drift fails CI rather than review.

### 14. Accepted v1 limitations

- One active Job: an export waits behind a running Separation.
- No authentication on the localhost server; any local process can read cached audio while the app runs.
- The Workspace is ephemeral; a restart starts empty with a recent-files list, and Job history is not persisted.
- yt-dlp runs in-process; a hung network call is bounded by its timeouts and progress hooks, not a killable worker.
- Model download is a separate Job with one automatic UI retry, not an automatic job chain.
- No `HEAD` or suffix/multi-range media support (ticket 05).
- Settings are a local JSON file, not NiceGUI/browser storage.

### 15. Downstream ownership

- **07** inherits `AppPaths`, `SettingsStore`, the refcount hooks, and the startup sweep.
- **08** inherits `WorkspaceService`, the snapshot shapes, and the three native seams.
- **09** inherits `YouTubeImporter` and the `import_youtube` Job.
- **10** inherits `binaries.py`, the frozen entry/dispatch, and the one-runtime rule.
- **11** inherits the engine fakes, import-linter contracts, and smoke-test hooks.
- **12** inherits the JobRunner thread and subprocess model for CPU/RAM budgets.
- **13** inherits the vendored-binary inventory and the attribution hooks.
- **14** inherits the error taxonomy and per-Job logs.
- **15** inherits the accepted v1 limitations above.

## Comments

**2026-11-15 — amendment from 10.** Frozen resources now have two roots instead of one: code, package data, `assets/`, and `models/models.lock.json` resolve from `sys._MEIPASS`; `vendor/**` binaries and the licence tree resolve from the **Bundle root**, `Path(sys.executable).resolve().parent` when frozen and the repository root in development. `platform/binaries.py` and `platform/resources.py` each own one rule, and the separation worker inherits the Bundle root because it is the same executable re-invoked as `sys.executable --worker separation`. Ticket 10 also adds `src/stemforge/smoke.py` for `--self-test`, `--smoke-test`, and `--smoke-separation`; `--preflight` stays in `platform/preflight.py`, and `__main__.py` dispatches all of them before importing the UI or torch. See ADR [0010](../../../docs/adr/0010-vendored-binaries-live-outside-the-frozen-payload.md).

**2026-11-15 — error-contract amendments from ticket 14.** §9's taxonomy gains `preflight.*` (`platform_unsupported`, `window_unavailable`, `webview2_missing`, `bundle_damaged`, `storage_unwritable`, `preview_unavailable`), `import.file_unavailable`, and `playback.unavailable`. Copy is keyed by `(code, reason)`, where `reason` is a stable short string in `detail`; `ui/formatting.py` owns every headline and hint, and the exception's `message` is log-only. Cancellation is honoured at stage boundaries only: a Cancel arriving after promotion starts is ignored and the Job reports success. §14's "one automatic UI retry" means the post-install re-submit of a pending Separation (already in §3), not an automatic network retry — there are none. See [14](14-failure-modes-and-recovery.md).
