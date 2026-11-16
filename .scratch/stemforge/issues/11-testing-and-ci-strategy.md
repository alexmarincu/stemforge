# 11 — Testing and CI strategy

**Type:** grilling
**Status:** resolved
**Blocked by:** 06, 10

## Question

Define the test and CI strategy for v1:

- Unit tests for domain and services; audio fixtures; integration tests gated on ffmpeg/Demucs availability; UI smoke tests in native mode (headless constraints); packaging smoke tests. Include a 1-second `demucs.api` import/separation smoke test to catch the missing-numpy release blocker.
- Pitch/media integration tests: Rubber Band R3 render with duration/sample-rate validation; yt-dlp with a mocked downloader (no network in CI); FFmpeg decode/encode round-trips.
- What runs on every PR vs nightly/release; Linux/Windows matrix; caching; publish release assets rather than Actions artifacts.
- Frozen-build smoke tests: `freeze_support()` guard, `nicegui-pack` lazy imports, AppImage `--appimage-extract-and-run`, WebView2 detection; Windows native-window validation needs a self-hosted VM or manual test.
- Deterministic audio assertions (duration, sample rate, channels, non-silence) vs perceptual checks.
- Manual QA checklist for native dialogs, playback, and both OS bundles.
- Release gates and rollback criteria.

The answer must specify test directories, pytest markers, CI jobs, and acceptance thresholds.

## Answer

**One policy: a four-layer suite — unit, integration, UI, and script-driven frozen smoke — with generated audio fixtures and structural/DSP assertions instead of byte hashes; PR CI runs lint, unit on Linux and Windows, real-binary integration on Linux and Windows, UI, licensing, and one Linux frozen build under 15 minutes with no weights; the real 1-second Demucs separation, AppImage extraction, and both frozen bundles run nightly and at release; release is gated by those frozen runs plus the manual OS matrix, and published tags and assets are immutable, so failures are fixed forward with a patch release.**

### 1. Layers, directories, and markers

| Layer | Location | Needs | Runs |
|---|---|---|---|
| **Unit** | `tests/unit/` | No external binaries, no network, no torch | Every PR and locally by default |
| **Integration** | `tests/integration/` | Real FFmpeg, ffprobe, Rubber Band; torch/demucs only for `slow` tests | Every PR on Linux; Windows PR minus `slow`; locally opt-in |
| **UI** | `tests/ui/` | NiceGUI `User` fixture, no browser | Every PR on Linux and locally by default |
| **Smoke** | `packaging/linux/smoke-test.sh`, `packaging/windows/smoke-test.ps1` (not pytest) | A built Bundle; model weights only with `--with-separation` | PR Linux (no weights); nightly and release both OSes |

