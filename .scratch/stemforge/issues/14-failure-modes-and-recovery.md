# 14 — Failure modes and recovery

**Type:** grilling
**Status:** resolved
**Blocked by:** 06, 07, 09, 10

## Question

Define failure handling and recovery behavior:

- Corrupt/unsupported files, missing ffmpeg/model, insufficient disk, permission errors, interrupted Jobs, and app crashes mid-render.
- Cancellation semantics: what happens to partial Artifacts; cleanup guarantees.
- Concurrent operations: queue behavior, allowed/disallowed combinations, per-Track locking.
- Offline YouTube, yt-dlp breakage, and expired URLs.
- User-facing error taxonomy, retry affordances, and access to logs/diagnostics.
- Startup self-check (binaries, models, WebView2, GStreamer plugins, webkit typelib) and degraded modes.
- No close-veto in NiceGUI native mode: define autosave/checkpointing and how in-progress Jobs are protected when the window closes.

The answer must map each failure to detection, message, recovery, and cleanup.

## Answer

**One policy: every failure has exactly one owner, one stable code, one sentence of plain English, and one recovery action. Anything that prevents the app's core loop is fatal *before* the window and ends in a native message box with the fix line and an exit code; anything that removes only a capability degrades it. Nothing unfinished survives a restart, and nothing finished is ever deleted to recover from a failure.**

### 1. Principles

1. **Fail before the window, not in it.** Checks that can invalidate the whole app run before `ui.run`. Their failure is a native box plus an exit code, never a half-drawn UI with an error page.
2. **Degrade a capability, not the app.** Missing Preview is a banner; missing FFmpeg is fatal. The line is "can StemForge still do the job it exists for?"
3. **One code, one surface, one action.** Engines raise codes; `ui/formatting.py` owns every string; raw FFmpeg/yt-dlp/worker text goes only to the Job log.
4. **Cancel is not failure.** No error entry, no red track state, no Retry prompt.
5. **Finished work is never deleted to make room for a failure.** Only Remove, LRU eviction under the 8 GiB cap, and Clear cached audio delete completed artifacts (07, ADR [0006](../../../docs/adr/0006-cache-outlives-workspace.md)). Recovery never does.
6. **No resume.** The artifact store is the checkpoint; a Job that did not commit its manifest is gone.

### 2. Delivery surfaces

| Surface | Used for | Lifetime |
|---|---|---|
| **Native message box** | Fatal preflight only: unsupported platform, no native window, damaged Bundle, unwritable roots | Until dismissed; process then exits with the preflight code |
| **Inline card** | Separation, Pitch, and the model gate — the failure stays where the action was | Until the next action or dismissal |
| **Toast** | Import (plus the rail/empty state), Export (after the Job bar), Playback | 8 s, dismissible; never carries the only Retry |
| **Job bar** | The current Job's progress, stall warning, and Cancel; a failed Export lingers until its toast | While the Job runs |
| **Banner** | Degraded mode (persistent), resource warning, crash recovery (one-time) | One window session; at most two stacked, degraded first |
| **Row state** | An Outputs row whose preview went missing | Until repaired or the Track is removed |
| **Settings & About** | `Copy diagnostics` and `Open log folder` | Permanent |

There is no in-app error page and no modal error dialog. The native box and the confirmations (Remove, overwrite) are the only dialogs.

### 3. Startup preflight

`platform/preflight.py` runs after `environment.configure()` and the single-instance lock, before the window. The first fatal result wins; every result is recorded in the diagnostics report either way.

| Order | Check | Detection | Class | Exit |
|---|---|---|---|---|
| 1 | Platform | `sys.platform` + `platform.machine()`: Linux x86_64 with glibc ≥ 2.35, Windows x64 | fatal | 7 |
| 2 | Native window | Linux: `gi` imports `Gtk 3.0`, `WebKit2 4.1`, `Soup 3.0`; WebKitGTK ≥ 2.44. Windows: WebView2 `pv` in `EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` (HKCU or HKLM) and .NET release ≥ 394802 | fatal | 3 (Linux) / 5 (Windows) |
| 3 | Bundle | `ffmpeg`, `ffprobe`, `rubberband`, `deno` exist, are executable, and `--version` matches `vendor.lock.json`; `models.lock.json` readable | fatal | 8 |
| 4 | Storage | Each `AppPaths` root creatable and writable (probe file), or `STEMFORGE_HOME` valid | fatal | 6 |
| 5 | Preview stack | Linux: GStreamer elements `wavparse`, `mpg123audiodec`, `autoaudiosink` reachable (via `gi.repository.Gst` if importable, else `gst-inspect-1.0`, else unknown → assume present and let the runtime rule in §4.8 handle it). Windows: covered by the WebView2 assertion | **degraded** | 4 in `--preflight` only |
| 6 | Resources | Free disk on the cache volume, total RAM (`shutil.disk_usage`, `os.sysconf`/`GlobalMemoryStatusEx`) | warn | 0 |

