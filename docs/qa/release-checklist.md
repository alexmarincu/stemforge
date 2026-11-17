# StemForge release checklist

The single manual QA and publish gate for every release. Fill this in against
the **draft release assets**, not a local build. A release is not published
until every row has a result, every artifact hash is recorded, and the publish
gates below are true.

| Field | Value |
|---|---|
| Release version | `________________` |
| Tag | `v________________` |
| Release manager | `________________` |
| Date | `________________` |
| Commit | `________________` |
| Draft release URL | `________________` |
| `verify-release.sh` run | `________________` |

## Artifact hashes and budgets

Download every asset from the draft, then record its SHA-256 and size here.
`verify-release.sh` enforces the target column; a value above the target fails
the release.

| Asset (ticket 10 §9 name) | SHA-256 | Size | Budget target | Fail threshold | Result |
|---|---|---:|---:|---:|---|
| Linux onedir | — | | ≤ 1.15 GB | > 1.40 GB | |
| Linux `tar.gz` | | | ≤ 450 MB | > 600 MB | |
| Linux `tar.zst` | | | ≤ 320 MB | > 420 MB | |
| Linux AppImage | | | ≤ 350 MB | > 450 MB | |
| Windows ZIP | | | ≤ 450 MB | > 600 MB | |
| Windows setup | | | ≤ 380 MB | > 500 MB | |

| Release asset | SHA-256 | Size | Result |
|---|---|---:|---|
| `StemForge-<ver>-linux-x86_64.tar.gz` | | | |
| `StemForge-<ver>-linux-x86_64.tar.zst` | | | |
| `StemForge-<ver>-linux-x86_64.AppImage` | | | |
| `StemForge-<ver>-win64-portable.zip` | | | |
| `StemForge-<ver>-win64-setup.exe` | | | |
| `StemForge-<ver>-src.tar.gz` | | | |
| `StemForge-<ver>-third-party-sources.tar.zst` | | | |
| `SHA256SUMS` | | | |

| Build / runtime budget | Measured | Target (ticket 10 §9) |
|---|---:|---:|
| Linux onedir | | ≤ 1.15 GB (fail > 1.40 GB) |
| Linux build job | | ≤ 20 min (job timeout 45 min) |
| Windows build job | | ≤ 35 min (job timeout 60 min) |
| Frozen startup, Linux reference host | | ≤ 4 s to first paint (fail > 8 s) |
| `--smoke-separation` (1 s audio) | | < 60 s and < 2 GB RSS |
| Linux `tar.gz` deterministic rebuild | | same SHA-256 |

## Publish gates (ticket 11 §8)

Publish only when all of these are true. Record the evidence next to each row.

