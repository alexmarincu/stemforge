# 10 — Packaging and distribution plan

**Type:** grilling
**Status:** resolved
**Blocked by:** 04, 06, 13

## Question

Lock the v0.1.0 packaging and distribution plan:

- Final toolchain: PyInstaller 6.22 `--onedir` (research 04 recommendation), native builds per OS; one-dir bundle layout; embedded Python; placement of torch, ffmpeg, ffprobe, Rubber Band CLI, yt-dlp, Deno, and model weights.
- Build entry point: custom PyInstaller `.spec` (needed for torch trimming, vendor binaries, hooks, and Windows `console=False`) that reproduces `nicegui-pack`'s default NiceGUI-as-data entry; keep `nicegui-pack --onedir` only as an optional environment smoke build.
- Build images: `ubuntu-22.04` (glibc 2.35) and `windows-2022`; Windows 11 primary with Windows 10 22H2 + ESU best-effort; release assets, not Actions artifacts.
- Torch trimming: remove `torch/test` and `torch/bin` (162 MB measured), validated by a real separation smoke test.
- Bundled binary provenance: FFmpeg 9.0.2 GPLv3 (Linux BtbN 145.6 MB archive; Windows gyan essentials 34 MB), Rubber Band 4.0.0 CLI, yt-dlp/Deno; SHA256 pins, mirrored release assets, and the GPL source-offer plan.
- Model weights: bundle the 84 MB `htdemucs` weights for a fully offline installer vs first-run download to avoid redistributing weights with an unresolved licence; checksums/verification, HF cache seeding, and offline guarantee. Depends on licensing (13).
- Linux: tar.gz layout and launcher dependency checks; AppImage shape (thin host-dependent wrapper vs tar.gz-only) given that bundling WebKitGTK/GStreamer is a documented failure mode; glibc baseline; Wayland/X11 notes.
- Windows: portable ZIP, Inno Setup installer, WebView2 bootstrapper, unsigned-build warnings, and a backend assertion at startup.
- Startup preflight requirements (WebView2 presence, GStreamer plugins, webkit typelib) and how the installer communicates them.
- CI matrix, caching, artifact naming/versioning, and release checklist.
- Bundle-size and build-time budget; what gets trimmed.
- Code-signing decision (likely unsigned v0.1.0) and SmartScreen guidance.

The answer must name the exact scripts and files to create in the implementation effort.

## Answer

**Recommendation: PyInstaller 6.22.3 `--onedir` on CPython 3.13, built natively on `ubuntu-22.04` and `windows-2022`; the frozen payload in `_internal/` and every vendored binary in `vendor/` beside it; Linux `tar.gz` canonical plus `tar.zst` and a thin zstd AppImage; Windows portable ZIP plus a per-user Inno Setup installer; unsigned v0.1.0 with `SHA256SUMS`; version `0.1.0` tagged `v0.1.0`; release assets uploaded straight to a draft GitHub Release, never Actions artifacts.**

### Decisions settled in this ticket's round