**Fatal delivery.** Every fatal result goes through one function: GTK `MessageDialog` on Linux (the window check guarantees GTK is importable; if it is not, print the same text to stderr), `MessageBoxW` with `MB_ICONERROR` on Windows. Title **"StemForge can't start"**; body = one sentence, the exact fix line, and `Details: <log path>` (or `Logs are unavailable.`); buttons `OK` and `Copy details`, the latter copying the diagnostics report (§10). With `--report <path>`, the JSON report is written before the box. The process exits with the code above. Exit code **8** is new in this ticket (the flag table in [10](10-packaging-and-distribution-plan.md) had no code for a damaged Bundle); a comment there records it.

Exact fatal copy:

| Code | Sentence | Fix line |
|---|---|---|
| `preflight.platform_unsupported` | StemForge runs on 64-bit Linux and 64-bit Windows. | Linux needs glibc 2.35 or newer; Windows needs Windows 10 22H2 or Windows 11, x64. |
| `preflight.window_unavailable` | StemForge needs GTK 3 and WebKitGTK 4.1 to open its window. | Debian/Ubuntu: `sudo apt install libgtk-3-0 libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1` · Fedora: `sudo dnf install gtk3 webkit2gtk4.1` · Arch: `sudo pacman -S gtk3 webkit2gtk-4.1` |
| `preflight.webview2_missing` | StemForge needs the Microsoft Edge WebView2 Runtime. | Install the Evergreen Bootstrapper from https://developer.microsoft.com/microsoft-edge/webview2/ and start StemForge again. |
| `preflight.bundle_damaged` | StemForge is missing files from its installation. | Reinstall StemForge, or if you use the portable build, extract the archive again. |
| `preflight.storage_unwritable` | StemForge can't write to `<path>`. | Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder. |

**`--preflight` differs deliberately.** The CLI flag reports the preview stack as exit 4 because a script wants to know; the GUI treats it as degraded mode and starts.

**Degraded mode.** Banner, not a block: *"Playback isn't available on this system."* Hint: *"WebKitGTK needs GStreamer's good plugins: `sudo apt install gstreamer1.0-plugins-good` (Fedora `gstreamer1-plugins-good`, Arch `gst-plugins-good`). Separation, pitch, and export still work."* Every play control is disabled with the tooltip "Playback unavailable"; the Outputs rows keep their Export action. There is no codec fallback to offer: research 01 measured that WAV decoding also needs `gst-plugins-good`. The banner returns every launch until the host is fixed.

**Resource warnings** (dismissible, once per launch, no code): under 8 GB RAM — *"This computer has <x> GB of RAM. Separation needs about 3 GB free; long tracks may be slow."*; under 20 GB free on the cache volume — *"Only <x> GB of disk space is free. A 30-minute track can need up to 7.6 GB."* Resources never block a Job; the per-Job checks in [07](07-workspace-storage-and-lifecycle.md) §8 still do.

### 4. Failure catalogue

Every row names the code, how it is detected, the exact headline, and the one action. Hints are one sentence in the same table. `reason` is a stable short string in `StemForgeError.detail`, used only to pick copy, never shown.

