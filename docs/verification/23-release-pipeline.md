# Ticket 23 — release pipeline and verification

Measured in the `ticket/23-release` worktree on 2026-11-17, based on
`integration/v0.1.0` `5b319bf` (tickets #1–#22 closed). The dry run is a throwaway
`dry-run/v0.0.9` branch created from the ticket branch; the coordinator merges
the ticket branch into `integration/v0.1.0` after this report. Nothing was
published: `publish` waited on the protected `release` environment until the run
was cancelled, and the draft release, tag, and branch were then deleted.

## What ships

| File | Purpose |
|---|---|
| `.github/workflows/release.yml` | Tag/`workflow_dispatch` release graph: `prepare` → `linux` / `windows` / `sources` → protected `publish`; non-blocking `linux-next` / `windows-next`. |
| `scripts/verify-release.sh` | POSIX shell entry point; extracts the AppImage, then runs the stdlib verifier. |
| `scripts/release_assets.py` | Exact asset set, budgets, `SHA256SUMS`, licence tree, weights, vendor lock, source bundles, PE signature status, optional setup extraction. |
| `scripts/fetch_sources.py` | Deterministic third-party source bundle from `vendor.lock.json` `source_offer.sources` and the `SOURCES.md` mapping; `--verify`; fails closed. |
| `scripts/release_notes.py` | Release notes renderer used by `prepare`: version, yt-dlp, unsigned/SmartScreen caveat, checklist/source links, HANDOFF §3 limitations. |
| `docs/qa/release-checklist.md` | The single release checklist: both matrices, drills, budgets, publish gates, artifact hash columns. |
| `scripts/vendor_unpack.py` | Stdlib replacement for the runner-dependent `tar.exe` extraction on Windows. |
| `packaging/windows/assert_cpu_torch.py` | File-based `+cpu` assertion (Windows PowerShell mangles multi-line `-c` arguments). |
| `tests/unit/test_release_pipeline.py` | Workflow graph, asset names, verifier accept/reject, source bundle, checklist rows, release notes, unpack safety. |

The `release` environment exists on `alexmarincu/stemforge` with **required
reviewer `alexmarincu` (user id 28675077)**. `prevent_self_review` is false,
`can_admins_bypass` is true (repository-owner default), and the deployment
branch policy is unrestricted (`deployment_branch_policy: null`). It was created
with `gh api --method PUT repos/alexmarincu/stemforge/environments/release` and
the reviewers payload; `gh api .../environments/release` reports the
`required_reviewers` protection rule.

## Job graph

| Job | Runner | Timeout | Needs | Dry-run result | Wall time |
|---|---|---:|---|---|---:|
| `prepare` | `ubuntu-24.04` | 10 | — | success | 19 s |
| `linux` | `ubuntu-22.04` | 45 | prepare | success | 14 m 10 s |
| `windows` | `windows-2022` | 60 | prepare | success | 9 m 05 s |
| `sources` | `ubuntu-24.04` | 30 | prepare | success | 1 m 34 s |
| `publish` | `ubuntu-24.04` | 15 | prepare, linux, windows, sources | **waiting** in `release` | — |
| `linux-next` | `ubuntu-24.04` | 30 | prepare | success (non-blocking) | 12 m 32 s |
| `windows-next` | `windows-2025` | 45 | prepare | success (non-blocking) | 4 m 03 s |

The Windows job has per-step timeouts (vendor 25, bundle 15, smoke 10, package
10) after the first real run stalled for the full 60-minute job timeout.

`prepare` asserts the tag equals `v<stemforge.__version__>` (and
`GITHUB_REF_NAME` on a tag push), runs `generate_notices.py --check` and
`--check-release`, renders the notes, and creates/refreshes the draft. `publish`
downloads the draft, runs `verify-release.sh --skip-sums`, writes the final
`SHA256SUMS`, re-runs the full verifier, uploads the manifest, and un-drafts only
after environment approval.

## Dry run

- Branch `dry-run/v0.0.9`, commit `e6cdaffec7c009166169c9eb6067178f8d6b0312`
  (`chore(release): dry-run 0.0.9`, from `ticket/23-release` `a69f600`).
- Tag `v0.0.9`.
- Run: <https://github.com/alexmarincu/stemforge/actions/runs/44054969714>
- `prepare`, `linux`, `windows`, `sources` all **success**; `publish` **waiting**
  on the `release` environment. The run was cancelled after collecting evidence.

The first real Windows build (vendor sources + MSVC) was run
<https://github.com/alexmarincu/stemforge/actions/runs/44048484109> (job
`133920300632`); its vendor tree was reused from the same lock hash by the final
run. The Windows fixes it exposed are listed under Deviations.

