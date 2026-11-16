# 07 — Workspace storage and lifecycle

**Type:** grilling
**Status:** resolved
**Blocked by:** 06

## Question

Where do session data, artifacts, model weights, caches, and logs live on Linux and Windows, and what is their lifecycle?

- App-data directory layout (platformdirs), per-session Workspace directory, and Artifact naming.
- Model cache location; read-only bundled assets vs writable cache. Set `HF_HOME` before importing Demucs, store the model under app data, and verify the first-run download with revision + SHA-256.
- Set `NICEGUI_STORAGE_PATH` to a per-user writable path before importing NiceGUI; do not rely on the default working-directory `.nicegui`.
- Temp/cache cleanup on exit, crash recovery, orphan cleanup; disk-space checks and user-facing failures.
- Recent-files storage and privacy. Note: pywebview's `private_mode=True` default disables GTK local storage and resets NiceGUI `app.storage.user`; do not rely on it for persistence unless `private_mode=False`.
- Retention policy for imported and downloaded Sources.
- Log location and rotation, with no telemetry.

The answer must define exact paths per OS and cleanup rules.

## Answer

**One `AppPaths` built once in `platform.environment.configure()` before any import: platform-idiomatic per-user roots (models in data, artifacts in an 8 GiB LRU cache, logs in state), a `STEMFORGE_HOME` override for portable/CI use, a single-instance lock, and a cache that outlives the in-memory Workspace. Explicit Remove, LRU eviction, and Clear cache are the only things that delete completed artifacts.**

### 1. Roots and exact paths

`platformdirs` 4.x, `appauthor=False`, `roaming=False`, `opinion=True` (default), no version segment. Add `platformdirs` to the runtime pins; the lockfile owns the exact version.

| Purpose | Linux | Windows |
|---|---|---|
| data | `~/.local/share/stemforge` (`$XDG_DATA_HOME/stemforge`) | `%LOCALAPPDATA%\StemForge` |
| config | `~/.config/stemforge` (`$XDG_CONFIG_HOME/stemforge`) | `%LOCALAPPDATA%\StemForge` |
| cache | `~/.cache/stemforge` (`$XDG_CACHE_HOME/stemforge`) | `%LOCALAPPDATA%\StemForge\Cache` |
| logs | `~/.local/state/stemforge/log` (`$XDG_STATE_HOME/stemforge/log`) | `%LOCALAPPDATA%\StemForge\Logs` |
| webview profile | `<data>/webview` | `<data>\webview` |

The per-OS casing matches platform convention and research 04's `$XDG_DATA_HOME/stemforge/models`. All roots are created on startup with mode `0700` where the OS supports it; `AppPaths` resolves symlinks **after** creating the directories so the `/media` route's `is_relative_to` check compares real paths. The Windows layout is one root plus `Cache`/`Logs` because `platformdirs` maps data, config, state, and log there; `appauthor=False` avoids a duplicated `StemForge\StemForge`.

**Bundled assets are read-only.** Binaries, the bundled `models.lock.json`, icons, and other package data are read from the frozen `_internal` directory (`sys._MEIPASS`, ticket 06) and never written. Every writable byte goes under one of the `AppPaths` roots; nothing is written beside the executable or into the working directory. That is what makes a read-only or Program Files install, a ZIP on a USB stick, and an AppImage mount all behave the same.

**`STEMFORGE_HOME` override:** when set, data/config/cache/logs become `<home>/data`, `<home>/config`, `<home>/cache`, `<home>/logs` (resolved absolute). It is the portable/USB and CI/test escape hatch. The Windows ZIP and installer both default to `%LOCALAPPDATA%`; "portable" means no installer, not data beside the exe. An unwritable override is a fatal preflight error (`storage.permission_denied`).

**No per-session directory.** The session is the in-memory Workspace. `sources/` and `renders/` are content-addressed and shared across sessions; that is what makes re-import reuse cached work.