#### 4.1 Local import

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `import.file_unavailable` | Dialog returns a path that `stat`/open fails | Can't open that file. It may have moved or been deleted, or you may not have permission to read it. | none / no |
| `media.probe_failed` | ffprobe exits non-zero or returns no stream | StemForge can't read this file. It may be damaged or in a format it doesn't support. | *Try WAV, MP3, FLAC, or M4A.* / no |
| `import.no_audio_stream` | Probe succeeds, no audio stream | This file has no audio track. | none / no |
| `import.too_long` | Duration > 30:00 after probe | This track is `<m:ss>` long. The limit is 30:00. | *Trim it and import again.* / no |
| `decode.failed` | FFmpeg decode exits non-zero | StemForge couldn't decode this file. | *The file may be damaged; try another copy.* / yes |
| `storage.disk_full` | Pre-check or pre-promotion `disk_usage` | Not enough disk space. This import needs about `<x>` and only `<y>` is free. | *Clear cached audio to make room.* / yes |
| `storage.permission_denied` | Root create/write fails | StemForge can't write to `<path>`. | *Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder.* / yes |

A cancelled import is not an error: the toast says *"Import cancelled."* only if the user cancelled from the Job bar, and the empty state returns to its idle form.

#### 4.2 YouTube import

The nine codes and their exact strings are final in [09](09-youtube-import-behavior.md) §8 (`import.youtube_url_invalid`, `import.youtube_playlist`, `import.unsupported_source` as a field error, `import.youtube_too_long`, `import.youtube_live`, `import.youtube_age_restricted`, `import.youtube_unavailable`, `import.youtube_rate_limited`, `import.youtube_extractor_outdated`, `import.network`, `import.download_failed`, `import.no_audio_stream`). This ticket owns only the surface and retry:

- The three field-level codes render under the URL field; no Job starts.
- Job-level codes render as a toast **and** in the empty state / rail; `Retry` appears only where 09 says yes, and it re-submits the identical URL.
- An expired or re-uploaded video is `import.youtube_unavailable`; the cache remains the artifact of record, and Remove + re-import is the only refresh (09 §10).
- Offline at startup is not a failure: no network is touched until an import; the model gate and cached tracks work.

#### 4.3 Model install

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `separation.model_missing` `reason=absent` | `model_install` marker missing when a Separation is requested | Separation needs the htdemucs model first. | *Download model* (inline gate) / — |
| `separation.model_missing` `reason=checksum` | SHA-256 or size mismatch; snapshot and marker deleted ([07](07-workspace-storage-and-lifecycle.md) §4) | The separation model needs to be downloaded again. | *The cached copy didn't match its checksum.* `Download model` / — |
| `separation.model_download_failed` `reason=offline` | Transfer raises a connection/DNS error | The model download failed. | *No connection. Check your network and try again.* / manual |
| `separation.model_download_failed` `reason=http` | Non-2xx, 403/404, HF error | The model download failed. | *Hugging Face didn't respond as expected. Try again later; if it keeps failing, update StemForge.* / manual |
| `storage.disk_full` | Space check before or during install | As §4.1. | *Clear cached audio* / yes |

No automatic network retry: the only automatic action is 06's re-submit of the pending Separation **after** a successful install, once. (This supersedes the ambiguous "one automatic UI retry" sentence in [06](06-app-architecture-and-module-boundaries.md) §14; a comment there records the reading.)

#### 4.4 Separation

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `separation.worker_crashed` | Exit code not 0/2/3/4, or tree-kill after Cancel | Separation stopped unexpectedly. | *The details are in the job log.* / yes |
| `separation.engine_error` | Worker `error` event (handled) | Separation failed while processing this track. | *Try again; if it keeps failing, open the job log.* / yes |
| `separation.protocol_mismatch` | `v` ≠ 1 or malformed JSONL | This version of StemForge can't read the separation worker's output. | *Reinstall StemForge or update to a newer version.* / no |
| `separation.output_invalid` | Missing stem, wrong rate/channels, duration skew > 50 ms | Separation finished, but its output didn't pass StemForge's checks. | *Try again; if it keeps failing, open the job log.* / yes |
| `media.validation_failed` | ffprobe rejects a staging file | The generated audio didn't pass validation. | *Try again; if it keeps failing, open the job log.* / yes |

Stall is a warning, never an error: after 30 s of silence the Separation card shows *"No progress for 30 seconds. Separation may still be working."* with `Keep waiting` and `Cancel`; the runner never kills it (06 §3). A failed Separation keeps the last successful stem set visible and playable; the card adds *"Stems from the last successful Separation are still available."* Cancelling deletes only staging.