### Artifacts (draft, 0.0.9)

| Asset | Bytes | Final SHA-256 |
|---|---:|---|
| `StemForge-0.0.9-linux-x86_64.tar.gz` | 407,085,913 | `d48edf7e2988d89428dfd6d67e59269f164fbe051e716d8a56fde260518e9640` |
| `StemForge-0.0.9-linux-x86_64.tar.zst` | 314,779,760 | `f35a571faf2cc8c5b6b39eba5258cb4f63ca72ff06bed4e7d74e65f673df7e8a` |
| `StemForge-0.0.9-linux-x86_64.AppImage` | 346,892,792 | `5bf18fecfa3440aecf1f6e575df7ecf21077d48d99babbd1d29ac2e5a5226b9b` |
| `StemForge-0.0.9-win64-portable.zip` | 363,043,109 | `f2d53366b1a6ca36e7097389ab6174fe5678388a12da468c056b53a24db17e97` |
| `StemForge-0.0.9-win64-setup.exe` | 271,664,714 | `cb6184c822ca6182d42dbda44ba355db5b00e8b065d20c024e7da49be2636218` |
| `StemForge-0.0.9-src.tar.gz` | 1,455,206 | `9e5d901d9fd7a100fae442ee66aa3f83282db168049031e8d8c08725481829fa` |
| `StemForge-0.0.9-third-party-sources.tar.zst` | 432,576,341 | `7bc340fbe411f3a988e3870e3b44e1110b6ce0927210f61c1b9089829ebef57c` |
| `SHA256SUMS` | 711 | covers the seven assets above |

Budgets: Linux onedir 1,090,356,454 B of 1,150,000,000 B; tar.gz 407.1 MB of
450 MB; tar.zst 314.8 MB of 320 MB; AppImage 346.9 MB of 350 MB; Windows portable
ZIP 346.2 MiB of 450 MiB; setup 259.1 MiB of 380 MiB. Linux `build.sh` took
730 s (12 m 10 s) against the ≤20 min target and 45 min timeout.

The draft's per-job `SHA256SUMS` was 309 bytes (the last uploader's three
assets). For verification, the final seven-line manifest was generated locally
exactly as `publish` does, then the full verifier was re-run; it was not uploaded.

### Windows runner evidence

First real MSVC vendor build (run `44048484109`):

| Step | Wall time | Evidence |
|---|---:|---|
| CMake 3.31.6 + VS 2022 libsndfile 1.2.2 configure/build | 71 s | `sndfile.dll`, `sndfile.lib` |
| Meson/MSVC Rubber Band 4.0.0 (`--vsenv`, static library, CLI) | 25 s | `rubberband-program.exe`, `rubberband.exe --version` 4.0.0 |
| `vendor/PROVENANCE.txt` written | 12 s | all five vendor files |
| Frozen bundle (PyInstaller onedir) | 931.5 MiB | `verify-bundle: OK (13 checks)` |
| `--self-test` / `--smoke-test` / `--preflight` | pass | reports in `build/reports/` |
| `--smoke-separation` | **7.309 s**, **1001 MiB** | four stems + instrumental; <60 s, <2 GB |
| Portable ZIP / Inno Setup 6.7.1 | 346.2 MiB / 259.1 MiB | `verify-bundle: OK (15 checks)` |
| Signature | **unsigned** | `verify-release.sh` PE certificate-table check |

