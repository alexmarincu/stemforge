# Ticket 24 — failure-mode and error-copy audit

Audited in the `ticket/24-failure-audit` worktree on 2026-11-17, based on
`integration/v1` (`f9d0beb`; tickets #1–#23 closed). The coordinator merges this
branch after the report; nothing here changes `.github/workflows/release.yml`.

The audit treats ticket 14 as the contract: one stable code, one headline of at
most 90 characters, one recovery action, and one owning surface per failure.
Tracebacks, stack frames, raw FFmpeg/yt-dlp/worker text, and modal error dialogs
stay out of the UI; they belong to the Job log. `ui/formatting.py` remains the
only copy table.

## Catalogue

`catalogue` is the parameterized
`tests/unit/test_failure_catalogue.py::test_every_code_has_a_trigger`; each case
invokes the production parser, classifier, engine mapping, preflight mapping, or
runner fallback that raises the code. The headline/action columns are the
rendered copy, and the length is the headline length in characters. Every
headline is ≤ 90 and contains no code, no traceback, and no raw tool text.

| Code | Trigger test | Surface | Headline length | Action |
|---|---|---|---:|---|
| `cancelled` | `catalogue[cancelled]` | Cancellation toast/state | 10 | No action needed. |
| `decode.failed` | `catalogue[decode.failed]` | Import toast + empty state | 36 | Try another copy of the file. |
| `export.write_failed` | `catalogue[export.write_failed]` | Export inline + Job bar | 23 | Try another folder; the details are in the job log. |
| `import.download_failed` | `catalogue[import.download_failed]` | Import toast + empty state | 60 | Try again; if it keeps failing, open the job log. |
| `import.file_unavailable` | `catalogue[import.file_unavailable]` | Import toast + empty state | 72 | Choose a file you can open. |
| `import.network` | `catalogue[import.network]` | Import toast + empty state | 14 | Check your network and retry. |
| `import.no_audio_stream` | `catalogue[import.no_audio_stream]` | Import toast + empty state | 29 | Choose a file with audio. |
| `import.too_long` | `catalogue[import.too_long]` | Import toast + empty state | 43 | Trim it and import again. |
| `import.unsupported_source` | `catalogue[import.unsupported_source]` | Field error (URL or file) | 39 | Paste a YouTube video link. |
| `import.youtube_age_restricted` | `catalogue[import.youtube_age_restricted]` | Import toast + empty state | 67 | Choose a video without an age gate. |
| `import.youtube_extractor_outdated` | `catalogue[import.youtube_extractor_outdated]` | Import toast + empty state | 64 | Update StemForge. |
| `import.youtube_live` | `catalogue[import.youtube_live]` | Import toast + empty state | 44 | Choose a finished video. |
| `import.youtube_playlist` | `catalogue[import.youtube_playlist]` | Field error under URL | 56 | Paste a single video link. |
| `import.youtube_rate_limited` | `catalogue[import.youtube_rate_limited]` | Import toast + empty state | 48 | Wait a few minutes, then retry. |
| `import.youtube_too_long` | `catalogue[import.youtube_too_long]` | Import toast + empty state | 46 | Choose a shorter video. |
| `import.youtube_unavailable` | `catalogue[import.youtube_unavailable]` | Import toast + empty state | 87 | Try another video. |
| `import.youtube_url_invalid` | `catalogue[import.youtube_url_invalid]` | Field error under URL | 38 | Paste a YouTube video link. |
| `internal.unexpected` | `catalogue[internal.unexpected]` | Fallback inline/toast | 21 | Open the log for details, then try again. |
| `licensing.files_missing` | `catalogue[licensing.files_missing]` | About inline | 53 | Reinstall StemForge, or open the notices in the repository. |
| `licensing.open_failed` | `catalogue[licensing.open_failed]` | About inline | 34 | Open the LICENSES folder in your file manager from the StemForge folder. |
| `media.encode_failed` | `catalogue[media.encode_failed]` | Export inline + Job bar | 25 | Try again; if it keeps failing, open the job log. |
| `media.probe_failed` | `catalogue[media.probe_failed]` | Import toast + empty state | 84 | Try WAV, MP3, FLAC, or M4A. |
| `media.validation_failed` | `catalogue[media.validation_failed]` | Export inline + Job bar | 43 | Try again; if it keeps failing, open the job log. |
| `pitch.engine_missing` | `catalogue[pitch.engine_missing]` | Pitch inline | 34 | Reinstall StemForge. |
| `pitch.output_invalid` | `catalogue[pitch.output_invalid]` | Pitch inline | 73 | Try again; if it keeps failing, open the job log. |
| `pitch.render_failed` | `catalogue[pitch.render_failed]` | Pitch inline | 24 | Try again; if it keeps failing, open the job log. |
| `playback.unavailable` | `tests/ui/test_preview.py::test_deleting_the_playing_preview_stops_and_reports_unavailable` | Playback toast + unavailable row | 65 | Re-import, re-separate, or re-render the file to rebuild it. |
| `preflight.bundle_damaged` | `catalogue[preflight.bundle_damaged]` | Native fatal box | 49 | Reinstall StemForge, or if you use the portable build, extract the archive again. |
| `preflight.platform_unsupported` | `catalogue[preflight.platform_unsupported]` | Native fatal box | 50 | Linux needs glibc 2.35 or newer; Windows needs Windows 10 22H2 or Windows 11, x64. |
| `preflight.preview_unavailable` | `catalogue[preflight.preview_unavailable]` | Degraded banner | 40 | WebKitGTK needs GStreamer's good plugins: `sudo apt install gstreamer1.0-plugins-good` (Fedora `gstreamer1-plugins-good`, Arch `gst-plugins-good`). Separation, pitch, and export still work. |
| `preflight.storage_unwritable` | `catalogue[preflight.storage_unwritable]` | Native fatal box | 41 | Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder. |
| `preflight.webview2_missing` | `catalogue[preflight.webview2_missing]` | Native fatal box | 52 | Install the Evergreen Bootstrapper from https://developer.microsoft.com/microsoft-edge/webview2/ and start StemForge again. |
| `preflight.window_unavailable` | `catalogue[preflight.window_unavailable]` | Native fatal box | 59 | Debian/Ubuntu: `sudo apt install libgtk-3-0 libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1` · Fedora: `sudo dnf install gtk3 webkit2gtk4.1` · Arch: `sudo pacman -S gtk3 webkit2gtk-4.1` |
| `separation.engine_error` | `catalogue[separation.engine_error]` | Separation inline | 46 | Try again; if it keeps failing, open the job log. |
| `separation.model_download_failed` | `catalogue[separation.model_download_failed]` | Separation inline gate | 26 | Check your network and try again. |
| `separation.model_missing` | `catalogue[separation.model_missing]` | Separation inline gate | 42 | Download the model. |
| `separation.output_invalid` | `catalogue[separation.output_invalid]` | Separation inline | 67 | Try again; if it keeps failing, open the job log. |
| `separation.protocol_mismatch` | `catalogue[separation.protocol_mismatch]` | Separation inline | 68 | Reinstall StemForge or update to a newer version. |
| `separation.worker_crashed` | `catalogue[separation.worker_crashed]` | Separation inline | 32 | Try again; the details are in the job log. |
| `storage.cache_full` | `catalogue[storage.cache_full]` | Separation inline | 64 | Remove a track or clear cached audio. |
| `storage.disk_full` | `catalogue[storage.disk_full]` | Owning inline surface | 22 | Clear cached audio to make room. |
| `storage.permission_denied` | `catalogue[storage.permission_denied]` | Owning inline surface | 41 | Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder. |

The copy checks are in
`test_failure_catalogue.py::test_every_code_has_a_headline_and_one_recovery_action`
and the existing `tests/unit/test_error_codes.py` / `tests/unit/test_ui_formatting.py`.
`playback.unavailable` is a UI state, not an exception: the browser
`<audio>` error handler maps it to the toast and unavailable row, and its
Details disclosure is covered by the headless page test plus
`tests/ui/test_diagnostics.py`.

### `internal.unexpected` reachability

`internal.unexpected` remains the fallback for an unknown exception class and
for a code emitted by a newer worker, exactly as ticket 14 §9 requires. No
supported user workflow reaches it:

- Every named import, YouTube, model, separation, pitch, export, storage,
  playback, licensing, cancellation, and preflight failure resolves to its own
  code in the catalogue above. `test_the_trigger_table_covers_the_taxonomy`
  proves the table covers every constant, and
  `test_the_independent_taxonomy_copies_stay_in_sync` keeps `errors.py`,
  `test_error_codes.py`, and `test_scaffold.py` identical.
- The production UI always injects `FfmpegTool`, `RubberBandEngine`, a
  `SeparationEngine`, and the native dialogs. The `application.py`
  "engines are not configured" guards are reachable only by constructing
  `WorkspaceService` programmatically without those seams; they are not a user
  action.
- `artifacts.py`'s post-commit "committed X is unreadable" guards run only
  after a successful promotion followed by an immediate filesystem failure.
  `separation_job._committed_file` is marked `pragma: no cover` and is likewise
  unreachable through a validated manifest.
- `media.sum_wavs` requires at least one internal input; the separation/pitch
  callers always pass three. It is an internal invariant, not a user failure.
- The browser-mode fallback (`STEMFORGE_BROWSER`) is a diagnostic server mode,
  not the supported desktop workflow; it has no native file dialog, so local
  import is not offered as a user path there. The native workflow always has a
  window.
- The one deliberate `internal.unexpected` trigger is the runner's catch-all
  for an unknown exception class; it is tested by
  `test_an_unexpected_exception_becomes_internal_unexpected` and is the
  fallback, not a user-facing failure.

The `--preflight` fatal path has no `internal.unexpected` branch reachable from
the six real checks: `_fatal_code` maps platform, window (Linux/Windows),
bundle, storage, and preview; the exhaustive catalogue exercises all six.

## Details and copy contract

Every inline error card renders the headline, the action, and one `Details`
disclosure. The disclosure shows `code · job <short id>`, the Job log path,
`Open log`, and `Copy diagnostics`. The path is the real per-Job log; `Open log`
uses the injected OS opener and a coded message if it cannot start. The About
modal also has `Copy diagnostics`.

The tests are headless: `tests/ui/test_diagnostics.py` injects a recording
clipboard and opener, never launches a native dialog or file manager. The modal
is a read-only textarea with "Contains local file paths. Review before
sharing."; copy uses `navigator.clipboard.writeText`, falls back to
`document.execCommand('copy')`, and leaves the text selectable with
"Copy failed. Select the text and copy it manually." when both fail. It never
uploads, zips, or writes.

## Diagnostics report

`src/stemforge/integration/diagnostics.py` is stdlib-only for rendering and
reads the normal app seams for the collector. `render_diagnostics` is a pure
function over `DiagnosticsFacts`; `collect_diagnostics` is best-effort so even a
fatal storage launch produces a copyable report. The text contains:

- version, bundle kind, OS/version/arch, session type;
- Python version, source/frozen, PyInstaller version;
- CPU model, cores/threads, RAM, free cache-volume disk;
- the preflight summary;
- data/cache/logs/models paths with `STEMFORGE_HOME=set|unset`;
- cache used/cap, source/render counts, evictions in the last seven days;
- YouTube toggle, cache cap, export directory, first-run flag;
- model installed/absent, revision, SHA-256 verification, verified timestamp;
- the last 10 Jobs (newest first);
- the last 200 app-log lines and, for the open Job, the last 100 Job-log lines.

The report contains local paths and log text, never audio, tags, file contents,
or cookies. `tests/unit/test_diagnostics.py` asserts the exact structure,
bounds the log tails, proves the recent-file title and import metadata never
appear, and proves collection does not write the report. Evictions are counted
from the small append-only `cache/evictions.jsonl` journal; the test checks the
seven-day window and malformed-line tolerance.

`--diagnostics` prints the report; `--diagnostics --report PATH` and a bare
`--report PATH` write the same text. The preflight/smoke `--report` forms keep
their JSON report from ticket 10/23; the fatal box's `Copy details` now copies
the full diagnostics text, while `--preflight --report` still writes the
preflight JSON before the box (ticket 14 §3). This split is tested by
`test_preflight.py::test_fatal_cli_writes_the_report_before_the_box_and_copies_diagnostics`
and `test_diagnostics.py`.

## Retries

Audit result: there is no automatic retry anywhere except the post-install
Separation resubmit.

- `ModelInstallJob` previously retried transient transport errors up to three
  times. That contradicted ticket 14 §8 and the ticket 06 amendment ("there are
  none"). The retry loop, sleep, attempt counter, and UI retry-delay parameter
  are removed. A transient URLError or 5xx now yields one failed attempt and
  `separation.model_download_failed reason=offline|http`, with the partial
  removed. Tests cover both.
- `tests/unit/test_job_runner.py::test_a_retryable_failure_is_not_automatically_retried`
  proves a `retryable=True` failure runs once and does not queue a second Job.
- The UI Retry buttons re-submit the identical spec only when the user presses
  them. The post-install Separation resubmit is consumed from the latch before
  submission (`resubmit_pending_separation`), so it happens exactly once and
  only on a successful install; the existing UI test
  `test_one_successful_install_resubmits_a_pending_separation_exactly_once`
  also proves later polls do not chain another Separation.
- yt-dlp keeps its own internal `retries`/`fragment_retries`/`extractor_retries`
  as ticket 09 §4 allows; that is not an app-level retry loop.

## Preflight, degraded mode, resources, and the sweep

- Fatal boxes go through `preflight.show_fatal_message`; unit tests use the
  injected backend and assert title, sentence, fix line, log path, and copy
  text. `--preflight` exit codes 0/3/4/5/6/7/8 remain as ticket 10/14 specify.
- Degraded preview: the GUI starts, the persistent banner shows
  "Playback isn't available on this system.", transports are disabled with
  "Playback unavailable", and Separation/Pitch/Export remain usable;
  `--preflight` exits 4 while the GUI exits 3 only for a fatal window check.
  Existing `tests/ui/test_preview.py` covers the banner and disabled controls.
- Low RAM/disk warnings never block. `check_resources` returns one warn result;
  the page now renders a dismissible `resource-banner` once per launch and
  leaves every action enabled. `test_preview.py::test_resource_warnings_are_a_dismissible_banner_and_never_block`
  covers it.
- The startup sweep runs behind the single-instance lock before the Workspace.
  The crash drill below proves stale staging and `.partial` files are removed,
  completed audio survives, the recovery flag is true only when the sweep
  deleted residue, and a second sweep is clean.
- Export writes `<final>.partial` and removes it on failure, cancel, or close;
  the drill and the existing UI cancel/export tests assert no final file and no
  partial.

## Drills

`tests/unit/test_drills.py` runs in the unit layer on both CI operating systems
(the Windows job runs `-m "unit or ui"`). It uses temporary `STEMFORGE_HOME`
roots and never fakes a Windows run; the same tests execute on the Windows
runner after merge.

| Drill | What the test does | Result |
|---|---|---|
| Process crash mid-Job | A child process stages a stem and a render partial under a temporary home, then exits hard with code 9. The parent reopens the store, runs the sweep, and asserts the residue is gone, the completed source survives, `deleted_anything` is true once, and the next sweep is clean. | pass |
| App close mid-Job | A running Job writes a partial, waits on its cancel token, and removes the partial in `finally`; `JobRunner.stop()` cancels and joins. The snapshot is `cancelled` and the partial is gone. | pass |
| Cancel mid-Job | A real Export Job copies an encoded file in 1 MiB chunks with a token that cancels during the copy; the destination partial and staging are removed and no final file exists. | pass |
| Offline model install | The injected transport raises `URLError`; one attempt, `separation.model_download_failed reason=offline`, retryable, no partial. | pass |
| Offline YouTube import | The production classifier maps a timeout to `import.network`/retryable. | pass |
| Disk full during Export | `OSError(ENOSPC)` during the destination copy maps to `storage.disk_full`, removes the partial and staging, and leaves the destination empty. | pass |
| Disk full during model install | `OSError(ENOSPC)` opening the `.partial` maps to `storage.disk_full`, retryable, one transport call, no partial. | pass |

The UI recovery-banner test
`tests/ui/test_load_flow.py::test_recovery_banner_shows_once_after_the_sweep_and_stays_dismissed`
covers the banner's once-only dismissal; the crash drill covers the state that
triggers it. The native fatal boxes themselves are manual items because the
unit suite cannot open GTK or Windows message boxes without a desktop; the
injected backend proves the exact payload and the `docs/qa/release-checklist.md`
rows carry the manual boxes.

## Gates

Run in this worktree before the final merge (see the report for the final
re-run):

| Gate | Result |
|---|---|
| `ruff format --check .` | pass |
| `ruff check .` | pass |
| `mypy src/stemforge` (strict) | pass, 61 files |
| `lint-imports` | 2 contracts kept |
| `pytest -m "unit or ui"` | pass (970 passed, 2 skipped) |
| `pytest -m "integration and not slow"` | pass (128 passed) |
| `pytest -m "integration and slow"` | pass (2 passed, 7 skipped) |
| `generate_notices.py --check` | 201 files match |
| `--self-test` / `--smoke-test` | pass |
| `--diagnostics` / `--diagnostics --report` | pass; text structure verified |
| `scripts/check.sh` | see final report |

## Deviations and remaining manual items

- **Native boxes.** GTK and Windows WebView2/.NET fatal boxes, their
  `Copy details` button, and the real clipboard cannot be automated in the
  headless unit/UI layer. The payload is unit-tested through the injected
  backend; the actual box is a manual checklist item (L12, W9).
- **Browser fallback.** `STEMFORGE_BROWSER` remains a diagnostic server mode.
  It has no native file dialog; local import is a desktop-window workflow.
  This is why the dialog's "no native window" guard is not listed as a
  supported user failure.
- **`--report` split.** `--preflight --report` and the smoke flags keep their
  ticket 10/23 JSON reports. The diagnostics text is written by
  `--diagnostics --report` or a bare `--report`, and the fatal box copies it.
- **Playback code.** `playback.unavailable` is a UI-state code produced by the
  browser `<audio>` error event; it has copy and a Details disclosure but is
  not raised as an exception. Its trigger is the headless page test.
- **Windows evidence.** The drills and catalogue run on the Windows CI runner
  after merge; no local Windows run was faked. The manual Windows rows remain
  in `docs/qa/release-checklist.md`.