#### 4.5 Pitch (Render)

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `pitch.engine_missing` | Rubber Band missing or `--version` fails at runtime (preflight already blocks a damaged Bundle) | The pitch-shift engine is missing. | *Reinstall StemForge.* / no |
| `pitch.render_failed` | Rubber Band exits non-zero or is tree-killed without Cancel | The pitch render failed. | *Try again; if it keeps failing, open the job log.* / yes |
| `pitch.output_invalid` | ffprobe: wrong duration/rate/channels | The pitch render finished, but its output didn't pass StemForge's checks. | *Try again; if it keeps failing, open the job log.* / yes |

The Pitch card gets the same inline error treatment as Separation; 08 only drew the Separation one. A master render and a stems render remain independent sets; failing one never touches the other.

#### 4.6 Export

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `export.write_failed` `reason=permission` | `EACCES`/`EPERM` at the destination | Couldn't save the file to `<folder>`. | *Choose another folder, or check its permissions.* / yes |
| `export.write_failed` `reason=in_use` | Windows sharing violation (`WinError 32`) | Couldn't save the file to `<folder>`. | *Close the file in other apps and try again.* / yes |
| `export.write_failed` `reason=name_too_long` | `ENAMETOOLONG` / Windows path limit | Couldn't save the file to `<folder>`. | *Choose a shorter name or folder.* / yes |
| `export.write_failed` `reason=device_gone` | `ENODEV`/`ENOENT` on the destination volume | Couldn't save the file to `<folder>`. | *Reconnect the drive and try again.* / yes |
| `export.write_failed` `reason=other` | Any other OSError from the final rename/write | Couldn't save the file to `<folder>`. | *Try another folder; the details are in the job log.* / yes |
| `storage.disk_full` | Free-space check on the destination volume, or `ENOSPC` | Not enough space at `<folder>`. This file needs about `<x>`. | *Choose another location.* / yes |
| `media.encode_failed` | FFmpeg export exits non-zero | Couldn't encode the file. | *Try again; if it keeps failing, open the job log.* / yes |

Export writes `<final-name>.partial` in the destination directory, renames it over the target only after the encoder exits 0 and ffprobe validates it, and deletes it on any failure, cancel, or clean close. Only a hard crash can leave one, and About → Behaviour says so. `<folder>` in the headline is the last path component; the full path is in Details.

#### 4.7 Storage and cache

| Code | Detection | Headline | Action / Retry |
|---|---|---|---|
| `storage.cache_full` | Cache at the 8 GiB cap with every remaining entry live | The audio cache is full (8 GiB), and everything in it is in use. | *Remove a track or clear cached audio.* / yes |
| `storage.disk_full` | Volume free space below the estimate | As §4.1. | *Clear cached audio* / yes |
| `storage.permission_denied` | Root or destination unwritable | As §4.1. | *Check permissions or `STEMFORGE_HOME`* / yes |

Nothing is deleted automatically on either full condition. The error reports needed vs free and offers `Clear cached audio…` (or `Choose another location` for Export); the user presses Retry. LRU eviction stays confined to the 8 GiB cap and never touches live artifacts.

#### 4.8 Playback

`playback.unavailable` is raised by the page's `<audio>` error handler or by a media-route 404. Toast: *"Couldn't play this file. It may have been cleared from the cache."* The transport stops, the row is marked **unavailable**, and the version tab falls back to `Original` or the next existing set. The row's action is the repair that recreates it: `Re-render` for a render, `Separate again` for stems, `Re-import` for a Source. Nothing is rebuilt silently.

#### 4.9 Unexpected

`internal.unexpected` — *"Something went wrong."* Hint: *"The details were written to the log."* The traceback goes to the app log at ERROR and the Job log; the UI shows the code, `Open log`, and `Copy diagnostics`. The app stays running; a failed page handler re-renders the page, and the runner thread is restarted if it died.

### 5. Cancellation and interruption

