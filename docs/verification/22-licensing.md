# Ticket 22 — Licensing artifacts and About

Measured in the `ticket/22-licensing` worktree on Linux x86_64, 2026-11-17.
Host: Arch Linux kernel 7.3.4, glibc 2.44, Python 3.13.18 (uv), repo HEAD based
on `integration/v0.1.0` `ca3ac74`. The vendor tree is absent in this checkout, so
`vendor.lock.json` is the provenance source; the generator never needs a binary.

## What ships

| Artifact | Source of truth | Size (bytes) |
|---|---|---:|
| `LICENSE` | `packaging/licenses/texts/GPL-3.0-only.txt` | 34,674 |
| `THIRD_PARTY_NOTICES.md` | lock + dist-info + `native.toml` | 16,375 |
| `SOURCE_OFFER.md` | `release.toml` + `vendor.lock.json` | 5,312 |
| `LICENSES/` (198 files) | generated | 846,585 |
| `LICENSES/python/DEPENDENCIES.md` | every `uv.lock` package | 24,039 |
| `LICENSES/common/` (13 texts) | `packaging/licenses/texts/` | 150,952 |
| `LICENSES/texts/` (157 pooled files) | deduplicated wheel licences | 643,533 |

The pooled `LICENSES/texts/` directory is content-addressed
(`<name>-<sha256:12>.txt`): identical upstream licence files appear once and
every `DEPENDENCIES.md` row links the same file. Curated components keep their
own `LICENSES/<component>/` directory with `NOTICE.md` and, for built vendor
components, `BUILDINFO.txt`. Long licence texts are never inlined in the
notice; the notice links them.

## Inventory coverage

| Source | Coverage | Evidence |
|---|---|---|
| `uv.lock` | 105/105 entries | `LICENSES/python/DEPENDENCIES.md` |
| Runtime packages | 80 | 104 dependencies + the project row; 80 runtime |
| Development-only | 20 | marked `development` in the table |
| Build-only | 5 | PyInstaller + hooks + altgraph/pefile/pywin32-ctypes |
| Windows-only runtime | 6 | `pythonnet`, `clr-loader`, `cffi`, `pycparser`, `colorama`, `cffi` deps marked `(windows)` |
| Linux-only runtime | 1 | `uvloop` marked `(linux)` |
| `torchaudio` | 0 | absent from `uv.lock`, paths, and generated text |
| Model weights | 0 | no `.safetensors`/`.pt`/`.pth`/`.onnx`/`.ckpt` |
| Vendor binaries | 0 | text-only tree; `.exe`/`.dll`/`.so` refused |
| Curated components | 14 | FFmpeg, Rubber Band, libsndfile, Deno, yt-dlp/EJS, PyTorch, NumPy, Python runtime, PyInstaller, GCC runtime, OpenBLAS, LLVM OpenMP, OpenSSL, WebView2, NiceGUI frontend |

Curated notices and their locations:

| Requirement | Where |
|---|---|
| FFmpeg GPL build, buildconf record, external libraries | `THIRD_PARTY_NOTICES.md` §2; `LICENSES/ffmpeg/{NOTICE.md,BUILDINFO.txt}` |
| Rubber Band build line | `LICENSES/rubberband/BUILDINFO.txt` |
| libsndfile LGPL relink right | `LICENSES/libsndfile/{NOTICE.md,BUILDINFO.txt}` |
| Deno version and EJS role | `THIRD_PARTY_NOTICES.md` §2 |
| yt-dlp / yt-dlp-ejs wheel roles | `THIRD_PARTY_NOTICES.md` §2 and §3 |
| PyTorch wheel notice tree | `LICENSES/python/DEPENDENCIES.md` + `LICENSES/texts/` |
| NumPy OpenBLAS and GCC runtime | `LICENSES/{numpy,openblas,gcc-runtime}/NOTICE.md` |
| NiceGUI Quasar/Vue/Socket.IO/Tailwind/DOMPurify/UnoCSS/fonts | `LICENSES/nicegui/frontend/*.txt` |
| WebView2 redistributable | `LICENSES/webview2/NOTICE.md` |
| GPL written offer and archive list | `SOURCE_OFFER.md` |

## `--check` evidence

```text
$ uv run python scripts/generate_notices.py --check
201 files match

$ uv run python scripts/generate_notices.py --check-release
release.toml: ok
```

A hand edit is detected with the first differing file and a unified diff:

```text
$ # after appending a line to THIRD_PARTY_NOTICES.md
1 difference(s): 1 changed, 0 missing, 0 extra; first: THIRD_PARTY_NOTICES.md
--- committed/THIRD_PARTY_NOTICES.md
+++ generated/THIRD_PARTY_NOTICES.md
@@ ...
+A hand edit that must be detected.
```

The generator was also run twice into separate directories; the two trees were
byte-identical (`test_two_generations_are_byte_identical`). No timestamps,
environment paths, or filesystem ordering enter the output.

## About modal — headless marker evidence