| # | Question | Decision |
|---|---|---|
| 1 | Repository home | `packaging/release.toml` is the only place the URLs live: `https://github.com/alexmarincu/stemforge` (ticket 18). `prepare` and `verify-release.sh` fail on `CHANGEME`/`example.com`, so a placeholder can never reach a tag. |
| 2 | Versioning | Semver in `src/stemforge/__init__.py::__version__`, mirrored into `pyproject.toml` as a dynamic attribute; first public release `0.1.0`, tags `v0.1.0`; `0.x` tags are CI-only and never published. |
| 3 | Linux artifacts | `tar.gz` (canonical) + `tar.zst` + a thin zstd AppImage from the same onedir. No GTK/WebKitGTK/GStreamer bundling. No deb/rpm/Flatpak. |
| 4 | Windows | Windows 11 x64 primary, Windows 10 22H2 + ESU best-effort (`MinVersion=10.0.19045`); per-user install, no UAC; the uninstaller keeps user data. |
| 5 | Code signing | Unsigned v0.1.0 with `SHA256SUMS` and a SmartScreen note; signing hooks exist but are off by default. |
| 6 | Freshness | No auto-update, no version check, no phone-home. Best-effort patch releases; release notes name the shipped `yt-dlp` version. |
| 7 | Bundle layout | Vendor binaries and the licence tree live at the Bundle root, outside `_internal` (ADR [0010](../../../docs/adr/0010-vendored-binaries-live-outside-the-frozen-payload.md)). |
| 8 | Linux launch | `run.sh` + `--preflight`; browser fallback when GTK/WebKitGTK/GStreamer are missing; WebKitGTK floor 2.44; `STEMFORGE_BROWSER=1` escape hatch. |
| 9 | CI | `release.yml` on tags with `prepare`/`linux`/`windows`/`sources`/`publish`; ticket 11 owns `ci.yml`, whose packaging job is a Linux-only frozen smoke under 15 minutes; the Windows native window is a manual release check (ticket 16's measurement recipe is post-handoff). |
| 10 | Icon | The asset contract is fixed here; final art is ticket 17, not a packaging blocker. |

**Confirmed, not re-opened:** CPU-only `torch 2.15.0+cpu`, no GPU artifact in v0.1.0 (02, 12); `htdemucs` weights downloaded on first use, never bundled (13, ADR 0009); no auto-update; no persistent library.

### 1. Toolchain, entry point, and version

- **Python 3.13** (`requires-python = ">=3.13,<3.14"`), one `uv.lock` (06), CPython patch pinned by CI (`python-version: '3.13'`).
- **PyInstaller 6.22.3** + `pyinstaller-hooks-contrib 2026.10`, native per OS, in a `[dependency-groups] build` group in `pyproject.toml` and locked by `uv.lock` (`uv sync --frozen --group build`). No Nuitka, Briefcase, or cx_Freeze.
- **Release build entry point:** `python -m PyInstaller packaging/pyinstaller/stemforge_{linux,windows}.spec --noconfirm --clean`. `nicegui-pack --onedir` is only a local environment smoke build; the spec is the release path because it alone can trim torch, place vendor files, add the GI hooks, and set Windows `console=False`.
- **App entry point:** `src/stemforge/__main__.py` (06). `freeze_support()` is the first statement in the main guard; module-level `app.native.*` config sits outside it.
- **Version:** `src/stemforge/__init__.py` holds `__version__ = "0.1.0"`; `pyproject.toml` declares a dynamic version read from that attribute (the build backend's `attr` mechanism), so there is exactly one source. `prepare` asserts `v<tag>` equals that value; `package.ps1` passes it to Inno as `/DVersion=`; About reads `stemforge.__version__` directly, so no `dist-info` metadata is needed at runtime.
- **Torch source:** a `[[tool.uv.index]]` entry marked `explicit = true` for `https://download.pytorch.org/whl/cpu`, with `torch==2.15.0`; `build.sh`/`build.ps1` assert `torch.__version__.endswith('+cpu')` and fail the build otherwise (a PyPI fallback silently pulls CUDA wheels).
- **Lazy imports vs static analysis:** 06 keeps torch/Demucs imports inside `workers/separation.py::run()`. Research 04 measured that function-scope imports were dropped by PyInstaller in one configuration, so the specs must list the heavy stack explicitly in `hiddenimports` (`torch`, `demucs`, `demucs.api`, `demucs.pretrained`, `demucs.apply`, `sphn`, `lameenc`, `huggingface_hub`, `safetensors`, `numpy`, `yt_dlp`, `webview`, plus `collect_submodules` for `nicegui`, `demucs`, `yt_dlp`). The release gate is the frozen `--self-test` and `--smoke-separation`, not the presence of the files.

### 2. Bundle layout

Linux (and the AppDir payload):

```text
StemForge-0.1.0-linux-x86_64/
├── StemForge                        # PyInstaller onedir launcher, name='StemForge'
├── _internal/                       # frozen CPython, torch, Demucs, NiceGUI, yt-dlp, assets,
│                                    #   models/models.lock.json
├── vendor/
│   ├── ffmpeg/{ffmpeg,ffprobe}
│   ├── deno/deno
│   └── rubberband/{rubberband,libsndfile.so.1}   # rpath $ORIGIN
├── LICENSES/                        # 13 §9 tree, single copy
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── SOURCE_OFFER.md
├── README.txt
└── run.sh                           # executable
```

Windows:

```text
StemForge-0.1.0-win64/
├── StemForge.exe                    # console=False, icon, version resource
├── _internal/
├── vendor/
│   ├── ffmpeg\{ffmpeg.exe,ffprobe.exe}
│   ├── deno\deno.exe
│   └── rubberband\{rubberband.exe,sndfile.dll}   # loader searches the exe directory
├── LICENSES\
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── SOURCE_OFFER.md
├── README.txt
└── MicrosoftEdgeWebview2Setup.exe   # portable ZIP only, ~2 MB, unmodified Microsoft redistributable
```

The Inno installer installs the same tree under `{localappdata}\Programs\StemForge`; it embeds the bootstrapper as a `dontcopy` file instead of shipping it visibly.

### 3. Resource-resolution amendment to 06

06's rule stays "no `__file__` traversal in frozen code", but frozen resources gain a second root:

| Root | Frozen | Development |
|---|---|---|
| **Data root** (`_MEIPASS`) | `Path(sys._MEIPASS)` | repo root |
| **Bundle root** | `Path(sys.executable).resolve().parent` | repo root |

`platform/resources.py` resolves `assets/`, `models/models.lock.json`, and NiceGUI package data from the data root; `platform/binaries.py` resolves `vendor/**` and the licence files from the Bundle root. In development both are the repo root, so there is one rule per artifact class, not per mode. The separation worker (`sys.executable --worker separation`) inherits the Bundle root automatically. `scripts/licensing/verify_bundle.py` uses the same rule.

This replaces 13 §9's "plus `_internal/` copies as PyInstaller data" for the licence tree: the Bundle root copy is the only copy, and both the About modal and the verifier read it. See the amendment comment on 13.

### 4. Vendored binaries: provenance, build, placement

| Component | Pin | How it is obtained | Installed at |
|---|---|---|---|
| `ffmpeg`, `ffprobe` | BtbN GPL static `n9.0.2-57-gd838f9f9ae`, tag `autobuild-2026-11-15-12-40`; per-OS archive SHA-256 in `packaging/vendor.lock.json` | Download, verify archive SHA-256, extract only `bin/ffmpeg[.exe]`, `bin/ffprobe[.exe]`, `LICENSE`/`COPYING`; record `ffmpeg -version` + `-buildconf` into `LICENSES/ffmpeg/BUILDINFO.txt`. Drop `ffplay`, docs, and presets. | `vendor/ffmpeg/` |
| Deno | 2.9.8, per-OS zip SHA-256 | Download, verify, extract `deno[.exe]`. The downloader is the `yt-dlp` Python wheel, not its standalone binary. | `vendor/deno/` |
| Rubber Band CLI | 4.0.0, commit `1d95888b`, tag archive SHA-256 `24300f48…d266` | Meson release build from source with builtin FFT/resampler (no FFTW, no libsamplerate); CLI only, no LV2/VST; `strip`; Linux rpath set to `$ORIGIN`. | `vendor/rubberband/rubberband[.exe]` |
| libsndfile | 1.2.2, tarball SHA-256 `3799ca99…b80e` | CMake release build, WAV-only, shared, programs/examples/tests off, external libs off; `strip`. | Beside Rubber Band: `libsndfile.so.1` / `sndfile.dll`. The two platforms' loaders only search the executable's own directory (Windows) or the rpath (Linux), so co-location is deliberate; the files remain separately replaceable and `LICENSES/libsndfile/` documents them. |
| `yt-dlp`, `yt-dlp-ejs` | `yt-dlp==2026.10.26`, `yt-dlp-ejs==0.8.0` | Python wheels in the frozen payload; EJS JS data via `collect_data_files('yt_dlp_ejs')`; `collect_submodules('yt_dlp')`. | `_internal/` |
| WebView2 Evergreen Bootstrapper | Microsoft redistributable | Download, verify SHA-256, ship unmodified; notice next to the installer and in `THIRD_PARTY_NOTICES.md`; never signed with the project certificate. | Windows installer + portable ZIP |
| `htdemucs` weights | HF `adefossez/HTDemucs`, pinned revision + SHA-256 (ADR 0009) | **Not shipped.** `packaging/models/models.lock.json` + `fetch_models.py` serve local/CI smoke runs only; the app downloads at first Separation. | `<data>/models/` at runtime |

`packaging/vendor.lock.json` is the machine-readable pin list (URL, archive SHA-256, extracted file SHA-256, licence) consumed by both vendor scripts and the CI cache key; ticket 13's inventory remains the prose record. `packaging/vendor.sh` and `packaging/vendor.ps1` orchestrate the four component scripts and are idempotent (skip when the file SHA-256 matches).

### 5. Linux distribution

**Artifacts (all release assets):**

| File | How | Notes |
|---|---|---|
| `StemForge-<ver>-linux-x86_64.tar.gz` | `tar -C dist -czf` over the assembled tree | Canonical Linux artifact |
| `StemForge-<ver>-linux-x86_64.tar.zst` | same tree, `zstd -19 -T0` | ~150 MB smaller than gzip; same bytes after extraction |
| `StemForge-<ver>-linux-x86_64.AppImage` | `ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 appimagetool --comp zstd AppDir …` | Thin wrapper, no host GUI libraries inside |

- **glibc baseline 2.35**, built on `ubuntu-22.04`. If that runner is retired, build inside an `ubuntu:22.04` container on `ubuntu-24.04`; never let the runner image raise the floor.
- **No GTK/WebKitGTK/GStreamer bundling** (ADR [0011](../../../docs/adr/0011-the-linux-appimage-is-a-thin-host-dependent-wrapper.md)). Host requirements for native mode: `libgtk-3-0`, `libwebkit2gtk-4.1-0` (2.44+), `gir1.2-webkit2-4.1`, `gstreamer1.0-plugins-good` (plus `-base`).
- **`run.sh`** is deliberately thin because the probe lives in Python, not in shell:

  ```sh
  #!/bin/sh
  set -eu
  here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  if "$here/StemForge" --preflight --quiet; then
      exec "$here/StemForge" "$@"
  fi
  "$here/StemForge" --preflight || true
  echo "StemForge: native window unavailable; starting in browser mode." >&2
  STEMFORGE_BROWSER=1 exec "$here/StemForge" "$@"
  ```

  It never exports `GST_PLUGIN_SYSTEM_PATH*` (an empty value disables GStreamer's default plugin search) and never sets `GDK_BACKEND` (Wayland stays native; `GDK_BACKEND=x11` is documented only as a troubleshooting override).
- **AppImage AppDir:** `AppRun` → `$APPDIR/usr/lib/stemforge/run.sh`; `usr/bin/stemforge` → `../lib/stemforge/run.sh`; `usr/share/applications/stemforge.desktop` (`Exec=stemforge`, `Terminal=false`, `Categories=AudioVideo;Audio;`, `StartupWMClass=StemForge`); `usr/share/icons/hicolor/scalable/apps/stemforge.svg` + `.DirIcon`; `usr/share/doc/stemforge/` gets copies of `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/`.
- **No FUSE on the host:** documented `--appimage-extract-and-run`; the tar.gz is the fallback and the primary artifact.
- **WebKitGTK floor 2.44.** Preflight blocks native mode below it. On 2.52+ it allows the window but prints the Skia/NVIDIA workarounds (`WEBKIT_SKIA_ENABLE_CPU_RENDERING=1`, `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`) and `STEMFORGE_BROWSER=1`.
- **`README.txt`** carries: extract-and-run steps, the per-distro package line for Debian/Ubuntu, Fedora, and Arch, exactly what degrades without each dependency, data locations (07), `STEMFORGE_HOME`, `STEMFORGE_BROWSER`, the AppImage caveat, and the licence/source pointers.

### 6. Windows distribution

- **Build on `windows-2022`**; `windows-2025` runs a non-blocking smoke job so the next image migration is caught early.
- **Portable ZIP** `StemForge-<ver>-win64-portable.zip`: the same tree, no installer, no `run.cmd`; `StemForge.exe` performs the WebView2/.NET check itself and shows a Win32 message box with the download link instead of opening a broken window.
- **Inno Setup** `StemForge-<ver>-win64-setup.exe` (Inno 6.x), key directives:

  ```ini
  [Setup]
  AppId={{7E2F6C2A-...}}
  AppName=StemForge
  AppVersion={#Version}
  DefaultDirName={localappdata}\Programs\StemForge
  PrivilegesRequired=lowest
  ArchitecturesAllowed=x64compatible
  ArchitecturesInstallIn64BitMode=x64compatible
  MinVersion=10.0.19045
  Compression=lzma2/max
  SolidCompression=yes
  DiskSpanning=yes
  DiskSliceSize=max
  WizardStyle=modern
  SetupIconFile=..\..\src\stemforge\assets\stemforge.ico
  UninstallDisplayIcon={app}\StemForge.exe
  OutputBaseFilename=StemForge-{#Version}-win64-setup
  ```

  `[Icons]` Start Menu shortcut always, desktop icon as an unchecked task; `[Run]` optional "Launch StemForge"; `CloseApplications=yes`; `[UninstallDelete]` covers program files only. `CurUninstallStepChanged(usPostUninstall)` shows the data folder path so the user can delete it deliberately.
- **WebView2:** `[Code]` checks `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` and the `HKCU` equivalent; if absent, `ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe')` then `Exec(…, '/silent /install')` and wait. The app repeats the check at startup for the ZIP case.
- **Backend assertion:** before `ui.run(native=True)`, the Windows path reads the WebView2 version and the .NET Framework release key (`NDP\v4\Full\Release` ≥ 394802). On failure it shows an actionable message box and exits 5 — pywebview's silent `mshtml` fallback must never reach the user as a blank "Vue requires Chrome 89+" window.
- **`version_info.txt`** gives `StemForge.exe` real file properties (ProductName, FileVersion, LegalCopyright, GPL-3.0-only).
- **Signing:** `sign.ps1` is wired but off. When `STEMFORGE_SIGN=1`, it builds the Azure Artifact Signing metadata JSON from env vars, signs `StemForge.exe` and any first-party DLLs before packaging, then Inno's `SignTool` signs setup and uninstaller; verification is `signtool verify /pa /all`. Third-party binaries are never signed with the project certificate.
- **SmartScreen:** README and release notes state that v0.1.0 is unsigned, that Windows may show "Unknown publisher", and that `SHA256SUMS` is the integrity check.

### 7. Diagnostics flags and startup preflight

All flags are handled in `__main__.py` after `freeze_support()` and `platform.environment.configure()` and before any UI import. `--report <path>` writes a JSON object; exit codes are authoritative (Windows has no console).

| Flag | Behaviour | Exit codes |
|---|---|---|
| `--preflight [--quiet] [--json] [--report P]` | Checks platform/arch, Bundle root and vendor binaries (`--version`), data/cache/config writability, free space (warn < 20 GB, fail < 2 GB), RAM (warn < 8 GB), Linux `gi`/Gtk 3.0/WebKit2 4.1/Soup 3.0 + WebKitGTK version + GStreamer elements (`wavparse`, `mpg123audiodec`, `autoaudiosink`), Windows WebView2 + .NET. Prints the per-distro package line on failure. | 0 ok; 3 native window unavailable; 4 audio preview unavailable; 5 WebView2/.NET missing; 6 storage fatal; 7 unsupported platform |
| `--self-test [--report P]` | Imports `torch`, `demucs.api`, `yt_dlp`, `webview`, `numpy`; asserts `torch.__version__.endswith('+cpu')`, `torchaudio` is absent, vendor binaries exist and execute. No window. | 0/1 |
| `--smoke-test [--report P]` | `--self-test`, then starts the NiceGUI server (`native=False`, `show=False`), self-polls `/` to HTTP 200 within 30 s, exits. No display needed. | 0/1 |
| `--smoke-separation [--report P]` | `--smoke-test`, then a 1-second synthetic WAV through the real worker: asserts four stems, records wall time and peak RSS. Gate: < 60 s, < 2 GB RSS (12). | 0/1 |
| `--worker separation …` | Existing 06 dispatch, unchanged. | worker protocol |

`platform/preflight.py` implements the checks; `src/stemforge/smoke.py` (new, 06 amendment) implements the three test flags. The preflight runs on every normal launch; `run.sh` only decides what to do with its exit code.

### 8. CI, artifact naming, and release process

**`release.yml`** (tag `v*`, `workflow_dispatch`):

| Job | Runner | Timeout | Steps |
|---|---|---|---|
| `prepare` | `ubuntu-24.04` | 10 | checkout; assert tag == `v{__version__}`; assert no `CHANGEME`/`example.com` in `release.toml`; create draft release (`gh release create … --draft`) |
| `linux` | `ubuntu-22.04` | 45 | checkout; Python 3.13 + uv cache; apt deps (`libgtk-3-0`, `libwebkit2gtk-4.1-0`, `gir1.2-webkit2-4.1`, `libgirepository1.0-dev`, `libcairo2-dev`, `pkg-config`, `patchelf`, `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`, `meson`, `ninja-build`, `cmake`, `libfuse2`, `fuse`, `desktop-file-utils`, `librsvg2-dev`, optional `xvfb`); `packaging/vendor.sh` (cached); `packaging/linux/build.sh` (assembles and runs `verify_bundle.py`); `generate_notices.py --check`; `smoke-test.sh --with-separation`; `package.sh`; upload to draft |
| `windows` | `windows-2022` | 60 | checkout; Python 3.13 + uv cache; `packaging\vendor.ps1` (cached; MSVC builds Rubber Band/libsndfile); `packaging\windows\build.ps1` (assembles and runs `verify_bundle.py`); `smoke-test.ps1 -WithSeparation`; `package.ps1`; upload to draft |
| `sources` | `ubuntu-24.04` | 30 | full checkout; `fetch_sources.py`; `git archive` → `StemForge-<ver>-src.tar.gz`; `third-party-sources.tar.zst`; upload to draft |
| `publish` | `ubuntu-24.04` | 15 | download/collect assets; `verify-release.sh`; final `SHA256SUMS`; `gh release edit --draft=false` |
| `linux-next` / `windows-next` | `ubuntu-24.04` / `windows-2025` | 30/45 | build + `--self-test` only, `continue-on-error: true` |

- **Caches:** uv (`${{ runner.os }}-uv-${{ hashFiles('uv.lock') }}`), vendor (`…-vendor-${{ hashFiles('packaging/vendor.lock.json') }}`), HF weights only for jobs that run `--smoke-separation` (`…-hf-${{ hashFiles('packaging/models/models.lock.json') }}`). Never cache `build/`.
- **Permissions:** `contents: write` on `release.yml`; `id-token: write` is added only when signing is enabled. Both workflows run on GitHub-hosted runners only.
- **`ci.yml`** is ticket 11's. The packaging job it calls is: `ubuntu-22.04`, vendor from cache, `build.sh`, `smoke-test.sh` (`--self-test` + `--smoke-test`, no weights, no packaging), upload logs on failure. Target under 15 minutes; no Windows build on PRs.
- **Asset naming:** `StemForge-<ver>-linux-x86_64.{tar.gz,tar.zst,AppImage}`, `StemForge-<ver>-win64-portable.zip`, `StemForge-<ver>-win64-setup.exe`, `StemForge-<ver>-src.tar.gz`, `StemForge-<ver>-third-party-sources.tar.zst`, `SHA256SUMS`.
- **`verify-release.sh`** (publish job, before un-drafting): version/tag match; no placeholder in `release.toml`; exactly the expected asset set and no extra binaries; `SHA256SUMS` covers every asset; no `*.safetensors`/weights anywhere; each binary artifact contains `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/` (tar/zip listing; AppImage via `--appimage-extract`); every vendor file matches `vendor.lock.json`; signature status recorded (unsigned is allowed).

**Release checklist (ordered):**

1. `uv lock --check`; clean tree; `__version__` bumped; `release.toml` has real URLs.
2. `generate_notices.py --check` clean; `fetch_sources.py --verify` passes in the `sources` job.
3. Tag `v<ver>`, push; `release.yml` green on `linux`, `windows`, `sources`.
4. Manual Linux matrix: Ubuntu 22.04 (X11 and Wayland), Ubuntu 24.04 (Wayland + NVIDIA), Fedora current, Arch; missing-WebKit and missing-GStreamer paths fall back to browser; AppImage with and without FUSE.
5. Manual Windows matrix: Windows 11 x64 fresh (WebView2 present), Windows 10 22H2 + ESU (WebView2 absent → installer bootstraps; ZIP shows the dialog); SmartScreen wording; uninstall keeps data.
6. `verify-release.sh` green; spot-check the draft asset list.
7. Publish; archive the written offer, release date, and `SHA256SUMS`.
8. Ticket 16 measures startup/Separation on the frozen Windows bundle.
9. Counsel review before the public `0.1.0` (13 §13).

### 9. Budgets and gates

| Asset | Target | Fail the release |
|---|---|---|
| Linux onedir | ≤ 1.15 GB | > 1.40 GB |
| Linux `tar.gz` | ≤ 450 MB | > 600 MB |
| Linux `tar.zst` | ≤ 320 MB | > 420 MB |
| Linux AppImage | ≤ 350 MB | > 450 MB |
| Windows ZIP | ≤ 450 MB | > 600 MB |
| Windows setup | ≤ 380 MB | > 500 MB |
| Linux build job | ≤ 20 min | > 45 min (job timeout) |
| Windows build job | ≤ 35 min | > 60 min (job timeout) |
| Frozen startup, Linux reference host (12) | ≤ 4 s to first paint | > 8 s |
| `--smoke-separation` (1 s audio) | < 60 s, < 2 GB RSS | gate failure |

`verify_bundle.py` additionally asserts: no `torch/test`, no `torch/bin`, no `torchaudio`, no `*.safetensors`, every shipped native file has a notice and a row in `packaging/licenses/native.toml`, every direct dependency has an SPDX id, and the licence tree is complete. The 168 MB torch trim is only trusted after `--smoke-separation` passes on both OSes (research 04's "verify with a real separation, not an import").

### 10. Licensing integration (13)

- The licence generator reads `packaging/release.toml` for `<REPO_URL>`/`<RELEASE_URL>` and `source_offer_contact`; no hand-edited URLs anywhere.
- The source bundle is built in the `sources` job from the same tag, so `StemForge-<ver>-src.tar.gz` and `third-party-sources.tar.zst` always match the binaries built in `linux`/`windows`.
- `LICENSES/ffmpeg/BUILDINFO.txt`, `LICENSES/rubberband/BUILDINFO.txt`, and `LICENSES/libsndfile/BUILDINFO.txt` are generated in the same jobs that build the binaries, so the recorded configure/meson/cmake lines and SHAs are the shipped ones.
- The AppImage adds no license surface because it bundles no host GUI files; if that ever changes, 13 reopens.
- `models.toml` keeps `distributed = false`; `verify_bundle.py` fails if a weights file appears in any artifact.

### 11. Files to create in the implementation effort

| Path | Purpose | Status |
|---|---|---|
| `pyproject.toml` | dynamic version from `stemforge.__init__`, deps, `[project.scripts]`, torch CPU index | new |
| `uv.lock` | one lock for the app and the build | new |
| `README.md` | user-facing install/run, Linux host deps, Windows support policy, SmartScreen, freshness policy | new |
| `packaging/release.toml` | repo/releases/source-offer URLs; placeholder gate | new |
| `packaging/vendor.lock.json` | archive + extracted-file SHA-256 pins for ffmpeg/Deno; vendor cache key | new |
| `packaging/pyinstaller/stemforge_linux.spec` | Linux onedir build, NiceGUI-as-data, torch trim, hiddenimports, hooks | new |
| `packaging/pyinstaller/stemforge_windows.spec` | Windows differences: `console=False`, icon, `version_info.txt`, no GI | new |
| `packaging/pyinstaller/hooks/hook-gi.repository.WebKit2.py` | collect `WebKit2-4.1` typelib via `collect_typelib_data` | new |
| `packaging/pyinstaller/hooks/hook-gi.repository.Soup.py` | collect `Soup-3.0` typelib if hooks-contrib lacks it | new |
| `packaging/ffmpeg/vendor.sh`, `.ps1` | BtbN download, SHA-256 verify, extract two binaries, record `-buildconf` | new |
| `packaging/deno/vendor.sh`, `.ps1` | Deno 2.9.8 download and verify | new |
| `packaging/rubberband/build.sh`, `.ps1` | Meson release build, builtin FFT/resampler, `strip`, `$ORIGIN` rpath | new |
| `packaging/libsndfile/build.sh`, `.ps1` | CMake WAV-only shared build, `strip` | new |
| `packaging/vendor.sh`, `.ps1` | idempotent orchestration of the four vendor steps | new |
| `packaging/models/models.lock.json` | weights pin (revision, size, SHA-256), `distributed = false` | new |
| `packaging/models/fetch_models.py` | local/CI smoke download only; never used by the app | new |
| `packaging/linux/build.sh` | uv sync, `+cpu` assert, PyInstaller, assemble Bundle root, `verify_bundle.py` | new |
| `packaging/linux/package.sh` | tar.gz, tar.zst, AppDir, `appimagetool --comp zstd`, per-asset `.sha256` | new |
| `packaging/linux/smoke-test.sh` | `--self-test` + `--smoke-test`; `--with-separation` adds `--smoke-separation`; AppImage `--appimage-extract-and-run` check | new |
| `packaging/linux/run.sh` | preflight + native/browser launch (copied into the Bundle) | new |
| `packaging/linux/AppRun` | AppImage entry → `usr/lib/stemforge/run.sh` | new |
| `packaging/linux/stemforge.desktop` | AppImage desktop entry | new |
| `packaging/linux/README.txt` | Linux bundle readme (copied into the Bundle) | new |
| `packaging/windows/build.ps1` | venv, `+cpu` assert, PyInstaller, assemble Bundle root, `verify_bundle.py` | new |
| `packaging/windows/package.ps1` | optional sign, ZIP, `ISCC /DVersion=`, per-asset hash | new |
| `packaging/windows/smoke-test.ps1` | `--self-test` + `--smoke-test`; `-WithSeparation` adds `--smoke-separation` | new |
| `packaging/windows/stemforge.iss` | Inno setup: per-user, `MinVersion`, WebView2, icons, uninstall message | new |
| `packaging/windows/webview2-check.ps1` | registry detection for CI/manual checks | new |
| `packaging/windows/sign.ps1` | optional Azure Artifact Signing / `signtool` wrapper | new |
| `packaging/windows/version_info.txt` | exe file-properties resource | new |
| `packaging/windows/README.txt` | Windows bundle readme | new |
| `packaging/verify-release.sh` | asset set, hashes, licence tree, no weights, signature status | new |
| `packaging/licenses/{direct,native,frontend,models}.toml` + `texts/` | curated licence manifests (13) | new |
| `scripts/licensing/generate_notices.py` | reads `uv.lock`, dist-info, manifests, `-buildconf`; writes notices | new |
| `scripts/licensing/verify_bundle.py` | per-bundle assertions (§9) | new |
| `scripts/licensing/fetch_sources.py` | third-party source bundle + completeness check | new |
| `.github/workflows/release.yml` | the release jobs above | new |
| `.github/workflows/ci.yml` | 11 owns; packaging smoke step calls `build.sh` + `smoke-test.sh` | 11 |
| `src/stemforge/__init__.py` | `__version__` single source | amend |
| `src/stemforge/__main__.py` | dispatch `--preflight`, `--self-test`, `--smoke-test`, `--smoke-separation`, `--report`; keep `--worker` | amend |
| `src/stemforge/smoke.py` | the three test flags (no UI imports except the smoke server) | new |
| `src/stemforge/platform/preflight.py` | implement the checks in §7 | amend |
| `src/stemforge/platform/resources.py` | data root vs Bundle root | amend |
| `src/stemforge/platform/binaries.py` | vendor resolution from the Bundle root + `worker_command` | amend |
| `src/stemforge/integration/window.py` | assert `edgechromium` on Windows; native config | amend |
| `src/stemforge/assets/stemforge.{svg,ico,png}` | icon set per the contract; ticket 17 supplies final art | new |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/` | 13's tree | new |

### 12. Amendments, deferrals, and open inputs

- **06 amended:** two resolution roots (`_MEIPASS` for code/data, Bundle root for `vendor/` and licences); `smoke.py` added for the test flags. See the comment on 06.
- **13 amended:** one licence-tree copy at the Bundle root instead of an `_internal/` duplicate; `libsndfile` co-located with `rubberband`; both recorded in a comment on 13.
- **Deferred:** Windows startup/Separation/pitch figures and the frozen native window → post-handoff verification, recipe in ticket 16 (out of scope here); the PR-CI separation gate policy → ticket 11; Windows code signing identity → post-v0.1.0 (hooks exist); SBOM → post-v0.1.0; public release counsel review → release gate (13).
- **Settled since:** repository home is `https://github.com/alexmarincu/stemforge` (ticket 18). The repository itself is created at the start of the implementation effort, and `prepare` keeps a placeholder out of any tag.

## Comments

**2026-11-15 — inputs resolved by 13.** Licensing is closed; the packaging plan can assume:

- **Model weights:** not bundled, no offline pack. First-run Hugging Face download at the pinned revision + SHA-256, behind an explicit consent gate (ADR 0009). The installer stays 84 MB lighter; preflight/UI must handle "model not installed" and offline failure.
- **FFmpeg/ffprobe:** BtbN GPL static `n9.0.2-57-gd838f9f9ae`, the same provenance on both OSes (pinned SHAs in 13). Vendor only `bin/ffmpeg`, `bin/ffprobe`, and notices; drop `ffplay`/docs/presets; record `ffmpeg -buildconf`. The Windows archive is ~195 MB compressed (vs gyan's ~34 MB), so the bundle-size budget must absorb it; the post-v0.1.0 audio-only build is the escape hatch.
- **Rubber Band 4.0.0 + libsndfile 1.2.2:** built from pinned sources on both OSes (builtin FFT/resampler; shared WAV-only libsndfile). Budget a small vendor build step and ship `libsndfile.so.1`/`sndfile.dll` as separate replaceable files.
- **YouTube:** yt-dlp wheel + `yt-dlp-ejs` + Deno 2.9.8, not yt-dlp's standalone binary.
- **Every artifact** carries `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `LICENSES/`; the release page carries `StemForge-<ver>-src.tar.gz` and `StemForge-<ver>-third-party-sources.tar.zst`. See `issues/13-licensing-and-attribution-compliance.md` §12–13 and `licensing/attribution-inventory.md`.
- **Placeholders:** `<REPO_URL>` / `<RELEASE_URL>` in the notices need this ticket's final repository and release naming.

**2026-11-15 — release-gate amendment from 11.** The `publish` job now runs in a protected `release` environment with the release owner as a required reviewer. A tag push still builds the draft and runs `prepare`/`linux`/`windows`/`sources`; `publish` verifies and un-drafts only after manual approval, so the ticket 11 manual matrix runs against the draft assets before they become public. The `linux` job also runs the AppImage `--appimage-extract-and-run --self-test` check after `package.sh` and before upload. No other part of `release.yml` changes.

**2026-11-15 — preflight exit-code amendment from ticket 14.** §7's table gains exit code **8** = `preflight.bundle_damaged` (a vendor binary is missing, not executable, or fails `--version` against `vendor.lock.json`). Fatal preflight results are delivered by a native message box with a `Copy details` button, not an in-app page; `--preflight` still exits 4 for a missing preview stack, while the GUI treats that one check as degraded mode and starts. See [14](14-failure-modes-and-recovery.md).

**2026-11-15 — icon assets landed (ticket 17).** The final mark is **A — Badge**. `src/stemforge/assets/` now contains `stemforge.svg` (master, 256 grid), `stemforge.ico` (16/20/24/32/40/48/64/128/256), `stemforge-512.png`, `stemforge-1024.png`, and `stemforge-mono.svg`. The singular `stemforge.png` in §11's file list resolves to the two named PNGs; `SetupIconFile` stays `..\..\src\stemforge\assets\stemforge.ico`. The AppImage `.DirIcon` symlinks to `usr/share/icons/hicolor/scalable/apps/stemforge.svg`; `hicolor/512x512/apps/` gets `stemforge-512.png`; the About modal uses `stemforge-512.png`. Rationale: [RATIONALE.md](../prototypes/17-icon/RATIONALE.md).