- **Queued** → removed immediately, snapshot `cancelled`, owning surface returns to its idle state. No toast unless the user pressed Cancel, then *"Import cancelled."* / *"Separation cancelled."* / *"Export cancelled."*
- **Running** → `cancel_requested`; the adapter raises `JobCancelled` at its next checkpoint, or the process tree is terminated after a 5 s grace. The Job log gets one `cancelled` line, never an error.
- **Cleanup is total:** `staging/<job_id>/`, the worker's `<stem>.partial.wav`, the destination `<final-name>.partial`, and `sources|renders/**/*.partial*` for that Job. HF download fragments are the one exception: they stay in `<data>/models` for resume (07 §4).
- **Commit is not interruptible.** Once a Job enters the promotion step (validate → manifest → one atomic rename), a Cancel arriving there is ignored and the Job reports `succeeded`. Half-promoted sets are impossible by construction.
- **Completed artifacts always survive a cancel.** Removing a Track is the only cancellation that also deletes finished work, and its confirmation says so.
- **Close** cancels the active Job, joins the runner for up to 5 s, deletes its partials, saves settings, releases the lock, exits 0. No prompt, no veto (06 §3, 08 §12). If the join times out, the tree is killed and the sweep on next start handles what remains.

### 6. Concurrency and ownership

One active Job at a time means **no per-Track locks**: a `JobSpec` carries its Track id, and removal cancels by id. The rules:

| While a Job runs | Allowed? |
|---|---|
| Select a Track, play, switch version or A/B | yes |
| Import, Separate, Render, Export (any second Job) | no — the control is disabled, and the service returns the running Job's id on a duplicate submit |
| Remove the Track owning the active Job | yes — cancels that Job, shows `Removing…` until the runner acknowledges (≤ 5 s), then deletes the cache subtree |
| Remove another Track | yes |
| Clear cached audio | no — disabled while any Job runs |
| Clear recent files, change settings, open About | yes |
| Close the window | yes — cancels and exits |

Remove is the only destructive action during a Job, and its modal already names the cache deletion (07 §3). A Removal that would delete staging in use never races: the runner acknowledges cancellation before `ArtifactStore` deletes the subtree.

### 7. Crash recovery and the startup sweep

The sweep from 07 §7 runs behind the single-instance lock, before the Workspace is built: `tmp/*`, every `staging/*`, every `*.partial*` under `sources/` and `renders/`, invalid or incomplete sets, then LRU to the cap. It never touches completed artifacts.

- **Recovery notice.** If and only if the sweep actually deleted a staging directory, a tmp file, or a partial, the UI shows a one-time dismissible banner: *"StemForge closed unexpectedly last time. Unfinished work was discarded; cached audio and completed renders are intact."* A clean launch shows nothing.
- **No resume, no job history.** A crashed Separation leaves nothing to resume; the Track returns to its last committed state, and the user re-runs the action. A crashed Render keeps its earlier sets.
- **Export partial.** A hard crash can leave `<final-name>.partial` beside the destination. It is never scanned for or deleted outside our roots; the error copy names it when we know it, and About → Behaviour documents it. A clean cancel, close, or handled error deletes it.
- **Corrupt settings** are renamed `settings.corrupt-<timestamp>.json` and defaults recreated (07 §5); the recovery banner does not fire for that.

### 8. Retry

- Retry appears **iff** `StemForgeError.retryable` is true; the flag is owned by the raising code and never guessed by the UI.
- Retry re-submits the **identical** `JobSpec`: same Track, same semitone and scope, same export format, destination, and overwrite consent. It does not reopen the format sheet or the save dialog.
- Retry is disabled while another Job runs, and it never chains: a retry that fails shows the error again, with no backoff and no attempt counter.
- **No automatic retries** for local work. The YouTube adapter keeps yt-dlp's internal `retries: 3` / `fragment_retries: 3` / `extractor_retries: 3` (09 §4), and a successful model install re-submits a pending Separation once (06 §3). That is the whole automatic surface.
- There is **no resume after restart** and no retry of a Job the app killed on close.

### 9. Error presentation contract

`ui/formatting.py` maps `(code, reason)` to `(headline, hint)`. The exception's own `message` is for the log; the UI never renders it. Unknown codes — including a worker code newer than the app — fall back to `internal.unexpected` with the original code in Details.

- The headline is a sentence, ≤ 90 characters, no code, no file extension jargon, no raw tool output.
- The **Details** disclosure shows `code · job <short id>`, the Job log path, `Open log`, and `Copy diagnostics`. Codes are always one click away, never in the headline.
- Toasts carry no Retry; the owning surface does.
- Never a traceback, never a stack frame, never a modal error.
- Numbers are formatted by one helper: one decimal, `MB` under 1 GiB, `GB` above, `m:ss` for durations.