`tests/ui/test_about_licensing.py` (7 tests, all headless; the OS opener is an
injected seam):

| Marker / assertion | Behaviour |
|---|---|
| `settings-dialog`, `settings-close` | existing modal contract preserved |
| `about-version` | renders `StemForge 0.1.0` from `stemforge.__version__` |
| `about-licence`, `about-view-licence` | `GPL-3.0-only`; scrollable plain-text `LICENSE` |
| `about-notices`, `about-view-notices` | `THIRD_PARTY_NOTICES.md` rendered offline |
| `about-source-offer`, `about-view-source` | `SOURCE_OFFER.md` rendered offline |
| `about-open-licences` | calls the injected `PathOpener` with `service.licence_files().licenses_dir` |
| `about-files-error` | missing tree → `licensing.files_missing` headline, opener never called |
| `about-weights-note` | weights-not-covered wording from ticket 13 |
| `about-cache-size`, `about-recent-size` | storage meter and recent count |
| `clear-cached-audio`, `clear-recent`, `clear-youtube-cache` | each clears only its own data; live Track survives Clear cache |
| `youtube-toggle` | still toggles YouTube import and hides the row |

Resolution is `WorkspaceService.licence_files()` → `resources.bundle_root()`:
the executable's parent when frozen (beside `_internal/`) and the repo root in a
source checkout. No path is relative to CWD.

## Gates

| Gate | Result |
|---|---|
| `ruff format --check .` | 175 files already formatted |
| `ruff check .` | All checks passed |
| `mypy src/stemforge` (strict) | Success, 59 files |
| `lint-imports` | 2 contracts kept |
| `pytest -m "unit or ui"` | 753 passed, 1 skipped |
| `pytest -m "integration and not slow"` | 128 passed |
| `generate_notices.py --check` | 201 files match |
| `generate_notices.py --check-release` | ok |
| `stemforge --self-test` | exit 0, 17 ok / 2 skipped |
| `stemforge --smoke-test` | exit 0, HTTP 200 |

## Manual items (not automated here)

- **Open licences folder in a real native window.** The test proves the seam is
  called with the resolved `LICENSES/` path. The actual `xdg-open` (Linux) and
  `os.startfile` (Windows) launches, and their file-manager result, are manual.
  Run on both OSes from the frozen bundle.
- **GPL source-offer link liveness.** `SOURCE_OFFER.md` uses the ticket-18
  repository, release, and issues URLs. The release asset URLs only resolve
  after `release.yml` uploads `StemForge-0.1.0-src.tar.gz` and
  `StemForge-0.1.0-third-party-sources.tar.zst`; spot-check them on the draft
  release before publish. The repository and issues URLs are live now.
- **Frozen bundle licence-tree placement.** `verify_bundle.py` (#20/#21) must
  confirm the four entries beside `_internal/`; this ticket supplies the tree
  and the build-script contract in `packaging/licenses/README.md`.
- **Counsel review** of the weights-download posture and source-offer wording
  remains the ticket-13 release gate.

## Deviations and notes

- **Root `LICENSE` is generated** in addition to the three named outputs, so the
  artifact contract in HANDOFF §4 and ticket 13 §9 is satisfied and `--check`
  covers it.
- **Two error codes were added** (`licensing.files_missing`,
  `licensing.open_failed`) because the About modal has reachable failures and
  the error contract requires one code and one action. The independent
  taxonomy copies in `test_error_codes.py` and `test_scaffold.py` were updated.
- **Storage controls already specified by #07/#08 but absent from the branch**
  (cache meter, Clear cached audio, Clear recent) are implemented here because
  the ticket requires the About modal to keep them. `WorkspaceService` now
  exposes `clear_cache()` and `clear_recent()` over the existing
  `ArtifactStore.clear_cache()`.
- **The current lock does not select yt-dlp's `default` extra**, so `mutagen`,
  `brotli`, `requests`, `urllib3`, and `pycryptodomex` are not resolved. The
  notices follow `uv.lock`; the older ticket-13 draft mentioned them, but the
  lock is authoritative (ticket 13 D7).
- **`--check` byte-compares on Linux.** The committed tree is the Linux
  reference output; `test_check_is_green_on_the_committed_tree` skips on other
  hosts because platform-specific wheels (notably PyTorch) carry different
  vendored notice trees. The CI `licensing` job is `ubuntu-24.04`.
- **`BUILDINFO.txt` is generated from `vendor.lock.json`**, not from a shipped
  binary. The release build appends the runtime `ffmpeg -buildconf`/`--version`
  output to the same component directory (ticket 10 §10); the committed file
  records the reproducible pin and says so.
- **Python licence texts are pooled by content** under `LICENSES/texts/`
  instead of one directory per wheel. This is the dedupe the ticket asks for;
  `LICENSES/python/DEPENDENCIES.md` is the per-package index.
- **`pythonpath = ["."]` was added to pytest** so `tests/unit/test_notices.py`
  can import `scripts.generate_notices` without installing the repository.
