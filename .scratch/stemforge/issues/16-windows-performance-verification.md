# 16 — Windows performance and startup verification

**Type:** task
**Status:** resolved
**Blocked by:** 10

## Question

Measure the frozen Windows bundle on real Windows hardware:

- Startup on Windows 11 x64 (primary) and Windows 10 22H2 + ESU (best-effort): cold and warm time to window and to HTTP 200, with and without Defender real-time scanning, plus `import torch` time.
- 4-minute `htdemucs` Separation wall time, RTF, and peak RSS; 30-minute block-mode Separation peak RSS; a 4-minute Pitch render.
- Bundle size on disk and the portable ZIP / Inno Setup output sizes.
- Confirm whether Windows fits ticket 12's envelope (RTF 1.5 planning, ~3 GB block peak, 199 MB base tar.zst) or needs its own numbers, limits, or hardware minimum.

This is a task because the numbers require running the frozen app on Windows; it unblocks 15's Windows acceptance criteria. Answer records the measurements, methodology, and any revision to [12](12-performance-and-resource-envelope.md).

## Answer

**Ruled out of scope for this map, 2026-11-15, while resolving [10](10-packaging-and-distribution-plan.md).** Every measurement here requires a built, frozen Windows bundle, and this map ends at the handoff: implementation and scaffolding are a separate effort (see the map's Out of scope). The Windows figures therefore stay deliberately unmeasured in the handoff — [12](12-performance-and-resource-envelope.md) already discloses "Windows/GPU and full-app startup unmeasured", and 10's release checklist plus the frozen `--self-test` / `--smoke-test` / `--smoke-separation` flags define what the implementation effort must verify. This file is the ready-made recipe for that effort; reopen it there rather than on this map.

**Why now:** 10 resolved the packaging plan, which is the last planning input this task needed, and exposed that the task itself executes a bundle this effort does not build. It blocks nothing: 15's `Blocked by` line never included it, and 15 carries the unmeasured-Windows limitation instead.