### 10. Diagnostics report

`Copy diagnostics` (in About and in every Details disclosure) renders a read-only textarea in a modal and copies it via `navigator.clipboard.writeText`, falling back to `document.execCommand('copy')`; if both fail the text stays selectable. Contents:

```text
StemForge <version> · <bundle kind> · <os> <os version> <arch> · <session type>
Python <version> · <frozen|source> · PyInstaller <version>
CPU <model>, <cores>/<threads> · RAM <x> GB · free disk (cache) <x> GB
Preflight: window ok | preview ok (GStreamer <ver>) | bundle ok (ffmpeg <ver>, rubberband <ver>, deno <ver>) | storage ok
Paths: data=<path> cache=<cache> logs=<logs> models=<models> (STEMFORGE_HOME=<set|unset>)
Cache: <used> / <cap> · <n> sources · <n> renders · <n> evictions last 7 days
Settings: youtube_enabled=<bool> · cache_cap=<x> · export_dir=<path> · first_run=<bool>
Model: <installed|absent> · revision <rev> · sha256 <verified|not verified> · verified_at <ts>
Jobs: <id> <kind> <state> <code> (last 10, newest first)
--- app log (last 200 lines) ---
--- job log <id> (last 100 lines) ---
```

It contains local file paths and never audio, file contents, tags, or cookies (there are none). The modal carries *"Contains local file paths. Review before sharing."* Nothing is uploaded, zipped, or written without the user pasting it. The `--report <path>` flag from [10](10-packaging-and-distribution-plan.md) writes the same structure for CI and terminals.

### 11. What a failure writes

Per-Job log: code, reason, the exact argv of the failing tool, the stderr tail (last 40 lines), stage and progress at failure, and elapsed time. App log: one INFO line per failed Job (`job <id> <kind> failed code=<code> after <s>s`); full traceback for `internal.unexpected` at ERROR. Paths and versions are logged; audio content never is. Rotation and retention stay as 07 §6.

### 12. Amendments to resolved tickets

- **[06](06-app-architecture-and-module-boundaries.md)** — taxonomy gains `preflight.*`, `import.file_unavailable`, and `playback.unavailable`; `reason` is part of the `(code, reason)` copy key; the copy table lives in `ui/formatting.py`; commit is not interruptible; "one automatic UI retry" means the post-install Separation re-submit, not a network retry.
- **[07](07-workspace-storage-and-lifecycle.md)** — missing roots are recreated at the start of every Job and on every settings write (self-heal); the sweep's deletions trigger the recovery banner; the export partial is named `<final-name>.partial` and is deleted on clean cancel/close.
- **[08](08-ui-workflow-and-layout.md)** — the Pitch card gets the inline error state; a banner region exists for degraded/resource/recovery; About gains `Copy diagnostics` and the Behaviour note about a force-quit export partial.
- **[10](10-packaging-and-distribution-plan.md)** — exit code **8** (`preflight.bundle_damaged`) for a missing or non-executing vendor binary; `--preflight` keeps exit 4 for the preview stack while the GUI degrades.

### 13. Downstream

- **15** inherits the failure table as acceptance criteria: each code has a reachable trigger, a surface, a sentence, and an action; the fatal codes have a way to be exercised with `--preflight`; no `internal.unexpected` in a supported workflow.
- **11** tests `ui/formatting.py` exhaustively (every code has copy; no code falls through), the classifier fixtures, the sweep's banner trigger, the partial cleanup on cancel/close, and one native-box smoke on each OS.
- **13** needs no change; the model gate's "Licence details" already links to the weights section.

### 14. Accepted v0.1.0 limitations

- No Job resume, no checkpoint beyond committed artifacts, no crash "report" upload.
- A force-quit during Export can leave one `.partial` beside the destination.
- yt-dlp runs in-process, so a hard crash fails the Job rather than the app.
- Fatal checks report one failure at a time (first fatal wins), not a wall of every problem.
- Degraded playback is not repairable from inside the app; it needs host packages.
- The diagnostics report is copy-only: no file save, no zip, no upload.
