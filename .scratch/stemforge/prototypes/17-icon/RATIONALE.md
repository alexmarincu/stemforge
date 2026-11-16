# StemForge icon — rationale

**Chosen mark: A — Badge.** Amber disc, four ink bars. Picked by the human from the three HITL candidates in [the specimen](17-icon-candidates.html) on 2026-11-15; B (ring) and C (coin) were rejected on 16 px evidence.

## The mark

| Property | Value | At 16 px |
|---|---|---|
| Canvas | 256 × 256, transparent | — |
| Disc | centre 128,128 · r 116 · `#F2A65A` | 14.5 px diameter |
| Bars | 4 filled pills · x 40/88/136/184 · w 32 · gaps 16 · rx 16 | 2 px wide, 1 px gaps |
| Heights | 76 / 132 / 100 / 140 · vertically centred | 4.75 / 8.25 / 6.25 / 8.75 px |
| Clear space | 28 units to the disc edge at the sides; 46 above/below the tallest bar | 1.75 / 2.9 px |
| Palette | amber `#F2A65A`, ink `#1D1204`, transparent field | — |
| Effects | none: no stroke, gradient, shadow, or baked square | — |

The amber is ticket 08's `--accent`; the ink is the prototype logo's `#1D1204`. The mark is the badge the UI prototype already draws at 15 px, redrawn on a 256 grid so the small sizes are intentional rather than resampled.

## Why this glyph

Four bars are the four Demucs stems. The glyph study in the specimen shows three bars losing the waveform rhythm and five turning into a picket fence at 16 px. The bars are **filled pills, not strokes**: a stroke would fall below 2 px at 16 and its caps would merge. Heights `0.54 / 0.94 / 0.71 / 1.00` of the tallest give a readable rhythm without a symmetrical "equaliser" look. The tallest bar keeps 46 units (19.8% of the disc height) clear of the disc edge, so the badge never looks clipped.

## Small sizes and Windows

The ICO carries 16, 20, 24, 32, 40, 48, 64, 128 and 256 px. The 16 and 20 entries are rendered from the same master; the nearest-neighbour zooms in the specimen show all four bars separate, with the disc still reading as a circle. Windows asks for 16 in the title bar, 20 at 125%, 24/32 on the taskbar, 40/48 for Alt-Tab and the Start menu, and 64/128/256 for jumbo views, the installer and the exe resource.

On light chrome the amber edge is only ~2.0:1 against white; the ink-on-amber bars carry the detail at ~9.1:1. That internal contrast is why the badge survives where the ring does not: B's 1.25 px amber ring breaks into a dotted circle at 16 px, and amber-on-white has no dark core. On dark chrome the amber disc is the bright anchor; C's `#1D1204` disc disappears into the `#0E1014` surface, leaving only bars.

## Field and monochrome

The field is **transparent**: a two-tone badge (amber disc, ink glyph) with no baked square, so it sits on light, dark and coloured chrome without a plate. `stemforge-mono.svg` is the fallback for trays, forced-colour themes and high-contrast mode: one `currentColor` disc with the bars knocked out as even-odd holes. A filled disc plus filled bars would merge into a blob in one colour; the holes keep the waveform at any tint. v0.1.0 has no tray, so the mono file is a deliverable for future/forced contexts, not a v0.1.0 UI asset.

## AppImage and asset map

`.DirIcon` is a **symlink** to `usr/share/icons/hicolor/scalable/apps/stemforge.svg`; the AppDir also ships `stemforge-512.png` under `hicolor/512x512/apps/`. The spec accepts SVG for `.DirIcon`, and one master feeds the scalable icon, the raster and the About modal. If a future `appimagetool` rejects SVG, packaging falls back to the 512 px PNG.

| File | Use |
|---|---|
| `src/stemforge/assets/stemforge.svg` | master, 256 grid; AppImage scalable icon, editable source |
| `src/stemforge/assets/stemforge.ico` | Windows exe, Inno `SetupIconFile`, pywebview window icon; 9 sizes |
| `src/stemforge/assets/stemforge-512.png` | About modal; AppImage `hicolor/512x512` |
| `src/stemforge/assets/stemforge-1024.png` | high-DPI/source/readme raster |
| `src/stemforge/assets/stemforge-mono.svg` | one-colour fallback (tray/forced theme) |

The contract's singular `stemforge.png` is split into `stemforge-512.png` and `stemforge-1024.png`: one PNG cannot carry both sizes. Ticket 10 carries a dated comment noting the names; `SetupIconFile` is unchanged.

## Reproduction and provenance

```sh
python3 .scratch/stemforge/prototypes/17-icon/make_assets.py
```

The generator writes the five assets and asserts all nine ICO sizes load, both PNGs are RGBA, and the mono path renders. It needs `rsvg-convert` and Pillow. The three candidate SVGs and the self-contained review page remain in the same folder as the decision record.

The artwork is original, drawn for StemForge with geometric primitives; no third-party marks, fonts or traced assets. It is GPL-3.0-only with the project and needs no `THIRD_PARTY_NOTICES` entry.
