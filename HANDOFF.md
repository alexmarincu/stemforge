# StemForge v1 — Implementation Handoff

**Status:** decision-complete. Produced by [V1 scope, acceptance criteria, and roadmap](.scratch/stemforge/issues/15-v1-scope-acceptance-and-roadmap.md) on 2026-11-15.

**This document is the implementation entry point.** The decision record is the wayfinder map at [`.scratch/stemforge/map.md`](.scratch/stemforge/map.md) and the resolved tickets under `.scratch/stemforge/issues/`. Where this handoff and a ticket disagree, **the ticket wins**; this document indexes and sequences decisions, it does not re-decide them. New decisions discovered during implementation get a new issue (see §9).

---

## 1. What v1 is

StemForge is a local-first desktop audio utility for musicians, singers, and creators. A single NiceGUI window loads local audio or one YouTube URL, previews playback, shifts pitch in integer semitones with tempo preserved, separates vocals/instrumental locally with Demucs `htdemucs`, and exports through native dialogs. No accounts, cloud processing, telemetry, or external APIs beyond YouTube import. Linux and Windows only, distributed as a Linux tar.gz/tar.zst/thin AppImage and a Windows portable ZIP/Inno Setup installer.

**The destination of this effort is this handoff.** Building the repository, the bundles, and the release is a separate implementation effort.

---

## 2. Read these first

| Artifact | Purpose |
|---|---|
| [`.scratch/stemforge/map.md`](.scratch/stemforge/map.md) | Destination, standing decisions, Decisions-so-far index, out-of-scope |
| [`GLOSSARY.md`](GLOSSARY.md) | Product vocabulary — Artifact, Cache, Instrumental, Render, Separation, Workspace, etc. |
| [`docs/adr/`](docs/adr/) | ADRs 0001–0012: canonical format, safety gain, serial Jobs, manifests, localhost trust, cache lifetime, YouTube pinning, blocks/cap, weights policy, vendor root, thin AppImage, recovery |
| `.scratch/stemforge/issues/01`–`18` | The resolved decision record; each answer is authoritative |
| [`.scratch/stemforge/research/`](.scratch/stemforge/research/) | Source research reports (desktop shell, separation, pitch/media, packaging) |
| [`.scratch/stemforge/prototypes/08-ui-layout.html`](.scratch/stemforge/prototypes/08-ui-layout.html) | UI prototype; variant A is the design |
| [`.scratch/stemforge/bench/REPORT.md`](.scratch/stemforge/bench/REPORT.md) | Measured performance envelope |
| [`.scratch/stemforge/licensing/`](.scratch/stemforge/licensing/) | Attribution inventory and drafted shipped notice |

The short version of the settled stack: NiceGUI 4.0 native mode + pywebview 6.2; demucs 4.1.0 + `htdemucs`, CPU-only torch 2.15.0, Python 3.13; Rubber Band 4.0.0 R3 + formant; FFmpeg/ffprobe 9.0.2 GPL static; yt-dlp 2026.10.26 + EJS 0.8.0 + Deno 2.9.8; canonical stereo 44.1 kHz float32 WAV; MP3 192 preview; WAV 24/FLAC 24/MP3 320 export; PyInstaller 6.22.3 onedir, native builds per OS; GPL-3.0-only; weights downloaded on first use, never bundled.

---

## 3. Frozen v1 scope

### In scope