```text
tests/
├── conftest.py                  # markers, --strict-config, tmp STEMFORGE_HOME, no-network fixture
├── fixtures/
│   ├── audio.py                 # FFmpeg-generated WAV/FLAC/MP3 fixtures + corrupt bytes
│   ├── manifests.py             # minimal source/stems/render manifests
│   ├── fakes.py                 # scripted SeparationEngine/PitchEngine/MediaTool/YouTubeImporter
│   └── stub_worker.py           # scripted JSONL worker for protocol tests
├── unit/
│   ├── test_domain.py           # Source/Track/ArtifactSet invariants
│   ├── test_job_runner.py       # FIFO, dedupe, one-active, watchdog, cancel state machine
│   ├── test_artifact_store.py   # staging/promotion, manifests, lookup, sweep, LRU refcounts
│   ├── test_settings.py         # atomic JSON, corrupt-file recovery, recent-files cap
│   ├── test_cache_policy.py     # 8 GiB cap, LRU unit, live-set protection (ArtifactStore)
│   ├── test_model_install.py    # mocked hf_hub_download: hash match/mismatch/marker
│   ├── test_preflight.py        # check aggregation, WebView2 registry parsing, exit codes
│   ├── test_worker_protocol.py  # JSONL schema, unknown events, v mismatch, exit-code mapping
│   ├── test_frozen_contract.py  # AST: freeze_support first; no module-scope torch/demucs
│   ├── test_scripts.py          # notices, verify_bundle, fetch_sources against fixture trees
│   └── test_test_hygiene.py     # every collected item has exactly one layer marker
├── integration/
│   ├── test_media_ffmpeg.py     # decode 44.1/48/mono/3ch, preview, export round-trips
│   ├── test_pitch_rubberband.py # R3 + formant, ±12/±1, duration/sample-rate/frequency
│   ├── test_instrumental_sum.py # instrumental == drums+bass+other within 1e-3
│   ├── test_youtube_adapter.py  # stubbed YoutubeDL: options, paths, hooks, errors (no network)
│   ├── test_worker_protocol.py  # parent vs stub_worker.py: progress, outputs, crashes, cancel
│   ├── test_media_route.py      # GET/Range/HEAD/416/path traversal against /media
│   ├── test_artifact_store.py   # real temp cache, promote/sweep/reopen
│   └── test_demucs_import.py    # `slow`: demucs.api + numpy + torch CPU, no weights/network
└── ui/
    ├── test_load_flow.py
    ├── test_separation_flow.py  # includes the model gate and one auto-resubmit
    ├── test_pitch_flow.py       # render tabs, A/B, one player at a time
    ├── test_export_flow.py      # format sheet → native-dialog fake → atomic write
    └── test_errors.py           # coded errors, Details, job log path, retry affordance
```

