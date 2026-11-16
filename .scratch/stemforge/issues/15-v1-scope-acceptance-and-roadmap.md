# 15 — V1 scope, acceptance criteria, and roadmap

**Type:** grilling
**Status:** resolved
**Blocked by:** 05, 06, 07, 08, 09, 10, 11, 12, 13, 14

## Question

Synthesize the resolved decisions into the handoff:

- Freeze v1 in/out scope, reconciling the standing decisions and the deferred list.
- Acceptance criteria per capability (local load, YouTube import, Preview, Pitch shift, Separation, Stems, Export) on Linux and Windows.
- Phased roadmap with gates and dependencies: scaffold → load/preview → pitch → separation → YouTube → export → packaging → hardening.
- Definition of done for source runs and for both OS bundles.
- Post-v1 backlog (waveform, mixer, library, batch export, GPU extras).
- Open risks and go/no-go criteria for each phase.

The answer is the roadmap section of the handoff; it must leave nothing for the implementation effort to decide.

## Answer

**One policy: the implementation handoff is [`HANDOFF.md`](../../../HANDOFF.md) at the repository root; v1 scope is frozen exactly as charted; acceptance is two-part (a user-visible OS-matrix pass plus the existing automated gate from [11](11-testing-and-ci-strategy.md), with no new thresholds); the roadmap is eight phases with a thin frozen-build spine in scaffold; Windows performance stays measured-in-hardening and disclosed rather than gated; and post-v1 is an ordered, unscheduled backlog.**

### 1. Handoff artifact

- The handoff is [`HANDOFF.md`](../../../HANDOFF.md) at the repo root, produced by this ticket. It is a compact entry point, not a second decision store: it indexes the map, tickets, ADRs, research, prototype, bench, and licensing artifacts, then fixes **scope, acceptance, sequence, definition of done, risks, and go/no-go**.
- The map and its resolved tickets remain authoritative. Where the handoff and a ticket disagree, the ticket wins.
- `AGENTS.md` gains a pointer to the handoff so a fresh implementation session starts there.
- No new ADR: a roadmap and acceptance contract are reversible planning. The existing ADRs 0001–0012 remain the hard-to-reverse decisions.

### 2. Frozen v1 scope

- In scope exactly as the map's standing decisions and tickets 01–14, 18 record: local load (WAV/MP3/FLAC/M4A, mono/multichannel normalization, 30-minute cap), YouTube import (one public non-age-gated video, ≤15 min, pinned trio), one-player Preview, ±12 semitone Render, `htdemucs` Separation with first-run download, Stems + Instrumental, WAV 24/FLAC 24/MP3 320 Export, cache/recent/settings, native dialogs, Linux tar.gz/tar.zst/thin AppImage, Windows ZIP/Inno, notices/source offer, diagnostics.
- The deferred list is frozen as recorded: waveform, stem mixer, project library, batch export, shortcuts, themes, draft pitch/formant toggle, Save original, YouTube thumbnails, bundled weights, audio-only FFmpeg, real-time/VST/karaoke, cloud/accounts/telemetry/auto-update/i18n, signing, distro packages, macOS/ARM/32-bit, Windows-on-Linux builds, and Windows performance measurement on this map.
- **[17](17-app-icon-art.md) remains a v1 deliverable.** Its contract is already fixed by [10](10-packaging-and-distribution-plan.md); the HITL choice of the final mark and the assets are open. It is the **only remaining map ticket**; it does not change the handoff or the roadmap.
- No additions. The handoff's §3 is the complete scope statement.

### 3. Acceptance criteria

- Every capability has a **user-visible pass** on the Linux and Windows matrices and an **automated gate** from [11](11-testing-and-ci-strategy.md), adopting its settled thresholds verbatim: duration 50 ms, pitch ±0.15 semitone, instrumental sum ≤1e-3, peak ±0.1 dB, `/media` GET 200 / Range 206 / HEAD 405 / suffix-multi 416 / traversal 404, 1024×700 minimum, frozen separation <60 s and <2 GB, coverage 85% core / 70% overall, and the release budgets.
- Windows acceptance is the **manual matrix plus the frozen smoke**; there is no automated native-window path. First-run model download, offline behavior, native dialogs, and live YouTube import are manual QA on both OSes.
- The failure table is acceptance too: every reachable code has a trigger, a surface, one sentence, and one action; `internal.unexpected` must be unreachable in a supported workflow.
- No new numeric threshold is invented by this ticket. The full per-capability table is in [`HANDOFF.md`](../../../HANDOFF.md) §4.

### 4. Roadmap and phase gates

Eight phases, in order, each exiting with a demo, its automated gate, and no open decisions:

1. **Scaffold + frozen spine** — repo, package tree, CI, `freeze_support`, lazy-import contract, two Bundle roots, preflight/smoke skeleton, stub frozen build.
2. **Load + Preview** — import Job, canonical WAV, metadata/cover, preview MP3, `/media`, dialogs, cache/LRU, workspace and rail, one player, Remove.
3. **Pitch** — Rubber Band adapter, render sets, gain, manifests, pitch card, version tabs, A/B.
4. **Separation** — worker, model install, blocks, progress/cancel, Stems/Instrumental, model gate, inline errors.
5. **YouTube** — parser, yt-dlp adapter, consent/toggle, cache reuse, classifier, clear-YouTube-cache.
6. **Export** — export Job, encoders, tags/cover, format sheet, save dialog, destination checks, errors.
7. **Packaging + distribution** — vendor builds, specs, AppImage, ZIP/Inno, WebView2, notices, `release.yml`, `verify-release.sh`, budgets.
8. **Hardening + release** — full matrices, Windows measurements, error-copy audit, crash/close/cancel/offline drills, counsel, 1.0.0.

The frozen-build spine belongs to phase 1 because `freeze_support`, lazy imports, and the two resource roots are architectural; the full packaging phase stays last because it needs every artifact. Phase 4's early gate is a **source-run** 1-second separation; the frozen separation gate belongs to phase 7, where a real Bundle exists. Phase 5 is technically independent of 3–4 and may overlap; keep it after 4 only to preserve one Job pipeline in development.

### 5. Definition of done

- **Source runs:** `uv sync --frozen`, `uv run stemforge` native on both development OSes, pytest unit/integration/UI and `scripts/check.sh` green, coverage thresholds met, no module-scope torch/demucs, no writes outside `AppPaths`, `STEMFORGE_HOME` isolation, and a full local workflow (import → preview → pitch → separation → YouTube → export → remove → diagnostics).
- **Linux Bundle:** `ubuntu-22.04` build, glibc 2.35 floor, tar.gz canonical + tar.zst + thin AppImage, `run.sh` native/browser fallback, frozen self/smoke/separation tests, AppImage extraction, full Linux matrix, notices, budgets, no weights.
- **Windows Bundle:** `windows-2022` build, ZIP + per-user Inno, WebView2 present/absent, backend assertion, no console, icon/version resource, frozen tests on runner and real hardware, full Windows matrix, uninstall keeps data, unsigned + `SHA256SUMS` + SmartScreen note.
- **Release:** tagged on `main` with CI green; `release.yml` green; protected `publish`; `verify-release.sh` green; both frozen separations; budgets; completed `docs/qa/release-checklist.md`; counsel sign-off; immutable `v1.0.0`; release notes name the yt-dlp version.

Full DoD: [`HANDOFF.md`](../../../HANDOFF.md) §6.

### 6. Windows unmeasured figures

Windows startup, RTF, and RSS stay **unmeasured in this effort** ([16](16-windows-performance-verification.md) is out of scope). Windows acceptance is functional: the manual matrix on Windows 11 x64 and Windows 10 22H2 + ESU plus the frozen smoke on the Windows runner. Phase 8 executes 16's recipe and either meets the envelope or revises the README/release notes and discloses the number. Unmeasured is never treated as passing; no new Windows performance gate is invented.

### 7. Post-v1 backlog

No v1.1 theme is committed. Ordered candidates: **waveform → stem mixer → project library → batch export**, with **GPU** as a separate build, then a platform/quality tier (macOS/ARM, signing, auto-update, distro packages, i18n, draft/formant toggle, Save original, YouTube thumbnails, native passthrough, audio-only FFmpeg). Each names its existing seam and the decision it would need; see [`HANDOFF.md`](../../../HANDOFF.md) §8. This clears the map's last "Not yet specified" patch.

### 8. Go/no-go

- **Release GO only when:** GPL compliance and counsel sign-off complete; both bundles pass frozen smoke and the manual matrices; no weights in any artifact; bundle budgets pass; every supported-workflow error has copy and an action; `internal.unexpected` unreachable.
- **Release NO-GO:** licence posture unresolved; Windows backend assertion fails; an asset exceeds its fail budget.
- **Phase no-go:** the phase's automated gate or demo fails. Linux host GTK/WebKitGTK/GStreamer dependency and unmeasured Windows performance are disclosed limitations, not global no-gos.

### 9. Boundary and downstream

- **Decided here:** handoff location, scope freeze, acceptance form, phase order and gates, DoD, Windows posture, post-v1 order, go/no-go.
- **Left to implementation:** code-level choices inside the settled module tree — function names, file splits within `src/stemforge`, test fixtures. Product behavior, formats, limits, user-facing copy, and distribution stay decided; a new product decision gets a new issue, never a silent edit to a resolved ticket.
- **Map update:** append this decision to Decisions-so-far, clear **Not yet specified** (post-v1 seams now live in the handoff), and leave [17](17-app-icon-art.md) as the only open map ticket.
- **Glossary:** no change; no new product term is introduced. **ADR:** none, by the reversal/surprise/trade-off test.
- **Artifact:** [`HANDOFF.md`](../../../HANDOFF.md).

**2026-11-15 — icon resolved (ticket 17).** [App icon art](17-app-icon-art.md) chose **A — Badge** and delivered the five assets to `src/stemforge/assets/`; the rationale is in `.scratch/stemforge/prototypes/17-icon/RATIONALE.md`. `HANDOFF.md` §5 and §9 now carry the assets instead of the open item. No roadmap change; the map has no open tickets.