```text
<data>/
  models/                      # HF_HOME; durable, never LRU-evicted
    hub/models--adefossez--HTDemucs/...
    installed/htdemucs.json    # verification marker
  webview/                     # pywebview profile (private_mode=False)

<config>/
  settings.json                # atomic, one lock, 0600
  storage_secret               # 32 random bytes, hex, 0600
  instance.lock                # OS file lock
  nicegui/                     # NICEGUI_STORAGE_PATH

<cache>/
  sources/<source_id>/         # ticket 05 layout + .used
  renders/<render_key>/        # ticket 05 layout + .used
  staging/<job_id>/            # jobs only; promoted or deleted
  tmp/                         # TMPDIR/TEMP/TMP
  deno/                        # DENO_DIR
  yt-dlp/                      # yt-dlp cachedir

<logs>/
  stemforge.log[.1..3]
  jobs/<UTC timestamp>-<job_id>.log
```

Artifact filenames and identities stay exactly as ticket 05 and ADR 0004 define them. `sources/<id>/native.<ext>` is a **copy**, never a hardlink or a reference to the original; the cached source stays immutable even if the user edits or moves the original. The manifest keeps the original display name, so cache paths stay short and Windows-safe.

### 2. Cache cap, LRU, and eviction

- Default cap **8 GiB** (`storage.cache_cap_bytes` in `settings.json`; no dedicated settings UI in v0.1.0). It counts `sources/` + `renders/` only. `tmp/`, `staging/`, `deno/`, `yt-dlp/` are transient/tool caches and excluded.
- Eviction unit is one **whole** source subtree or render set. Individual files inside a manifest are never evicted; a partial set is deleted entirely.
- LRU order is the mtime of `sources/<id>/.used` or `renders/<key>/.used`, touched when the Track is opened, rendered, or exported. Missing marker falls back to the manifest's `created_at`. Playback streaming does not touch it; opening the Track does.
- Enforcement points: at startup after the sweep, and before every Job from its output estimate. The live Workspace's artifacts, the active Job's staging, and `<data>/models` are never eviction candidates.
- If the cache cannot get under cap because everything left is live, the Job fails `storage.cache_full` ("Remove a track or clear cached audio"). `storage.disk_full` remains reserved for real volume exhaustion; `storage.permission_denied` for unwritable roots. Their user-facing messages are 14's.
- A "Clear cached audio" action calls `WorkspaceService.clear_cache()`: delete everything under `sources/` and `renders/` that the live Workspace does not reference, plus `tmp/`, `deno/`, `yt-dlp/`; 08 places the control.

### 3. Remove, retention, and refcounts

- **Startup and exit never delete completed artifacts.** Cache entries with zero live Tracks are normal after a restart; they are what the reuse promise depends on.
- **Remove Track** is the only explicit release: it cancels that Track's queued/running Jobs, drops its references, and, once no Track references the source, deletes `sources/<source_id>/` and every `renders/<render_key>/` derived from it. One Track per Source makes the refcount effectively one, but the rule is still written as a refcount. 08 must name the deletion in the confirmation ("Remove track and delete its cached audio").
- A recent-files entry survives Remove; clicking it re-imports or re-downloads. Eviction and Remove are the only automatic deletions; Clear cache is the bulk manual one.
- Imported **and** YouTube native files are retained alongside the canonical WAV for the life of the cache entry. There is no separate native-file expiry.

### 4. Model store and verification

