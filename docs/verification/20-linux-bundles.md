# Ticket 20 — Linux bundles

Measured in the `ticket/20-linux-bundles` worktree on 2026-11-17, after merging
`origin/integration/v1` (`f9040ec`, ticket 22's licence tree). Host: Archcraft
(Arch-derived) Linux, kernel 7.3.4, glibc **2.44**, Python 3.13.18, 4-core CPU.
Network was live for the vendor/model fetches; `vendor/`, `dist/`, `build/`, and
the weights are git-ignored and never committed.

> **Canonical-build caveat.** This host is newer than the release floor. The
> release tarballs/AppImage must be built on `ubuntu-22.04` (glibc 2.35) by the
> release pipeline (ticket 23 / ticket 10 §5). The Arch build below proves the
> layout, the scripts, the sizes, and the runtime gates; it is **not** the
> published artifact. CI stays on `ubuntu-22.04`; it was not switched to Arch.
> The local AppImage is also built against this host's glibc and only runs on
> glibc ≥ 2.44, while the release AppImage will floor at 2.35.

## What was built

```text
dist/StemForge-1.0.0-linux-x86_64/          the Bundle root
├── StemForge                              PyInstaller onedir launcher
├── _internal/                             frozen CPython, torch, Demucs, NiceGUI, …
├── vendor/                                ffmpeg, ffprobe, deno, rubberband + libsndfile
├── LICENSES/  LICENSE  THIRD_PARTY_NOTICES.md  SOURCE_OFFER.md
├── README.md  README.txt  run.sh
dist/StemForge-1.0.0-linux-x86_64.tar.gz   canonical artifact
dist/StemForge-1.0.0-linux-x86_64.tar.zst
dist/StemForge-1.0.0-linux-x86_64.AppImage thin zstd AppImage
```

`vendor/` is a sibling of `_internal/`, not a child (ADR 0010). The build copies
the whole pinned tree except `vendor/.cache/`, then `platform.preflight`
re-verifies it inside the frozen binary.

## Build command and wall time

```bash
bash scripts/vendor-fetch.sh                     # already present from ticket 19; --verify-only re-checked
bash packaging/linux/build.sh                    # release mode, licence tree required
```

| Phase | Wall time |
|---|---:|
| `uv sync` + torch `+cpu` assert + PyInstaller + strip | ~3 min |
| Bundle root assembly, vendor copy, pre-compression verify | ~1 min |
| `tar.gz` (gzip -9n) | ~1 min |
| `tar.zst` (`zstd --ultra -22 -T0`) | ~5 min 50 s |
| AppDir + AppImage (`appimagetool --comp zstd`) | ~1 min 35 s |
| Final `verify_bundle.py` (Bundle + 3 archives) | ~10 s |
| **Total `build.sh`** | **775 s (12 min 55 s)** |

Against ticket 10's ≤20 min Linux build target and 45 min job timeout. The
release CI job will add the vendor fetch/cache restore and Ubuntu install time.

The `tar.gz` is byte-reproducible on this host: rebuilding it with the same
`SOURCE_DATE_EPOCH=315532800`, `--sort=name`, `--mtime`, `--owner=0 --group=0
--numeric-owner` produced the identical SHA-256. Tar entries show
`1980-01-01` timestamps and uid/gid `0/0`.

## Budgets

Ticket 20's budgets are enforced as hard limits by
`packaging/linux/verify_bundle.py`; all four pass.

| Asset | Measured | Budget | Used | Result |
|---|---:|---:|---:|---|
| Linux onedir | 1,080,077,566 B (1.080 GB) | 1,150,000,000 B | 93.9 % | PASS |
| `tar.gz` | 406,260,368 B (406.3 MB) | 450,000,000 B | 90.3 % | PASS |
| `tar.zst` | 314,319,602 B (314.3 MB) | 320,000,000 B | 98.2 % | PASS |
| AppImage | 346,401,272 B (346.4 MB) | 350,000,000 B | 99.0 % | PASS |

The `tar.zst` and AppImage margins are thin (1.8 % and 1.0 %). They are recorded
openly; if a later dependency bump pushes either past the budget, the verifier
fails the build rather than letting the asset slip through.

Artifact hashes (this host, glibc 2.44; not release hashes):

| Artifact | SHA-256 |
|---|---|
| `StemForge-1.0.0-linux-x86_64.tar.gz` | `3d60c6e0f46d53881042471f85e894c4b11d4015864e0162616fd3dbd1cd118b` |
| `StemForge-1.0.0-linux-x86_64.tar.zst` | `335d645b045422b5ee82c05d0ea900bad0707bc078577313fd967224e55342d4` |
| `StemForge-1.0.0-linux-x86_64.AppImage` | `cd82f190a123f9086e2b8b430c2de64dd811748839ae743371a3ee8622c11435` |

## Verifier

```bash
python3 packaging/linux/verify_bundle.py \
  --bundle dist/StemForge-1.0.0-linux-x86_64 \
  --archive dist/StemForge-1.0.0-linux-x86_64.tar.gz \
  --archive dist/StemForge-1.0.0-linux-x86_64.tar.zst \
  --archive dist/StemForge-1.0.0-linux-x86_64.AppImage
```

```text
PASS  bundle.layout       StemForge, _internal/, vendor/, run.sh, README.md
PASS  bundle.licences     LICENSE, notices, source offer, LICENSES/
PASS  bundle.weights      no model weights or unexpected model data
PASS  bundle.torch        torch/test and torch/bin trimmed to torch_shm_manager; no torchaudio
PASS  bundle.vendor       5 files match vendor.lock.json for linux-x86_64
PASS  budget.bundle       1,080,077,566 B of 1,150,000,000 B
PASS  budget.tar.gz       406,260,368 B of 450,000,000 B
PASS  archive.tar.gz.contents  licence tree present; no weights
PASS  budget.tar.zst      314,319,602 B of 320,000,000 B
PASS  budget.appimage     346,401,272 B of 350,000,000 B
```

`verify_bundle.py --self-check` passes (it proves the synthetic passing bundle
and the weights-failing bundle in a temporary directory). The unit layer
(`tests/unit/test_linux_bundle.py`) covers budgets, missing notices, weight
detection, `torch/test`, extra `torch/bin` entries, `torchaudio`, vendor-lock
mismatch, missing/extra vendor files, executable bits, tar content, the pinned
AppImage tools, and script parsing.

## Frozen runtime gates

Run with `STEMFORGE_HOME=/tmp/opencode/t20-smoke-home` (outside the repository)
and the pinned `htdemucs` weights installed through
`--install-model --accept-model-licence`; the weights were never committed.

```bash
bash packaging/linux/smoke-test.sh \
  --bundle dist/StemForge-1.0.0-linux-x86_64 \
  --with-separation \
  --appimage dist/StemForge-1.0.0-linux-x86_64.AppImage
```

| Gate | Result | Detail |
|---|---|---|
| `--self-test` with `STEMFORGE_REQUIRE_VENDOR=1` | exit 0 | 19 ok, 0 failed |
| `--smoke-test` | exit 0 | 20 ok, 0 failed; server polled HTTP 200 |
| `--smoke-separation` | exit 0 | 6.946 s wall, 1,112,965,120 B peak RSS; four stems + instrumental |
| AppImage `--appimage-extract-and-run --self-test` | exit 0 | 19 ok, 0 failed |
| Whole smoke script | exit 0 | **17 s** |

`--smoke-separation` is well inside the <60 s / <2 GB gate (peak RSS 1.113 GB).

`--preflight` on this host exits **3** only because PyGObject/GTK is absent;
the bundle check itself is green:

```text
exit 3
platform ok   Linux x86_64, glibc 2.44.
window   fail PyGObject (gi) is not installed.
bundle   ok   ffmpeg, ffprobe, rubberband, and deno match vendor.lock.json;
              models.lock.json is readable.
storage  ok   All application folders are writable.
preview  ok   GStreamer elements available via gst-inspect.
```

## Native window / browser fallback

This host has GStreamer but no `gi`/GTK/WebKitGTK, so `run.sh` exercised the
fallback path:

```text
$ timeout 15 ./dist/StemForge-1.0.0-linux-x86_64/run.sh
StemForge can't start
StemForge needs GTK 3 and WebKitGTK 4.1 to open its window.
… per-distro package line …
StemForge: native window unavailable; starting in browser mode.
StemForge: browser mode is on; opening http://127.0.0.1:8080
```

The process then served the UI until `timeout` killed it (expected exit 124).
`STEMFORGE_BROWSER=1` is honoured as the explicit escape hatch, and
`_browser_preflight()` only relaxes a failed *window* check: a damaged bundle or
unwritable storage still exits fatally. Diagnostic arguments
(`--self-test`, `--smoke-test`, `--smoke-separation`, `--preflight`, `--worker`,
`--import`, `--install-model`, `--help`) bypass the probe and run directly, which
is what makes the AppImage extraction check exact.

The preflight's native fatal notice is shown by the quiet probe before `run.sh`
prints its fallback line. It is informative but says "can't start" even though
browser mode then starts; see "Deviations".

## AppImage extraction evidence

```bash
mkdir -p /tmp/opencode/t20-appimage
cd /tmp/opencode/t20-appimage
/path/dist/StemForge-1.0.0-linux-x86_64.AppImage --appimage-extract
```

* extraction exit 0 in **2 s**; `squashfs-root` is **1015 MiB**;
* `squashfs-root/AppRun`, `stemforge.desktop`, `stemforge.svg`, `stemforge.png`,
  `.DirIcon` are at the root; the desktop file and icons also exist under
  `usr/share/`;
* `squashfs-root/usr/lib/stemforge/` is the **single** payload copy:
  `StemForge`, `_internal/`, `vendor/`, `run.sh`, `README.md`, `README.txt`,
  `LICENSES/`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`;
* `LICENSES/` carries the full ticket 22 tree (`common`, `deno`, `ffmpeg`,
  `gcc-runtime`, `libsndfile`, `llvm-openmp`, `nicegui`, `numpy`, `openblas`,
  `openssl`, `pyinstaller`, `python`, `python-runtime`, `pytorch`, `rubberband`,
  `texts`, `webview2`, `yt-dlp-ejs-role`);
* `file` reports an ELF x86-64 static-pie runtime, stripped;
* `--appimage-extract-and-run --self-test` passed (see the gate table).

No GTK/WebKitGTK/GStreamer libraries are in the AppDir (ADR 0011). The AppImage
requires the host stack listed in `packaging/linux/README.md`; FUSE-less hosts
use `--appimage-extract-and-run`, and the tarball is the fallback artifact.

## Tool pinning

`packaging/linux/appimage.lock.json` pins both moving parts:

| Tool | Version | SHA-256 |
|---|---|---|
| `appimagetool-x86_64.AppImage` | 1.9.1 | `ed4ce84f…4eb0` |
| `runtime-x86_64` (type2 runtime) | 20251108 | `2fca8b44…260d` |

`build.sh` downloads them into `build/tools/`, verifies size and SHA-256, and
passes `--runtime-file` so appimagetool never fetches its moving `continuous`
runtime at build time. The AppImage is built with `--comp zstd`, 1 MiB squashfs
blocks, zstd level 22, and `-no-xattrs`; all files are owned by uid/gid 0 and
`SOURCE_DATE_EPOCH` is honoured by appimagetool.

## Gates run in this worktree

| Gate | Result |
|---|---|
| `ruff format --check .` | pass (179 files) |
| `ruff check .` | pass |
| `mypy src/stemforge` | pass (59 files) |
| `lint-imports` | 2 contracts kept |
| `shellcheck -x packaging/linux/*.sh scripts/*.sh` | pass |
| `generate_notices.py --check` | 201 files match |
| `generate_notices.py --check-release` | `release.toml: ok` |
| `pytest -m "unit or ui"` | 783 passed, 1 skipped |
| `pytest -m "integration and not slow"` | 128 passed |
| `pytest -m "integration and slow"` | 9 passed |
| Frozen `--self-test` / `--smoke-test` / `--smoke-separation` | pass |
| Real bundle + tarballs + AppImage verification | pass |

## CI

`.github/workflows/linux-bundle.yml` runs on `ubuntu-22.04`, caches `vendor/`
keyed on `packaging/vendor.lock.json` (same key shape as `frozen-linux`), builds
with `build.sh`, runs the frozen smoke tests, optionally installs the model and
runs `--smoke-separation`, runs the AppImage extraction self-test, and uploads
reports only on failure. The existing `frozen-linux` job in `ci.yml` is
unchanged and keeps its 20-minute timeout; the packaging job has ticket 10's
45-minute timeout.

Triggers:

* `workflow_dispatch` (with a `separation` boolean input, default true);
* a path-filtered push to `integration/v1` when packaging files change.

**Dispatch caveat:** GitHub only offers/accepts `workflow_dispatch` for a
workflow that exists on the repository's default branch. This branch and
`integration/v1` are not the default branch, so dispatch cannot be used until
the workflow reaches the default branch. The path-filtered push trigger runs
once when this ticket merges into `integration/v1`; the tag-triggered release
path is ticket 23's `release.yml`. No PR job pays for the full packaging build.

## Deviations and follow-ups

* **README naming:** ticket 10's layout says `README.txt`, ticket 20's brief says
  `README.md`. The bundle ships `README.md` (the source file
  `packaging/linux/README.md`) and an identical `README.txt` copy.
* **Stripping:** the PyInstaller spec does not strip, and unstripped wheels put
  the onedir at ~1.29 GB, over budget. `build.sh` now runs
  `strip --strip-unneeded` on unstripped ELF shared objects in `_internal/`
  (sphn 82 MB → 4.4 MB; `libtorch_cpu.so` 440 MB → 350 MB). `vendor/` is never
  touched because its hashes are pinned. The frozen separation gate validates
  the stripped inference stack.
* **Browser mode:** `STEMFORGE_BROWSER` was documented in ticket 10 but not
  implemented in `__main__`. Ticket 20 adds the env check, a browser-mode
  `ui.run(native=False, show=True, host="127.0.0.1")`, and
  `_browser_preflight()` so only a missing window is downgraded. A damaged
  bundle still exits 8.
* **run.sh probe message:** the quiet preflight still shows the ticket 14 fatal
  notice before the browser fallback line. The fallback works; a future polish
  could add a probe-only flag that suppresses the notice.
* **AppImage runtime:** appimagetool 1.9.1 fetches its runtime from the moving
  `continuous` tag unless `--runtime-file` is given; the runtime is pinned here
  and verified, closing that reproducibility gap.
* **Release artifact:** the local build is glibc-2.44 and is not publishable.
  Ubuntu 22.04 (glibc 2.35) remains the release runner. The manual matrix from
  ticket 11 §7 (Ubuntu 22.04/24.04, Fedora, Arch; missing WebKitGTK and
  GStreamer; Wayland/X11; FUSE/no-FUSE) is a release-time check and was not run
  here beyond this Arch host.
* **Licence tree:** ticket 22 landed before the final build; the release-mode
  build required and included the full `LICENSES/` tree. No pending content
  gate.