Linux frozen separation: **5.935 s**, **1151 MiB**, AppImage
`--appimage-extract-and-run --self-test` passed.

### `verify-release.sh` against the draft

```text
release verification: 34 checks, 0 failed, 3 skipped
```

It checked the exact eight names, all budgets, the final `SHA256SUMS` (7/7
hashes), the licence tree in both tarballs, the AppImage, and the portable ZIP,
no weights, the vendor lock in Linux/AppImage/ZIP (5/5 files), the source
archives, and the unsigned setup. The three skips are the setup's internal tree:
`innoextract 1.9` supports Inno Setup up to 6.0.5, while the runner built an Inno
6.7.1 installer (loader revision 2). The portable ZIP carries the same tree and
`package.ps1` ran `verify_bundle.py` against it; the setup's own name table is
compressed and cannot be scanned. This limitation is recorded rather than
treated as a pass.

## Cleanup

- Workflow run `44054969714` cancelled: `status=completed`, `conclusion=cancelled`.
- Draft release `v0.0.9` deleted (`release not found` afterwards).
- Tag `v0.0.9` deleted locally and remotely (`git ls-remote --tags` empty).
- Branch `dry-run/v0.0.9` deleted locally and remotely (`git ls-remote --heads`
  empty).
- The ticket branch remains; no `main` commit, no `v0.1.0` tag, no publication.

## Gates

| Gate | Result |
|---|---|
| `ruff format --check .` | pass |
| `ruff check .` | pass |
| `mypy src/stemforge` (strict) | pass, 59 files |
| `lint-imports` | 2 contracts kept |
| `shellcheck -x scripts/*.sh packaging/linux/*.sh` | pass |
| `pytest -m "unit or ui"` | pass |
| `pytest -m "integration and not slow"` | pass |
| `generate_notices.py --check` | 201 files match |
| `generate_notices.py --check-release` | `release.toml: ok` |
| `stemforge --self-test` / `--smoke-test` | pass |

## Deviations and follow-ups

- **Dry-run base.** The tag was cut from `ticket/23-release` `a69f600` plus the
  version-only dry-run commit, not from a coordinator merge commit. The
  coordinator merges the ticket branch after this report; no product or workflow
  difference is hidden by the base choice.
- **Inno extraction.** `verify-release.sh` records the unreadable Inno 6.7.1
  header as three skips. For 0.1.0, either build a newer innoextract (1.9 is the
  latest release) or pin the installer toolchain to a version the verifier can
  read. The ZIP and `package.ps1`/`verify_bundle.py` evidence currently covers
  the same tree.
- **Windows runner fixes found by the real run** (all in the ticket branch):
  Python `tarfile` extraction instead of the runner-dependent `tar.exe`;
  `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` for CMake 4 and `-Dcmdline=auto` for the
  Meson `find_library` fallback; `PKG_CONFIG` forced to pkg-config-lite because
  the image's Strawberry Perl shim is broken; native stderr captured with
  `Start-Process`; `$ErrorActionPreference` relaxed only around child processes;
  `Start-Process -Wait` for the GUI-subsystem exe; empty missing-item arrays
  wrapped in `@()`; `vendor/PROVENANCE.txt` shipped in the ZIP.
- **Non-blocking next-image jobs** are included. `windows-next` initially caught
  the CMake 4 policy failure; after the lock/script fixes both `linux-next`
  (ubuntu-24.04) and `windows-next` (windows-2025) pass.
- **No signature tool.** `verify-release.sh` reads the PE certificate table
  directly, so the unsigned status is recorded without `osslsigncode`.