- `HF_HOME=<data>/models`; the standard HF cache layout lives under it. Models are durable, outside the cache cap, and never evicted.
- `packaging/models/models.lock.json` (also bundled as read-only data) pins `repo_id=adefossez/HTDemucs`, `revision=cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`, `filename=955717e8.safetensors`, `size=84,025,440`, `sha256`, source URL, and the licence-notice text.
- The `model_install` Job downloads with `hf_hub_download(revision=...)`, verifies size and SHA-256, then writes `<data>/models/installed/htdemucs.json` (`model`, `revision`, `sha256`, `size`, `verified_at`, `app_version`). The marker appears only after successful verification. A mismatch deletes the snapshot and marker and fails `separation.model_missing` so the UI can offer a re-download.
- The worker always runs with `HF_HUB_OFFLINE=1` and never downloads. The parent re-verifies the weights hash before **every** Separation; 84 MB is cheap against a multi-minute job.
- `HF_HUB_DISABLE_SYMLINKS_WARNING=1` and `HF_HUB_DISABLE_TELEMETRY=1` are set for the parent and worker.
- Failed/partial downloads are left in the HF cache for resume; no model-cache sweep. First-run licensing/onboarding remains with 08/10/13.

### 5. Recent files and settings

- `SettingsStore` at `<config>/settings.json`: schema version, one process lock, `os.replace` atomic writes, mode `0600`. Corrupt JSON is renamed `settings.corrupt-<timestamp>.json` and defaults are recreated.
- `recent`: at most **10** entries, newest first, each `{source_id, title, origin_kind: "file"|"youtube", origin, duration_s, last_opened}`. `origin` is the absolute local path or the canonical `https://www.youtube.com/watch?v=<id>` URL. No audio, covers, cookies, or tag sets.
- Open behaviour: cache hit → open; miss + local path exists → re-import (same bytes produce the same `source_id`); miss + path gone → non-destructive "file moved or deleted" state with Remove-from-list; YouTube miss → offer re-download. No Job starts until the user acts.
- A "Clear recent files" action exists. The list is local-only, never logged wholesale, and never leaves the machine. Settings never use `app.storage.user` or browser storage; `private_mode=False` (ticket 06) only keeps pywebview's own profile coherent.

### 6. Logs

- `logs/stemforge.log`: stdlib `RotatingFileHandler`, 2 MiB × 3 backups, INFO by default, `STEMFORGE_LOG_LEVEL` override, UTF-8.
- `logs/jobs/<UTC timestamp>-<job_id>.log`: one per Job, hard cap 10 MiB, keep the last **20** Jobs and at most **7 days**, swept at startup. Worker stderr verbatim; worker JSONL stdout at DEBUG; the handler closes when the Job finishes.
- Logs contain paths, versions, commands, and tag records for support; never audio content. No telemetry, no network logging, no diagnostics upload (14 owns any local bundle).

### 7. Startup sweep, crash recovery, and cleanup

The single-instance lock is acquired **before** any sweep or artifact write, so a startup sweep cannot race another copy:

1. Configure paths and env, create the config root, acquire `<config>/instance.lock` (OS lock), then create the remaining roots. If the lock is held: native "StemForge is already running" message and exit 0 — `MessageBoxW` on Windows, `Gtk.MessageDialog` on Linux, stderr fallback.
2. Delete `tmp/*`, all of `staging/*`, and every `*.partial*` under `sources/` and `renders/`. The single-instance guarantee replaces ticket 05's 24-hour staging rule.
3. Delete `sources/<id>/` without a valid `source.json` and `renders/<key>/` without a valid `manifest.json`; delete any set whose referenced files are missing (the cache is rebuildable). A manifest with a **newer** schema is left untouched, ignored by the Workspace, and remains eligible for LRU eviction.
4. Compute cache usage and evict LRU until under cap.

On shutdown: cancel the active Job (its staging is deleted), clear `tmp/*`, save settings, release the lock; `atexit` repeats child-kill and `tmp` cleanup if NiceGUI's shutdown events do not run. A hard crash can leave a `<name>.partial.<ext>` at a user-chosen **export** destination; that directory is not ours to sweep, so it is documented as an accepted limitation (14 may surface it in the message).

### 8. Disk-space checks and failure messages

All estimates use PCM 44.1 kHz stereo f32 ≈ **21.2 MB/min per WAV** and MP3 192 preview ≈ **1.44 MB/min**:

| Job | Estimate | Requirement |
|---|---|---|
| import local | native size + canonical + preview, ×1.2 | `max(1 GiB, estimate × 1.5)` free on cache volume |
| import YouTube | duration × 1.5 MB/min for native + canonical + preview, ×1.2 | same |
| Separation | 5 WAVs + 5 previews, ×1.2 | same |
| Render | 1 or 5 outputs (WAV + preview), ×1.2 | same |
| model_install | 2 × 84 MB + 256 MiB | same |
| Export | WAV24 ≈ 15.9 MB/min worst case, ×1.2 + 64 MiB | free on the **destination** volume |

Checks run with `shutil.disk_usage` on the volume that will receive the bytes, before the Job starts and again before promotion when the estimate was uncertain. Failures use the existing `storage.*` namespace plus `storage.cache_full`; the UI offers Remove/Clear cache where applicable. Export checks never use the 1 GiB floor; a small MP3 must fit a nearly full USB stick.

### 9. Environment bootstrap

`platform.environment.configure()` sets these **before** any NiceGUI, Hugging Face, Demucs, yt-dlp, or Deno import, and the PyInstaller runtime hook asserts them:

| Variable | Value |
|---|---|
| `NICEGUI_STORAGE_PATH` | `<config>/nicegui` |
| `HF_HOME` | `<data>/models` |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` | `1` |
| `HF_HUB_DISABLE_TELEMETRY` | `1` |
| `DENO_DIR` | `<cache>/deno` |
| `TMPDIR` / `TEMP` / `TMP` | `<cache>/tmp` |

yt-dlp gets `cachedir=<cache>/yt-dlp` and a temp path inside `staging/<job_id>/` through its Python options, not environment. The worker's environment adds `HF_HUB_OFFLINE=1`, `OMP_NUM_THREADS`, `PYTHONUNBUFFERED=1`, and `PYTHONUTF8=1` (ticket 06). `storage_secret` is 32 random bytes hex at `<config>/storage_secret`, mode `0600`, regenerated if unreadable; there are no accounts, so regeneration costs nothing.

### 10. Downstream

- **08**: confirm-before-Remove copy, "Clear cached audio", "Clear recent files", stale-recent state.
- **10**: document the per-user roots and `STEMFORGE_HOME`; no model weights are bundled by this ticket (13/10 still decide acquisition).
- **14**: owns `storage.cache_full`, `storage.disk_full`, `storage.permission_denied` messages and the crash-left export partial.
- **15**: accepted limitations — cache is rebuildable and evictable; logs/recent paths are local only; a downgrade ignores newer-schema caches.
- `GLOSSARY.md` gains **Cache**; [ADR 0006](../../../docs/adr/0006-cache-outlives-workspace.md) records that the cache outlives the Workspace.

## Comments

**2026-11-15 — estimate correction from ticket 09.** §8's `import YouTube` row reads *"duration × 1.5 MB/min for native + canonical + preview"*. That undercounts the canonical WAV, which §8 itself prices at 21.2 MB/min; preview is 1.44 MB/min and native Opus/AAC ~1.5 MB/min. Use **24 MB/min** for the import estimate (×1.2, with the existing `max(1 GiB, estimate × 1.5)` free-space floor). Everything else in §8 stands. Details in [09](09-youtube-import-behavior.md).

**2026-11-15 — recovery amendments from ticket 14.** Missing `AppPaths` roots are recreated at the start of every Job and on every settings write (self-heal); a genuinely unwritable root still fails `storage.permission_denied`. The §7 sweep triggers a one-time recovery banner only when it actually deleted a staging directory, a tmp file, or a partial. Export writes `<final-name>.partial` in the destination directory and deletes it on handled failure, cancel, and clean close; only a hard crash leaves one, and About → Behaviour documents it. No automatic eviction on `storage.disk_full`/`storage.cache_full` — the error offers Remove/Clear cache and the user presses Retry. See [14](14-failure-modes-and-recovery.md) and ADR [0012](../../../docs/adr/0012-recovery-discards-unfinished-work-never-finished-work.md).