| Capability | What ships | Decision owner |
|---|---|---|
| Local load | WAV/MP3/FLAC/M4A through the native open dialog or drop; decode to canonical stereo 44.1 kHz f32 WAV; mono → dual-mono; >2 channels downmixed with a UI note; 30-minute cap; native file retained; cache by content hash | [Audio pipeline and format policy](.scratch/stemforge/issues/05-audio-pipeline-and-format-policy.md), [Performance and resource envelope](.scratch/stemforge/issues/12-performance-and-resource-envelope.md) |
| YouTube import | One public, non-age-gated video ≤ 15 min; accepted URL shapes; playlist/channel/search/non-YouTube field errors; pinned yt-dlp/EJS/Deno; consent modal once; persistent disclaimer; enable/disable toggle; cache by video ID; no cookies, PO tokens, or updater | [YouTube import behavior](.scratch/stemforge/issues/09-youtube-import-behavior.md) |
| Preview | One player at a time; cached MP3 192 over the single `/media` route; GET + `Range: bytes=N-`; A/B switches version; per-row playback; degraded mode when the host GStreamer stack is missing | [Desktop shell and audio-preview feasibility](.scratch/stemforge/issues/01-desktop-shell-and-audio-preview.md), [UI workflow and layout](.scratch/stemforge/issues/08-ui-workflow-and-layout.md) |
| Pitch shift | Integer semitones −12…+12; explicit Render; from the original master or all four Stems; immutable content-addressed render sets per (semitones, scope); version tabs; A/B Original ↔ newest; shifted Instrumental summed from shifted Stems | [Pitch-shift engine and media pipeline](.scratch/stemforge/issues/03-pitch-shift-and-media-pipeline.md), [Audio pipeline and format policy](.scratch/stemforge/issues/05-audio-pipeline-and-format-policy.md) |
| Separation | `htdemucs` on CPU; first-run 84 MB download behind a consent gate, pinned revision + SHA-256; 8-minute blocks with a 1 s crossfade; progress/cancel/stall warning; four Stems + Instrumental; previews; shared presentation gain; atomic promotion | [Separation engine and invocation](.scratch/stemforge/issues/02-separation-engine.md), [Licensing and attribution compliance](.scratch/stemforge/issues/13-licensing-and-attribution-compliance.md), [Failure modes and recovery](.scratch/stemforge/issues/14-failure-modes-and-recovery.md) |
| Stems + Instrumental | Vocals and Instrumental primary; drums/bass/other expandable; every row plays and exports; per-stem A/B; older renders labelled "based on an earlier separation"; re-separate never deletes them | [Audio pipeline and format policy](.scratch/stemforge/issues/05-audio-pipeline-and-format-policy.md), [UI workflow and layout](.scratch/stemforge/issues/08-ui-workflow-and-layout.md) |
| Export | Format sheet → native save dialog → `.partial` → atomic rename; WAV 24, FLAC 24, MP3 320; record-driven tags; cover in MP3/FLAC; no normalization; Windows-safe filenames; destination-space check; per-reason errors | [Audio pipeline and format policy](.scratch/stemforge/issues/05-audio-pipeline-and-format-policy.md), [Failure modes and recovery](.scratch/stemforge/issues/14-failure-modes-and-recovery.md) |
| Workspace and storage | Per-user roots plus `STEMFORGE_HOME`; 8 GiB LRU cache; explicit Remove; recent files (10); settings JSON; logs; single-instance lock; startup sweep and recovery banner; diagnostics report | [Workspace storage and lifecycle](.scratch/stemforge/issues/07-workspace-storage-and-lifecycle.md) |
| Distribution | Linux tar.gz (canonical) + tar.zst + thin AppImage; Windows portable ZIP + per-user Inno Setup; WebView2 bootstrapper; unsigned v1 + `SHA256SUMS`; notices/source offer; GitHub Actions release | [Packaging and distribution plan](.scratch/stemforge/issues/10-packaging-and-distribution-plan.md), [Repository home and release URLs](.scratch/stemforge/issues/18-repository-home-and-release-urls.md) |
| App icon | A — Badge; master SVG, ICO set (16–256 px), 512/1024 PNGs and a mono fallback at `src/stemforge/assets/`; shipped in the window/exe/installer/AppImage/About | [App icon art](.scratch/stemforge/issues/17-app-icon-art.md) — resolved; rationale in `.scratch/stemforge/prototypes/17-icon/RATIONALE.md` |

### Deferred, not v1

Waveform display; stem mixer (mute/solo/volume); persistent project library; batch export; keyboard shortcuts; themes; draft/low-quality pitch render mode; formant toggle; "Save original" passthrough; YouTube thumbnail embedding; bundled or offline `htdemucs` weights; project-built audio-only FFmpeg; real-time pitch shifting, VST/plugin hosting, karaoke/MIDI; cloud sync, accounts, telemetry, auto-update, i18n; code signing and SmartScreen reputation; distro packages (deb/rpm/Flatpak); macOS, ARM, mobile, 32-bit; building or signing Windows bundles on Linux; Windows performance measurement on this map.

### Accepted v1 limitations (state them, do not "fix" them silently)