- **Markers:** exactly one of `unit`, `integration`, `ui` per test; `slow` = needs torch/demucs or takes over 30 s; `network` = the single opt-in live YouTube check, never run in CI; `platform_linux` / `platform_windows` for OS-specific tests. `--strict-markers` and `--strict-config` are on. A collect-only check fails CI when a test has no layer marker.
- **Dev dependencies:** `pytest`, `pytest-cov`, `pytest-timeout`, `pytest-asyncio` (`asyncio_mode = "auto"`), and `httpx`, all in the `dev` group and locked; no Selenium, no xdist, no coverage service.
- **Local defaults:** `uv run pytest` runs `unit` + `ui` in under 60 s. `uv run pytest -m integration` runs the real-tool layer. `scripts/check.sh` runs lint, unit, integration, UI, and licensing checks. A missing vendor binary makes integration fail, never skip: CI sets `STEMFORGE_REQUIRE_VENDOR=1`, and the fixture raises instead of returning `pytest.skip`.
- **Seams from 06 are used as designed:** `WorkspaceService` is constructed with a `JobRunner`, `ArtifactStore`, `SettingsStore`, and fakes; unit tests import no NiceGUI and no torch; engine protocols are exercised through their fakes, except the integration tests that deliberately use the real binaries.
- **Isolation:** `tests/conftest.py` sets `STEMFORGE_HOME` to a session temp directory before any `stemforge` import; per-test isolation is a `tmp_path`-rooted `AppPaths` injected into the service. Tests never write to a real user profile.
- **No network in unit tests:** an autouse fixture in `tests/unit/conftest.py` makes `socket.socket.connect` raise. Integration and UI tests may bind localhost (NiceGUI test server, `/media` route); the only `network` test is `tests/integration/test_youtube_live.py`, skipped unless `STEMFORGE_LIVE_YOUTUBE=1`, and it is not in any workflow.
- **Test doubles:** yt-dlp is stubbed at `YoutubeDL` (the adapter's options, progress hooks, cachedir, and error mapping are asserted); model downloads are stubbed at `hf_hub_download`; the worker is a real subprocess running `tests/fixtures/stub_worker.py`, so the parent's parser, exit-code mapping, and cancel path are tested against a process boundary.

### 2. Audio fixtures and assertions

Fixtures are **generated at test time with the real FFmpeg**, never committed, so the repository carries no binary audio:

| Fixture | Purpose |
|---|---|
| 1 s 440 Hz stereo 44.1 kHz WAV | Fast pitch/separation/decode fixture |
| 3 s 440 Hz stereo 44.1 kHz WAV | Pitch duration and A/B checks |
| 3 s 440 Hz stereo 48 kHz WAV | Resample to 44.1 kHz |
| 3 s 440 Hz mono 44.1 kHz WAV | Dual-mono channel handling |
| 3 s sweep 100 Hz–8 kHz stereo | Encode/decode and spectral sanity |
| 1 s silence WAV | Non-silence assertions reject it; export still works |
| Clipped 440 Hz f32 WAV | Presentation-gain and 0 dBFS policy |
| MP3 and FLAC native files | Native-path import and metadata |
| Video-only MP4, truncated WAV, random bytes | `no_audio_stream`, `decode.failed`, unsupported input |

Assertions are deterministic and version-tolerant:

- **Structure:** ffprobe codec (`pcm_f32le`, `pcm_s24le`, FLAC, MP3), sample rate, channel count, and duration within **50 ms** (the same tolerance ticket 05 uses for decode and worker output).
- **Level:** peak dBFS within **±0.1 dB** of the expected value; RMS above a silence floor; mono → dual-mono channels equal within `1e-6`.
- **Pitch:** FFT of a 440 Hz tone with a Hann window and parabolic peak interpolation; expected `440 × 2^(N/12)`, tolerance **±0.15 semitone**; duration within 50 ms; stereo preserved. Cases: `N ∈ {−12, −1, +1, +12}`.
- **Instrumental:** per-sample maximum error **1e-3** against `drums + bass + other`.
- **Export:** WAV 24 / FLAC 24 / MP3 320 codec, bit depth/bitrate, duration, tags from the manifest, cover present in MP3/FLAC, and no sample above 0 dBFS after the shared presentation gain.
- **Preview:** MP3 192, duration within 50 ms, and the `/media` route's Range behavior (below).
- **No byte-for-byte hashes and no golden WAVs.** FFmpeg and Rubber Band builds differ across OSes and versions; the assertions above are the contract. Perceptual checks (does it sound right, does the vocal disappear) are manual QA, never CI.

### 3. UI and native coverage

- **Flow tests use `nicegui.testing.User`**, which runs in the same async context without a browser. Covered: empty state → drag/drop load, track selection, separation model gate and automatic resubmit, progress/cancel, pitch render tabs and A/B, one-player-at-a-time, export format sheet → dialog fake → atomic write, coded error banner with Details and log path.
- **No Selenium `Screen` tests in v1 CI.** Browser/JavaScript-specific behavior (audio element playback, native window chrome) is not worth the flake; it is manual QA. NiceGUI 4.0.0 is pinned, so the `User` API cannot drift under the suite.
- **`/media` route:** an `httpx`/Starlette `TestClient` test asserts GET 200, `Range: bytes=N-` → 206 with a correct `Content-Range`, `HEAD` → 405, suffix (`bytes=-500`) and multi-range → 416, and a traversal attempt → 404. These are the ticket 05 behaviors, tested as HTTP, not through NiceGUI.
- **Native window:** not automatable on GitHub-hosted runners. PR CI covers `--preflight --json` (exit 0 on a Linux runner with the GUI packages installed; schema and check list asserted) and the frozen `--smoke-test` (server boots, `native=False`, HTTP 200 within 30 s). The actual pywebview window, native dialogs, taskbar icon, and WebView2 path are manual QA on both OSes (§7). No xvfb native-window job in v1.
- **Windows backend assertion:** `--preflight --json` on `windows-2022` asserts the JSON schema and that the WebView2/.NET checks are present. Whether WebView2 is installed is a runner property, so the value is recorded, not gated; the installer bootstrapper and the ZIP dialog are manual release checks.

### 4. Heavy stack and frozen-build gates

- **PR Linux, import-only:** `tests/integration/test_demucs_import.py` (marker `slow`) imports `torch`, `numpy`, and `from demucs.api import Separator`; asserts `torch.__version__.endswith('+cpu')`, `demucs.__version__ == '4.1.0'`, and `torchaudio` is absent. This catches the missing-`numpy` release blocker in seconds without weights or network. Windows PRs exclude `slow`; the Windows wheel difference is covered by the Windows frozen nightly.
- **Nightly and release, real separation:** `--smoke-separation` runs a 1-second synthetic WAV through the real worker in the frozen Bundle: four Stems plus Instrumental, exit 0, wall time **< 60 s**, worker peak RSS **< 2 GB** (ticket 12). The smoke module measures peak RSS with `resource.getrusage(RUSAGE_CHILDREN)` on Linux and `GetProcessMemoryInfo` on Windows; no new runtime dependency. CI seeds the weights with `packaging/models/fetch_models.py` from the cached HF directory; the first-run download and consent gate are **manual** QA, not CI.
- **Two consecutive nightly `--smoke-separation` failures block the next release.**
- **Static frozen-contract tests** (`tests/unit/test_frozen_contract.py`): parse `src/stemforge/__main__.py` with `ast` and assert `multiprocessing.freeze_support()` is the first executable statement in the main guard; assert no module outside `src/stemforge/workers/separation.py` imports `torch` or `demucs` at module scope, and that `workers/separation.py` imports them only inside `run()`. This is the regression test for 06's frozen constraints.
- **`--self-test`** imports `torch`, `demucs.api`, `yt_dlp`, `webview`, and `numpy`, asserts the CPU build, and executes every vendor binary; **`--smoke-test`** then boots the NiceGUI server (`native=False`, `show=False`) and polls `/` to HTTP 200 within 30 s. Together they cover `freeze_support()`, the lazy-import ordering, and the `nicegui-pack` data trick far better than a static check.
- **AppImage:** nightly and release run `package.sh`, then the AppImage with `--appimage-extract-and-run --self-test` (and `--preflight --json` on the runner). PR builds no AppImage.
- **Windows native window:** manual only. If a self-hosted Windows VM is added later, it gets its own `windows-native` job; that is post-v1 and not a blocker.
- **Smoke script contract:** `smoke-test.sh` runs `--self-test` + `--smoke-test`; `--with-separation` adds `--smoke-separation`; `--appimage <path>` runs the extraction check. `smoke-test.ps1` is the same with `-WithSeparation`. Both write JSON reports under `build/reports/`, exit nonzero on any failure, and are the only release-gate entry points; no test asserts a frozen behavior through a second code path.

### 5. CI topology, matrix, and caching

**`ci.yml` (every PR and push to `main`):**

| Job | Runner | Content | Ceiling |
|---|---|---|---|
| `lint` | `ubuntu-24.04` | `ruff format --check`, `ruff check`, `mypy`, `lint-imports`, `shellcheck -x`, `uv lock --check` | 5 min |
| `unit-linux` | `ubuntu-24.04` | Lightweight sync (no torch/demucs), `pytest -m unit`, coverage data | 5 min |
| `unit-windows` | `windows-2022` | Same lightweight sync, `pytest -m unit` | 5 min |
| `integration-linux` | `ubuntu-22.04` | Full sync (torch CPU), vendor cache, `pytest -m integration` (includes `slow`), coverage data | 12 min |
| `integration-windows` | `windows-2022` | Lightweight sync, vendor cache (builds Rubber Band/libsndfile on a cold cache), `pytest -m "integration and not slow"`, `Invoke-ScriptAnalyzer -Severity Error`, `--preflight --json` schema check | 25 min cold / 12 min warm |
| `ui-linux` | `ubuntu-24.04` | Lightweight sync, `pytest -m ui`, coverage data | 5 min |
| `licensing` | `ubuntu-24.04` | Full sync, `generate_notices.py --check`, `release.toml` placeholder gate | 5 min |
| `coverage` | `ubuntu-24.04` | Downloads the three `.coverage.*` artifacts, `coverage combine`, enforces the §6 thresholds | 3 min |
| `frozen-linux` | `ubuntu-22.04` | GUI apt deps from 10 §8, vendor cache, `build.sh`, `smoke-test.sh` (no weights, no packaging), `--preflight --json`, logs on failure | **15 min** |

- **No Windows build and no packaging on PRs.** `frozen-linux` is exactly the job ticket 10 delegated: Linux-only, `--self-test` + `--smoke-test`, no weights, under 15 minutes.
- **Lightweight sync:** `uv sync --frozen --no-install-package torch --no-install-package torchaudio --no-install-package demucs`; production code never imports them at module scope, so unit/UI/Windows integration run without the heavyweight stack. `integration-linux`, `licensing`, and `frozen-linux` install the full lock.
- **Caches:** `uv` keyed on `hashFiles('uv.lock')`; vendor keyed on `hashFiles('packaging/vendor.lock.json')`; HF weights only in jobs that run `--smoke-separation`, keyed on `hashFiles('packaging/models/models.lock.json')`. Never cache `build/`, `dist/`, or `.venv`. PRs restore the default-branch caches; a cold Windows vendor build is accepted, with the fallback in §9.
- **Release assets are not Actions artifacts.** Release files go only to the draft GitHub Release via `release.yml` (10). Actions retention is 7 days for failure logs, JSON smoke reports, and coverage HTML only.
- **No xdist and no retries.** Audio timing and the single-instance lock are sensitive to parallelism; tests run in one process per job.

**`nightly.yml` (03:00 UTC, `schedule` + `workflow_dispatch`):**

| Job | Runner | Content | Ceiling |
|---|---|---|---|
| `linux-frozen-separation` | `ubuntu-22.04` | Full build, `fetch_models.py`, `smoke-test.sh --with-separation`, `package.sh`, AppImage `--appimage-extract-and-run --self-test`, `--preflight --json` | 35 min |
| `windows-frozen-separation` | `windows-2022` | Full build, `fetch_models.py`, `smoke-test.ps1 -WithSeparation` | 50 min |

- Nightly runs on the same images as release (that is the point), not the `-next` images; image drift stays visible through `release.yml`'s non-blocking `linux-next`/`windows-2025` jobs.
- `release.yml` is 10's (`prepare`/`linux`/`windows`/`sources`/`publish` plus the non-blocking next-image jobs), with the §8 protected-environment amendment and the post-`package.sh` AppImage extraction check. Its `linux`/`windows` jobs already run `--with-separation`; the new gates here are the nightly policy, the extraction check, and the manual checklist.

### 6. Quality gates and thresholds

| Gate | Threshold | Where |
|---|---|---|
| Ruff format/check | Zero findings; `target-version = "py313"`, line length 100, rules `E,F,I,UP,B,SIM,PT,RUF` | `lint` |
| mypy | Strict on `src/stemforge`; `ui/` uses `check_untyped_defs` and allows untyped defs; no untyped `ignore` | `lint` |
| Import contracts | The 06 §1 dependency rule passes `lint-imports`; `ui` never imports `engines`/`workers`/`pipeline`/`jobs` | `lint` |
| Shell scripts | `shellcheck -x` clean on `packaging/**/*.sh` and `scripts/**/*.sh` | `lint` |
| PowerShell | `Invoke-ScriptAnalyzer -Severity Error` clean on `packaging/windows/*.ps1` | `integration-windows` |
| Coverage, core | **≥ 85% line** on `domain`, `jobs`, `artifacts`, `pipeline`, `engines`, `settings`, `application`, `worker_protocol`, `errors` | `coverage` |
| Coverage, overall | **≥ 70% line** over `src/stemforge`, excluding `ui/` and `workers/` | `coverage` |
| Unit suite wall | < 60 s | local + CI |
| PR wall | < 30 min | `ci.yml` |
| Frozen PR job | < 15 min | `frozen-linux` |
| Frozen startup | `--smoke-test` HTTP 200 < 30 s; release reference host ≤ 4 s to first paint, > 8 s fails the release (10 §9) | nightly + manual |
| 1 s separation | < 60 s, < 2 GB worker peak RSS | nightly + release |
| Bundle budgets | 10 §9 table (onedir ≤ 1.15 GB, tar.gz ≤ 450 MB, ZIP ≤ 450 MB, setup ≤ 380 MB, etc.) | release |
| Flake | Two failures of the same test in a week → quarantine with an issue; fix or delete; never a silent skip | any |

- **Coverage is combined, not per-job:** `unit-linux`, `integration-linux`, and `ui-linux` write `.coverage.<job>` and upload it; `coverage` combines and runs two `coverage report` commands with `--include`/`--omit` and `--fail-under`. No third-party coverage service.
- **Licensing/script tests:** `tests/unit/test_scripts.py` feeds fixture `uv.lock`, dist-info, and manifests to `generate_notices.py`, `verify_bundle.py`, and `fetch_sources.py`, covering missing notices, placeholder URLs, a bundled `*.safetensors`, a missing licence tree, and a checksum mismatch. The real `generate_notices.py --check` runs on every PR; `verify-release.sh` runs at release.
- **No mutation testing, no coverage badge, no Codecov.**

### 7. Manual QA checklist

`docs/qa/release-checklist.md` is the single checklist; it is filled into the draft release for every version and linked from the release notes. Evidence: pass/fail per row, OS build, date, tester, app version, and each artifact's SHA-256.

**Linux matrix** — Ubuntu 22.04 (X11 and Wayland), Ubuntu 24.04 (Wayland + NVIDIA), Fedora current, Arch current:

- `run.sh` opens the native window; `--preflight --json` is clean on a fully provisioned host.
- Drag-and-drop WAV, MP3, FLAC, 48 kHz, mono, and a multichannel file; the downmix notice appears.
- YouTube import: one public, non-age-gated video ≤ 15 min; consent modal once; disclaimer line; a playlist/channel/search URL fails as a field error; "Clear YouTube cache" removes only `<cache>/yt-dlp/`.
- Preview: play, seek, and switch Tracks; only one player sounds at a time.
- Pitch: +12 and −12, A/B between Original and the newest render, version tabs, and the `master`/`stems` suffixes.
- Separation: first-run model download and consent; cancel mid-Job; the model-missing gate and resubmit; per-stem playback; Instrumental matches the non-vocal mix.
- Export: WAV 24, FLAC 24, and MP3 320 through the native save dialog, with correct extension handling and filters; probe each exported file; tags and cover art are present.
- Remove Track deletes cached audio after confirmation; Clear cached audio leaves the live Workspace intact; recent files behave as 07 defines.
- Logs exist under the 07 paths; About shows licence, notices, source offer, and the licences folder.
- AppImage with and without FUSE (`--appimage-extract-and-run`); browser fallback when WebKitGTK or GStreamer is missing; Wayland and X11; the 2.52+ NVIDIA/Skia workaround note.
- Offline: launch, local import, and Separation with the model already installed; model missing + offline shows the actionable error.

**Windows matrix** — Windows 11 x64 fresh, Windows 10 22H2 + ESU:

- Inno installer: per-user, no UAC, Start Menu shortcut (desktop optional), `MinVersion`, WebView2 present and absent (bootstrapper runs silently), optional launch.
- Portable ZIP: WebView2 absent shows the message box with the download link, not a blank window.
- SmartScreen wording: unsigned build, "Unknown publisher", `SHA256SUMS` is the integrity check.
- Native dialogs: open/save filters, extension normalization, overwrite confirmation; no console window.
- Playback, pitch, separation, export, remove, clear cache, logs, About — the same rows as Linux.
- Title bar, taskbar, Start Menu, and Alt-Tab icon at 16/20/24/32/48/256 px.
- Uninstall keeps `%LOCALAPPDATA%\StemForge` and shows the data path; reinstall reuses it.
- `--preflight --json` on a clean VM; WebView2/.NET failure exits 5 with the actionable message.

**Both OSes, once per release:** close the window during a running Job (no prompt, no veto, partials removed, settings saved); corrupt-file and unsupported-file errors; disk-space warning; job log path from the error banner.

### 8. Release gates and rollback

**Publish only when all of these are true:**

1. The tagged commit is on `main` with `ci.yml` green.
2. `release.yml`'s `prepare`, `linux`, `windows`, and `sources` are green; `publish` is waiting on the protected environment; the non-blocking next-image jobs have been read.
3. `verify-release.sh` is green: exact asset set, `SHA256SUMS` covers every asset, every artifact carries `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `LICENSES/`, no `*.safetensors`/weights anywhere, every vendor file matches `vendor.lock.json`, and the signature status is recorded.
4. Both frozen `--smoke-separation` runs pass (< 60 s, < 2 GB, four Stems + Instrumental).
5. The 10 §9 bundle budgets pass; the startup measurement meets the ≤ 4 s target on the reference host, and a reading above 8 s fails.
6. `docs/qa/release-checklist.md` is complete for the Linux and Windows matrices, with artifact hashes.
7. Version `1.0.0` also has counsel sign-off (13).

**Workflow gate:** the `publish` job runs in the protected `release` environment with the release owner as a required reviewer. A tag push builds and verifies the draft; the manual matrix runs against the draft assets; only an approval un-drafts it. This is the one-line amendment to 10's `release.yml` that makes the manual gate real rather than procedural, and it is recorded as a comment on 10.

**Rollback:**

- **Before publication:** delete the tag and the draft release, fix, and re-tag. 18's immutability starts at publication.
- **After publication:** never delete or move the tag or its assets — the GPLv3 §6(d) source offer and the three-year written offer point at them. Edit the release notes to mark the release known-bad with the reason and a link to the fix, and publish a patch. Critical defects (data loss or corruption, failure to launch on a supported OS, corrupt exports, a security exposure, or a licence-compliance failure) get a patch within 48 hours; lesser defects wait for the next patch.
- **No auto-update and no telemetry:** release notes, README, and a pinned issue are the only notification channels.
- If the source-offer contact or repository URL is broken, fix the repository, not the published tag.

### 9. Downstream, risks, and contingency

- **14** inherits the harness: its failure-mode matrix adds cases under `tests/integration/` and `tests/ui/`, using the same `STEMFORGE_HOME`, fakes, and coded-error assertions. No new test infrastructure is needed.
- **15** inherits the gates and thresholds above, the nightly policy, and the manual checklist.
- **10**'s `ci.yml` ownership is satisfied: `frozen-linux` is the Linux-only frozen smoke under 15 minutes with no weights, and Windows packaging stays off PRs.
- **Risks and responses:**
  - GitHub-hosted Windows cold vendor builds may exceed 25 minutes. If two consecutive cold runs do, move `integration-windows` to nightly and keep `unit-windows` on PR; do not silently drop the Windows native-binary coverage.
  - `--smoke-separation` gate is 60 s on CI runners; the nightly records the actual number. If a runner cannot meet it for infrastructure reasons, the reference-host gate remains the release gate and the runner number is disclosed, not averaged away.
  - No native-window automation and no live YouTube test in CI. Both are accepted v1 limitations, covered by the manual checklist; yt-dlp is pinned and its release notes name the shipped version.
  - Coverage thresholds may be noisy in the first weeks; they ratchet upward only. Lowering a threshold needs a ticket.
- **No new ADR.** This is a reversible CI policy recorded here; the architecture, packaging, and licensing ADRs are unchanged.

### 10. Files and configuration to create in the implementation effort

| Path | Purpose |
|---|---|
| `tests/conftest.py`, `tests/fixtures/{audio,manifests,fakes,stub_worker}.py` | Fixtures, isolation, fakes, stub worker |
| `tests/unit/**`, `tests/integration/**`, `tests/ui/**` | The layers and markers above |
| `pyproject.toml` `[tool.pytest.ini_options]`, `[tool.coverage.*]`, `[tool.mypy]`, `[tool.ruff]`, `[tool.importlinter]` | Marker registration, `--strict-*`, thresholds, typing, lint, contracts |
| `.github/workflows/ci.yml` | The PR jobs above |
| `.github/workflows/nightly.yml` | The nightly frozen separation jobs |
| `scripts/check.sh` | Local full pre-PR run |
| `docs/qa/release-checklist.md` | The manual matrix |
| `packaging/linux/smoke-test.sh`, `packaging/windows/smoke-test.ps1` | Owned by 10; behavior fixed in §4 |
| `packaging/models/fetch_models.py` | Owned by 10; seeds the HF cache for nightly/release smoke |