| # | Gate | Result / evidence |
|---|---|---|
| 1 | The tagged commit is on `main` with `ci.yml` green | |
| 2 | `release.yml`'s `prepare`, `linux`, `windows`, and `sources` are green; `publish` is waiting on the protected environment; the non-blocking next-image jobs have been read | |
| 3 | `verify-release.sh` is green: exact asset set, `SHA256SUMS` covers every asset, every artifact carries `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `LICENSES/`, no `*.safetensors`/weights anywhere, every vendor file matches `vendor.lock.json`, and the signature status is recorded | |
| 4 | Both frozen `--smoke-separation` runs pass (< 60 s, < 2 GB, four Stems + Instrumental) | |
| 5 | The 10 §9 bundle budgets pass; the startup measurement meets the ≤ 4 s target on the reference host, and a reading above 8 s fails | |
| 6 | `docs/qa/release-checklist.md` is complete for the Linux and Windows matrices, with artifact hashes | |
| 7 | Version `0.1.0` also has counsel sign-off | |

## Environment coverage

Run the Linux rows on every host below. `Ubuntu 22.04` is the release floor
(glibc 2.35); `Ubuntu 24.04` covers the newer Wayland/NVIDIA path; Fedora and
Arch catch distro drift.

| Environment | OS build / kernel | GPU / session | Tester | Date | Coverage result |
|---|---|---|---|---|
| Ubuntu 22.04 | | X11 | | | |
| Ubuntu 22.04 | | Wayland | | | |
| Ubuntu 24.04 | | Wayland + NVIDIA | | | |
| Fedora (current) | | Wayland or X11 | | | |
| Arch (current) | | Wayland or X11 | | | |
| Windows 11 x64 (fresh) | | WebView2 present | | | |
| Windows 10 22H2 + ESU | | WebView2 present and absent | | | |

## Linux matrix (ticket 11 §7)

Columns: `Pass/fail`, `OS build`, `Date`, `Tester`, `Artifact SHA-256`.
"Run on" names every host that must execute the row.

| # | Check | Run on | Pass/fail | OS build | Date | Tester | Artifact SHA-256 |
|---|---|---|---|---|---|---|---|
| L1 | `run.sh` opens the native window; `--preflight --json` is clean on a fully provisioned host | Ubuntu 22.04 X11, Ubuntu 22.04 Wayland, Ubuntu 24.04, Fedora, Arch | | | | | |
| L2 | Drag-and-drop WAV, MP3, FLAC, 48 kHz, mono, and a multichannel file; the downmix notice appears | all five Linux hosts | | | | | |
| L3 | YouTube import: one public, non-age-gated video ≤ 15 min; consent modal once; disclaimer line; a playlist/channel/search URL fails as a field error; "Clear YouTube cache" removes only `<cache>/yt-dlp/` | all five Linux hosts | | | | | |
| L4 | Preview: play, seek, and switch Tracks; only one player sounds at a time | all five Linux hosts | | | | | |
| L5 | Pitch: +12 and −12, A/B between Original and the newest render, version tabs, and the `master`/`stems` suffixes | all five Linux hosts | | | | | |
| L6 | Separation: first-run model download and consent; cancel mid-Job; the model-missing gate and resubmit; per-stem playback; Instrumental matches the non-vocal mix | Ubuntu 22.04, Ubuntu 24.04, Fedora | | | | | |
| L7 | Export: WAV 24, FLAC 24, and MP3 320 through the native save dialog, with correct extension handling and filters; probe each exported file; tags and cover art are present | all five Linux hosts | | | | | |
| L8 | Remove Track deletes cached audio after confirmation; Clear cached audio leaves the live Workspace intact; recent files behave as 07 defines | all five Linux hosts | | | | | |
| L9 | Logs exist under the 07 paths; About shows licence, notices, source offer, and the licences folder | all five Linux hosts | | | | | |
| L10 | AppImage with and without FUSE (`--appimage-extract-and-run`); browser fallback when WebKitGTK or GStreamer is missing; Wayland and X11; the 2.52+ NVIDIA/Skia workaround note | Ubuntu 22.04 (both sessions), Ubuntu 24.04, Fedora, Arch | | | | | |
| L11 | Offline: launch, local import, and Separation with the model already installed; model missing + offline shows the actionable error | Ubuntu 22.04, Ubuntu 24.04, Fedora | | | | | |
| L12 | Fatal preflight: start on a host without GTK 3/WebKitGTK 4.1; the native "StemForge can't start" box names the fix, `Copy details` yields the diagnostics report, and the process exits 3 | Ubuntu 22.04 | | | | | |
| L13 | Degraded playback: remove GStreamer's good plugins; the persistent banner appears, transports are disabled with "Playback unavailable", Separation/Pitch/Export still work, and `--preflight` exits 4 while the GUI starts | Ubuntu 22.04 | | | | | |
| L14 | Resource warning: under 8 GB RAM or under 20 GB free disk shows a dismissible banner once per launch; importing still starts and the per-Job space check still enforces | Ubuntu 22.04 | | | | | |
| L15 | Crash recovery: force-quit during Export, relaunch; the startup sweep removes cache partials, the one-time recovery banner appears once, completed audio survives, and a clean relaunch shows no banner | Ubuntu 22.04 | | | | | |
| L16 | About → Copy diagnostics and every error Details disclosure: read-only report, "Contains local file paths. Review before sharing.", clipboard copy works, the text stays selectable when copy fails, and nothing is uploaded or written | all five Linux hosts | | | | | |

## Windows matrix (ticket 11 §7)

Run on Windows 11 x64 fresh and Windows 10 22H2 + ESU. Record the exact edition
and build in `OS build`.

| # | Check | Run on | Pass/fail | OS build | Date | Tester | Artifact SHA-256 |
|---|---|---|---|---|---|---|---|
| W1 | Inno installer: per-user, no UAC, Start Menu shortcut (desktop optional), `MinVersion`, WebView2 present and absent (bootstrapper runs silently), optional launch | Windows 11 fresh; Windows 10 22H2 + ESU without WebView2 | | | | | |
| W2 | Portable ZIP: WebView2 absent shows the message box with the download link, not a blank window | Windows 10 22H2 + ESU | | | | | |
| W3 | SmartScreen wording: unsigned build, "Unknown publisher", `SHA256SUMS` is the integrity check | both Windows hosts, fresh browser profile | | | | | |
| W4 | Native dialogs: open/save filters, extension normalization, overwrite confirmation; no console window | both Windows hosts | | | | | |
| W5 | Playback, pitch, separation, export, remove, clear cache, logs, About — the same rows as Linux | both Windows hosts | | | | | |
| W6 | Title bar, taskbar, Start Menu, and Alt-Tab icon at 16/20/24/32/48/256 px | both Windows hosts | | | | | |
| W7 | Uninstall keeps `%LOCALAPPDATA%\StemForge` and shows the data path; reinstall reuses it | both Windows hosts | | | | | |
| W8 | `--preflight --json` on a clean VM; WebView2/.NET failure exits 5 with the actionable message | Windows 10 22H2 + ESU without WebView2; Windows 11 fresh | | | | | |
| W9 | Native fatal box for missing WebView2/.NET: "StemForge can't start", the download/fix line, `Copy details` gives the diagnostics report, and the process exits 5 | Windows 10 22H2 + ESU without WebView2 | | | | | |

## Both OSes, once per release (ticket 11 §7)

| # | Check | Run on | Pass/fail | OS build | Date | Tester | Artifact SHA-256 |
|---|---|---|---|---|---|---|---|
| B1 | close the window during a running Job — no prompt, no veto, partials removed, settings saved | Linux and Windows | | | | | |
| B2 | corrupt-file and unsupported-file errors | Linux and Windows | | | | | |
| B3 | disk-space warning | Linux and Windows | | | | | |
| B4 | job log path from the error banner | Linux and Windows | | | | | |
| B5 | crash recovery banner after a force-quit mid-Job: shown once when the sweep removed residue, absent on a clean launch | Linux and Windows | | | | | |
| B6 | Copy diagnostics from About and from one failed Job's Details: read-only, paths visible, copy-only, no upload or file written | Linux and Windows | | | | | |

## Automated evidence

| Evidence | Where | Result |
|---|---|---|
| `release.yml` prepare / linux / windows / sources | run URL | |
| `publish` waiting in the protected `release` environment | run URL | |
| Linux frozen `--self-test` / `--smoke-test` | `build/reports/*.json` | |
| Linux `--smoke-separation` wall / RSS | `build/reports/smoke-separation.json` | |
| Windows frozen `--self-test` / `--smoke-test` | `build/reports/*.json` | |
| Windows `--smoke-separation` wall / RSS | `build/reports/smoke-separation.json` | |
| AppImage `--appimage-extract-and-run --self-test` | `build/reports/appimage-self-test.json` | |
| `verify-release.sh` output | publish log | |
| `SHA256SUMS` re-checked from a clean directory | `sha256sum -c SHA256SUMS` | |
| Signature status (unsigned allowed) | `verify-release.sh` output | |

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| Release manager | | | |
| Linux QA | | | |
| Windows QA | | | |
| Licensing / counsel (0.1.0) | | | |

A published tag and its assets are immutable. If a defect is found after
publication, edit the release notes to mark the release known-bad and publish a
patch; never delete or move the tag.