- 48 kHz sources are resampled to 44.1 kHz, including untouched exports; multichannel is downmixed.
- Preview is MP3 192; there is no `HEAD` route and suffix/multi-range requests return 416.
- No loudness normalization; a shared presentation gain only protects full scale.
- One active Job; an export waits behind a Separation. The Workspace is ephemeral; the cache and recent list survive.
- No authentication on the localhost server; any local process can read cached audio while the app runs.
- No Job resume; a crash discards unfinished work and never finished work. A hard crash can leave one `.partial` beside a chosen export destination.
- yt-dlp runs in-process and is pinned; YouTube changes can break import until a new app release. No cookies, PO tokens, or update check.
- Linux native mode needs host GTK 3, WebKitGTK 4.1 (2.44+), and GStreamer good plugins; missing preview degrades playback only. Missing WebKitGTK falls back to browser mode via `run.sh`.
- Windows figures (startup, RTF, RSS) are **unmeasured in this effort**; measuring them is hardening work, and the handoff discloses that limitation.
- The native window, native dialogs, and live YouTube import are manual QA; CI covers preflight, the frozen server, and the frozen separation.
- Newer-schema caches are ignored, not migrated.
- No AppImage self-containment: it is a thin host-dependent wrapper, not the primary artifact.

---

## 4. Acceptance criteria

Acceptance is **two-part**: a user-visible pass on the OS matrix, and an automated gate from [Testing and CI strategy](.scratch/stemforge/issues/11-testing-and-ci-strategy.md) with its existing thresholds. No new numeric thresholds are introduced here. Linux and Windows both need the manual matrix; Windows has no automated native-window path.

### Per capability

