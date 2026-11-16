# 17 — App icon art

**Type:** prototype
**Status:** resolved
**Blocked by:** 10

## Question

Produce the final StemForge icon art to the contract fixed by [10](10-packaging-and-distribution-plan.md):

- `src/stemforge/assets/stemforge.svg` — master, amber circular waveform mark, 4-bar glyph per [08](08-ui-workflow-and-layout.md) §12.
- `src/stemforge/assets/stemforge.ico` — 16, 20, 24, 32, 40, 48, 64, 128, and 256 px, for the Windows executable, Inno `SetupIconFile`, and the pywebview window icon.
- `src/stemforge/assets/stemforge.png` — 512 and 1024 px, for the AppImage and the About modal.

Resolve:

- The glyph itself: bar count, spacing, rounding, stroke versus filled bars, the circle's weight, and the negative space between bars and ring.
- Small-size legibility at 16 px and 20 px, plus rendering in the Windows title bar, taskbar, Start Menu, and Alt-Tab.
- Palette values against ticket 08's amber accent, and a monochrome-safe fallback for trays or themes that force a single colour.
- Whether the mark is amber-on-dark, amber-on-transparent, or a two-tone badge; and the AppImage `.DirIcon` treatment.

Deliver the three files plus a one-page rationale and the editable source. This is HITL: the human chooses between at least two candidates. It does not block tickets 11–16 or the handoff, but the packaging plan's asset paths are fixed and waiting.

## Prototype

[17-icon-candidates.html](../prototypes/17-icon/17-icon-candidates.html) — three candidate marks: **A Badge** (amber disc, ink bars), **B Ring** (amber ring and bars on transparency), **C Coin** (ink disc, amber bars). The page shows each at actual size, 16/20 px nearest-neighbour zooms, light/dark Windows title-bar and taskbar contexts, a 3/4/5-bar glyph study, the monochrome fallback, and the AppImage `.DirIcon` treatment. Open the file directly in a browser; no build or server. `?candidate=A` deep-links one mark.

Editable candidates: [`a-badge.svg`](../prototypes/17-icon/a-badge.svg), [`b-ring.svg`](../prototypes/17-icon/b-ring.svg), [`c-coin.svg`](../prototypes/17-icon/c-coin.svg). Regenerate the page with `python3 build_specimen.py` (needs `rsvg-convert`).

## Answer

**Decision: A — Badge.** The human chose the amber disc with four ink pill bars from the three HITL candidates in [the specimen](../prototypes/17-icon/17-icon-candidates.html). B (ring) and C (coin) were rejected on 16 px evidence. Full one-page rationale: [RATIONALE.md](../prototypes/17-icon/RATIONALE.md).

### Delivered

| Path | Contents |
|---|---|
| `src/stemforge/assets/stemforge.svg` | Master, 256 grid, transparent field; AppImage scalable icon and editable source. |
| `src/stemforge/assets/stemforge.ico` | 16, 20, 24, 32, 40, 48, 64, 128, 256 px; DIB 16–128, PNG 256; Windows exe, Inno `SetupIconFile`, pywebview window icon. |
| `src/stemforge/assets/stemforge-512.png` | RGBA; About modal and AppImage `hicolor/512x512`. |
| `src/stemforge/assets/stemforge-1024.png` | RGBA; high-DPI/source/readme raster. |
| `src/stemforge/assets/stemforge-mono.svg` | `currentColor` fallback: disc with the bars knocked out as even-odd holes. |

The contract's singular `stemforge.png` is split into `stemforge-512.png` and `stemforge-1024.png`; one PNG cannot carry both sizes. Ticket 10 carries a dated comment with the final names.

### Resolved sub-questions

- **Glyph:** four bars, one per Demucs stem. Filled pills, not strokes: width 32, gaps 16, heights 76/132/100/140, `rx=16`. Three bars lose the waveform rhythm; five turn into a picket fence at 16 px (glyph study in the specimen).
- **Circle:** solid amber disc, centre 128, r 116, 12-unit canvas margin, no ring stroke. The bar block is 176 × 140; 28 units clear to the disc edge at the sides, 46 units above/below the tallest bar. The amber field is the "ring".
- **Small sizes:** at 16 px the disc is 14.5 px, bars 2 px, gaps 1 px; all four bars stay distinct in a nearest-neighbour render. At 20 px: 2.5 px bars, 1.25 px gaps. The ICO carries 16/20/24/32/40/48/64/128/256 for the Windows title bar, taskbar, Start menu, Alt-Tab, jumbo views and installer.
- **Palette and field:** amber `#F2A65A` (ticket 08 `--accent`) and ink `#1D1204` (prototype logo), transparent outside the disc. Two-tone badge, not amber-on-dark and no baked square. Ink-on-amber is ~9.1:1; amber-on-white is ~2.0:1, so the dark bars carry the 16 px detail.
- **Monochrome:** `stemforge-mono.svg` with `currentColor` and even-odd holes. It survives trays, forced-colour themes and high-contrast mode; v1 has no tray, so it is a future/forced-context asset.
- **AppImage `.DirIcon`:** a symlink to `usr/share/icons/hicolor/scalable/apps/stemforge.svg`; the AppDir also ships `stemforge-512.png` under `hicolor/512x512/apps/`. If a future `appimagetool` rejects SVG, packaging falls back to the 512 px PNG.

### Reproduction

`python3 .scratch/stemforge/prototypes/17-icon/make_assets.py` (needs `rsvg-convert` and Pillow) writes the five assets and asserts all nine ICO sizes, both RGBA PNGs and the mono render. The artwork is original, drawn for StemForge with geometric primitives; no third-party marks or fonts, GPL-3.0-only with the project, no `THIRD_PARTY_NOTICES` entry.

## Comments

**2026-11-15 — HITL choice and asset build.** The human chose A — Badge from the three candidates. Final assets were generated from the winning SVG; all nine ICO entries load, both PNGs are RGBA 512/1024, and the mono path renders. `src/stemforge/assets/` now holds the five files. Candidate record and generator: `.scratch/stemforge/prototypes/17-icon/`.