| Capability | User-visible pass (both OSes) | Automated gate |
|---|---|---|
| **Local load** | The native open dialog loads WAV/MP3/FLAC/M4A as one playable Track with no network; drop works where pywebview yields real paths, and the dialog remains the reliable path; mono and 3-channel files normalize as specified; a 30:01 file fails with the exact `import.too_long` copy; a video-only MP4, truncated WAV, and random bytes each produce their coded error and leave no Track; Remove deletes cached audio but not the user's file | `tests/integration/test_media_ffmpeg.py` (44.1/48/mono/3ch decode, 50 ms tolerance), `tests/integration/test_artifact_store.py`, `tests/ui/test_load_flow.py` |
| **YouTube import** | A public ≤15 min video imports and previews; playlist, channel, search, and non-YouTube links fail as field errors without starting a Job; live, age-gated, private, and >15 min fail with the exact [09 §8](.scratch/stemforge/issues/09-youtube-import-behavior.md) copy; consent appears once; the toggle hides the row and affects only YouTube; a second import of the same link touches the network zero times; cancel mid-download leaves no staging | URL-parser table tests, classifier fixtures (no network), stubbed `YoutubeDL` option/progress/cancel tests, `tests/ui/test_load_flow.py`; live smoke is opt-in and never in CI |
| **Preview** | Exactly one player sounds at a time; play, seek, and switch Tracks; A/B switches the version without mixing; a missing preview marks the row unavailable and offers the repair; with GStreamer missing, the banner appears, the transport is disabled, and Separation/Pitch/Export still work | `tests/integration/test_media_route.py` (GET 200, Range 206, HEAD 405, suffix/multi 416, traversal 404), `tests/ui/test_pitch_flow.py`, one-player assertions |
| **Pitch shift** | ±12 and ±1 render with tempo preserved; the stepper is a request, not a view; a master render does not satisfy a stems render; tabs show `+3` and `+3 master`/`+3 stems`; A/B is Original ↔ newest set; Instrumental equals the sum of shifted non-vocal Stems; re-separating keeps old render sets labelled as based on an earlier separation; errors are inline with Retry | `tests/integration/test_pitch_rubberband.py` (440 × 2^(N/12), ±0.15 semitone, 50 ms, stereo), `tests/integration/test_instrumental_sum.py` (≤1e-3), `tests/ui/test_pitch_flow.py` |
| **Separation** | First use shows the inline model gate (84 MB, revision, licence details); download progress/cancel/offline retry work; after install the pending Separation auto-submits once; a 4-minute track separates in blocks with progress and Cancel; a cancelled or failed run keeps the last successful Stems playable; output is four Stems plus Instrumental; starting a second Separation without the model fails with `separation.model_missing` and the gate, not a crash | `tests/unit/test_model_install.py`, `tests/integration/test_worker_protocol.py` against `stub_worker.py`, `tests/integration/test_demucs_import.py` (`slow`), `--smoke-separation` <60 s / <2 GB on both OSes at nightly/release; first-run download and offline are manual |
| **Stems + Instrumental** | Vocals and Instrumental are primary; drums/bass/other sit behind "Show 3 more stems"; every row plays and exports; Instrumental audibly matches the non-vocal mix; per-stem A/B works; versions remain independent across re-separation | `tests/integration/test_instrumental_sum.py`, `tests/ui/test_separation_flow.py`, `tests/ui/test_pitch_flow.py` |
| **Export** | WAV 24, FLAC 24, and MP3 320 each export through the native save dialog with the matching filter; a typed wrong extension is corrected, not used to switch format; filenames are Windows-safe; MP3/FLAC carry tags and cover, WAV carries INFO tags and no cover; no sample exceeds 0 dBFS after the shared gain; a failed export leaves no final file and offers Retry; no batch, no "Save original" | Export round-trip tests (codec, bit depth/bitrate, 50 ms, tags, cover, peak), `tests/ui/test_export_flow.py`, `tests/unit/test_artifact_store.py`; native dialogs are manual |
| **Storage, recovery, diagnostics** | Per-OS roots and `STEMFORGE_HOME` are used; a second instance shows the native "already running" message; startup sweeps staging/partials and shows the one-time banner only when it deleted something; finished artifacts survive failures; the 8 GiB LRU never evicts live work; Remove and Clear cached audio behave as specified; logs rotate; `Copy diagnostics` yields the documented report and uploads nothing | `tests/unit/test_cache_policy.py`, `test_settings.py`, `test_artifact_store.py`, `test_preflight.py`, `tests/ui/test_errors.py`; the fatal boxes and close/cancel path are manual |
| **Distribution + licensing** | Every artifact carries `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `LICENSES/`; no weights anywhere; Linux tar.gz/tar.zst/AppImage and Windows ZIP/setup match the naming and budgets; AppImage extracts and self-tests; Windows installs per-user, bootstraps WebView2, and keeps data on uninstall; unsigned build is documented | `generate_notices.py --check`, `verify_bundle.py`, `verify-release.sh`, frozen smoke on both OSes, bundle budgets; the OS matrices are manual |

### Cross-cutting acceptance

- Error contract: every reachable failure has one code, one headline ≤90 characters, and one action; no traceback or raw tool text in the UI; `internal.unexpected` is unreachable in a supported workflow.
- UI: variant A at the 1024×700 minimum; no close prompt or veto; one Job bar; deferred features absent; A/B and playback stay enabled during Jobs.
- Crash/close: closing during a Job cancels it, removes partials, saves settings, and exits 0; a clean launch after a crash shows no banner.
- Warning thresholds: RAM below 8 GB and free disk below 20 GB warn once; per-Job disk checks still enforce.
- `--preflight` exit codes 0/3/4/5/6/7/8 behave as specified; the GUI treats missing preview as degraded mode, not fatal.

---

## 5. Roadmap: phases, gates, dependencies

Sequence only — no calendar estimates. Each phase ends with a **demo**, its **automated gate**, and **no open decisions**. Phases are mergeable vertical slices. The thin frozen-build spine is deliberately in phase 1; the full packaging phase stays last.

| Phase | Build | Exit gate | Depends on |
|---|---|---|---|
| **1. Scaffold + frozen spine** | Repository, `pyproject.toml`/`uv.lock`, `src/stemforge` tree and dependency rule, settings/logging/error taxonomy, `AppPaths`, single-instance lock, preflight skeleton, `--self-test`/`--smoke-test` skeletons, PyInstaller spec skeleton, `freeze_support`, lazy-import AST test, vendor-lock stubs, CI lint/unit/UI, import-linter, `scripts/check.sh` | `ci.yml` green; `lint-imports` passes; the stub frozen build runs `--self-test`/`--smoke-test`; the import-contract test proves no module-scope torch/demucs; no writes outside `AppPaths` | — |
| **2. Load + Preview** | Import Job, FFmpeg decode/probe, canonical WAV, metadata/cover, preview MP3, `/media` route, native dialogs, `ArtifactStore`, cache/LRU, recent files, workspace and empty state, track rail, one player, Remove | Local-load matrix and `/media` HTTP tests green; UI load flow green; preview manual on Linux and Windows; no network in tests | 1 |
| **3. Pitch** | Rubber Band adapter, render sets, gain, manifests, render Job, pitch card, version tabs, A/B, render errors | Pitch integration ±0.15 semitone / 50 ms; instrumental sum; UI pitch flow; manual A/B; no master/stems confusion | 2 |
| **4. Separation** | Worker protocol, model install (pinned + SHA), `--worker separation`, block mode, progress/cancel, Stems/Instrumental, separation UI and model gate, inline errors | Stub-worker protocol tests; `demucs.api` import test; source-run 1 s separation <60 s / <2 GB; UI separation flow; model install unit tests; manual first-run/offline | 2 (sequenced after 3 so the first transform is small) |
| **5. YouTube** | URL parser, yt-dlp adapter, consent/toggle, cache reuse, classifier, clear-YouTube-cache, exact copy | Parser/classifier/adapter tests; UI flow; optional live manual check; zero-network cache-hit test | 2 (can overlap 3–4) |
| **6. Export** | Export Job, encoders, tags/cover, format sheet, save dialog, destination checks, export errors, atomic write | Format round-trips; UI export flow; manual native dialogs both OSes; no partial left on handled failures | 2, 3, 4 |
| **7. Packaging + distribution** | Vendor binaries and builds, PyInstaller specs, Bundle roots, AppImage, ZIP/Inno, WebView2, version resource, notices generator, `release.yml`, `verify-release.sh`, budgets | Frozen `--smoke-test` + `--smoke-separation` on both OSes; AppImage extraction; `verify-release.sh`; budgets; notices/licence tree; draft assets | 1, 6 |
| **8. Hardening + release** | Full OS matrices, Windows measurements (recipe in [16](.scratch/stemforge/issues/16-windows-performance-verification.md)), error-copy audit, crash/close/cancel/offline drills, performance and budget checks, counsel review, 1.0.0 | All §4 criteria and §6 DoD; release gates from [Testing and CI](.scratch/stemforge/issues/11-testing-and-ci-strategy.md) §8; Windows figures recorded or explicitly disclosed; counsel sign-off; tag `v1.0.0` | 1–7 |

**Gate rules**

- A phase gate is the listed automated tests plus the demo on both OSes where the capability is native-only. A failing gate stops the phase; it does not get waived by later work.
- Phase 1's frozen spine proves `freeze_support`, lazy imports, the two Bundle roots, and NiceGUI-as-data early; it does not need torch or the full vendor set.
- Phase 4's source-run separation is the early gate; the frozen separation gate belongs to phase 7, where a real Bundle exists.
- Phase 5 is technically independent of Pitch and Separation; keep it after phase 4 only to preserve a single Job pipeline in development.
- The icon assets are in place at `src/stemforge/assets/` (A — Badge); phase 7's packaging gate consumes them. No open icon decision.

---

## 6. Definition of done

### Source runs

- `uv sync --frozen` succeeds on Linux and Windows; `uv run stemforge` opens the native window on both development OSes.
- `uv run pytest` (unit + UI) and `uv run pytest -m integration` pass; `scripts/check.sh` passes ruff, mypy strict, import-linter, shellcheck, and licensing checks.
- Coverage meets **85% core / 70% overall**; the unit suite is under 60 s; no test writes to a real user profile.
- No module-scope `torch`/`demucs` import outside `workers/separation.py`; `freeze_support()` is the first statement in the main guard.
- No network in tests, no committed audio fixtures, no model weights, no writes outside `AppPaths`, and `STEMFORGE_HOME` fully isolates a run.
- A source run can import, preview, pitch, separate with the model, import from YouTube, export, remove, clear cache, and show diagnostics on both development OSes.

### Linux Bundle

- Built on `ubuntu-22.04`; glibc floor 2.35; `StemForge-<ver>-linux-x86_64.tar.gz` is canonical, with `tar.zst` and a thin zstd AppImage from the same tree.
- `run.sh` opens the native window when host GTK/WebKitGTK/GStreamer are present and falls back to browser mode otherwise; `--preflight` works; `README.txt` gives the per-distro package lines, data paths, `STEMFORGE_HOME`, and the AppImage/FUSE caveat.
- Frozen `--self-test`, `--smoke-test`, and `--smoke-separation` pass on a real Linux host; the AppImage passes `--appimage-extract-and-run --self-test`.
- The Linux manual matrix in [Testing and CI](.scratch/stemforge/issues/11-testing-and-ci-strategy.md) §7 passes on Ubuntu 22.04 (X11 and Wayland), Ubuntu 24.04, Fedora, and Arch, including missing-WebKit and missing-GStreamer paths.
- No model weights, correct notices/source offer, and all bundle budgets met.

### Windows Bundle

- Built on `windows-2022`; portable ZIP and per-user Inno Setup using the same tree; `MinVersion=10.0.19045`; no UAC; WebView2 bootstrapper; `edgechromium` asserted; no `mshtml` fallback.
- `StemForge.exe` has no console, the icon, and the version resource; the installer and ZIP are unsigned with `SHA256SUMS` and the SmartScreen note.
- WebView2 present and absent both work (installer bootstraps silently; ZIP shows the actionable dialog); uninstall keeps `%LOCALAPPDATA%\StemForge` and shows the path.
- Frozen `--self-test`, `--smoke-test`, and `--smoke-separation` pass on the Windows runner and on real hardware.
- The Windows manual matrix passes on Windows 11 x64 and Windows 10 22H2 + ESU: native dialogs, playback, pitch, separation, export, remove, clear cache, logs, About, title/taskbar icons.
- Windows startup/RTF/RSS are measured during hardening and either meet the documented envelope or the limitation is disclosed in the release notes and README; they are not silently treated as passing.

### Release

- The tagged commit is on `main` with `ci.yml` green; `release.yml` `prepare`/`linux`/`windows`/`sources` are green; `publish` waits in the protected `release` environment.
- `verify-release.sh` is green: exact asset set, `SHA256SUMS` covers every asset, every artifact carries the licence tree, no weights anywhere, vendor files match `vendor.lock.json`, signature status recorded.
- Both frozen separations pass (<60 s, <2 GB, four Stems + Instrumental); bundle budgets pass; `docs/qa/release-checklist.md` is complete for both matrices with artifact hashes.
- Version `1.0.0` has counsel sign-off on the weights-download posture and source-offer wording; the tag `v1.0.0` is immutable after publication; the release page carries the source bundles.
- Release notes name the shipped yt-dlp version and the unsigned/SmartScreen caveat. After publication, critical defects get a patch within 48 hours; tags and assets are never moved.

---

## 7. Risks and go/no-go

| Risk | Impact | Mitigation and owner |
|---|---|---|
| Linux host GTK/WebKitGTK/GStreamer dependency (top risk) | Native mode or preview unavailable on older/minimal distros | tar.gz is primary; preflight with exact package lines; browser fallback via `run.sh`; AppImage is thin; degraded preview only. Disclosed limitation, not a release no-go if the fallback works |
| `htdemucs` weights licence unresolved | Cannot bundle; possible legal exposure if redistributed | Never bundle/mirror/host; first-run download at pinned revision + SHA; consent gate; counsel review is a release gate; no offline pack in v1 |
| Windows WebView2/.NET or backend fallback | Blank window (pywebview `mshtml`) | Registry check + startup assertion + bootstrapper; exit 5 with actionable box; ZIP dialog; manual matrix. **Release no-go if the assertion cannot be enforced** |
| Bundle size/build time | GitHub asset limits, slow releases | Budgets in Packaging and distribution plan §9; torch trim; fail thresholds; post-v1 audio-only FFmpeg if needed. **Release no-go above the fail budgets** |
| Frozen-build traps (`freeze_support`, lazy imports, data root) | Fork bomb, missing submodules, late rewrite | Phase-1 spine; AST contract test; `--self-test`/`--smoke-test`; real separation on both OSes |
| yt-dlp breakage | YouTube import fails until an app update | Pinned trio; classifier copy; no runtime updater; patch release. Feature-level failure, not an app no-go |
| Windows performance unmeasured | Envelope may not hold | Measure in phase 8; disclose until then; revise README/limits if missed, do not hide |
| Windows cold vendor builds >25 min | PR CI slow | Fallback per Testing and CI §9: move `integration-windows` nightly, keep `unit-windows` on PR; never drop Windows coverage silently |
| No native-window automation / no live YouTube test | Regressions reach release | Manual matrices on real hosts; pinned NiceGUI and yt-dlp; opt-in live check locally |
| AppImage/FUSE hostility | AppImage won't launch on some hosts | `--appimage-extract-and-run` documented; tar.gz fallback and primary |
| Coverage/threshold noise early | False red CI | Thresholds ratchet upward only; lowering needs a ticket |
| Single release owner | Release bottleneck | Protected `release` environment with the owner as required reviewer; the checklist is the authority |

**Release GO only when:** GPL compliance and counsel sign-off are complete; both bundles pass frozen smoke and the manual matrices; no weights appear in any artifact; bundle budgets pass; every supported-workflow error has copy and an action; `internal.unexpected` is unreachable.

**Release NO-GO:** licence posture unresolved; Windows backend assertion fails; an asset exceeds its fail budget.

**Phase no-go:** the phase's automated gate or demo fails. There are no other global no-gos; Linux host dependencies and unmeasured Windows performance are disclosed limitations.

---

## 8. Post-v1 backlog (unscheduled)

No v1.1 theme is committed. This is the ordered starting point; each item names the seam it uses and the decision it needs. Revisit after 1.0 feedback.

| Order | Candidate | Seam / prerequisite | Decision it would need |
|---|---|---|---|
| 1 | **Waveform display** | Peak/decimation cache beside each preview; a UI component; no architecture change | Whether waveform replaces or supplements the transport scrubber |
| 2 | **Stem mixer** (mute/solo/volume) | Requires synchronized multi-stem playback; `ui.audio` is single-track, so a custom Vue/Web Audio component; ArtifactSet mix semantics | Whether a mix becomes a new Artifact or stays playback-only |
| 3 | **Project library** | Persistent index over manifests (not a second artifact store); conflicts with the ephemeral Workspace | Whether a project is a saved list of Source references or a copied cache |
| 4 | **Batch export** | Multi-track Job pipeline and queue; the one-Job model can stay but the UI and Job model extend | Whether a batch is one Job or a sequence, and how partial failure reports |
| 5 | **GPU separation pack** | Separate torch environment/build, ~1.2–1.5 GB compressed; cannot be a runtime toggle; separate artifact | Whether demand justifies a second bundle and its support surface |
| 6 | **Platform/quality tier** | macOS/ARM, code signing, auto-update, distro packages, i18n, draft/formant toggle, Save original, YouTube thumbnails, native passthrough, audio-only FFmpeg | Each is its own go/no-go; none is assumed |

Architecture seams already exist for 1–5: `SeparationEngine`, `PitchEngine`, `MediaTool`, `YouTubeImporter`, content-addressed manifests, and the one-way dependency rule. No post-v1 item reopens a v1 decision without a new ticket and, where hard to reverse, an ADR.

---

## 9. Open items carried into implementation

- **No open wayfinder tickets** — the map is complete. [App icon art](.scratch/stemforge/issues/17-app-icon-art.md) is resolved (A — Badge; five assets in `src/stemforge/assets/`; rationale in `.scratch/stemforge/prototypes/17-icon/RATIONALE.md`).
- **Windows performance measurement** — the recipe in [16](.scratch/stemforge/issues/16-windows-performance-verification.md) is executed in phase 8. Until then, "Windows figures unmeasured" is a disclosed limitation.
- **Counsel review** — weights-download posture and source-offer wording; a release gate, not a planning blocker.
- **Repository first commit** — `https://github.com/alexmarincu/stemforge`, public from the first commit; lands at implementation start.
- **Release identity** — `__version__ = "1.0.0"`, tag `v1.0.0`; 0.x tags are CI-only.
- **New decisions** — file a new local issue under `.scratch/stemforge/issues/`, wire its `Blocked by:` line, and link it here. Do not silently edit a resolved ticket. If a resolution invalidates a settled decision, append a dated comment to that ticket and record an ADR when the change is hard to reverse and surprising.

---

## 10. Implementation rule of thumb

If a task needs a product decision this handoff does not contain, stop and file it rather than guessing. If it needs an implementation detail inside a settled boundary — a function name, a file split within the package tree, a test fixture — proceed. The boundary is: **product behavior, formats, limits, user-facing copy, and distribution are decided; code-level choices inside the module tree are yours.**
