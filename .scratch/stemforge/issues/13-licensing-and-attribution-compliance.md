# 13 — Licensing and attribution compliance

**Type:** task
**Status:** resolved
**Blocked by:** 02, 03, 04

## Question

Produce the GPL-3.0 compliance and attribution inventory for the v1 stack:

- Verify the licenses of Demucs, `htdemucs` weights, PyTorch, torchaudio, the ffmpeg build (GPL vs LGPL), Rubber Band/pedalboard, yt-dlp, NiceGUI, pywebview, and every bundled binary or model. The Demucs author states the weights' licence is unresolved; determine whether redistribution or only on-demand download is permissible.
- Confirm GPL compatibility and obligations: Rubber Band GPL-2.0-or-later (no commercial licence needed), FFmpeg GPLv3 source offer and exact configure line, libsndfile LGPL with dynamic `sndfile.dll`, libmp3lame, Deno/yt-dlp, PyInstaller's GPL-2 bootloader exception, and the notices to ship. Research 03 provides the compatibility table.
- Confirm the release-asset licensing tree: `LICENSES/`, `THIRD_PARTY_NOTICES.md`, corresponding source for bundled GPL binaries, and the About-dialog entry point.
- Draft `THIRD_PARTY_NOTICES` content and the in-app About/attribution entry point.
- Confirm source-offer obligations for bundled GPL binaries; note any model-weight redistribution constraints.
- Decide the ffmpeg build variant and document it.

The answer records the inventory, chosen variants, and the exact notices and source links to ship. AFK task.

## Answer

**Licensing inventory complete: StemForge v1 is GPL-3.0-only, ships no model weights, uses one GPLv3 FFmpeg provenance on both platforms, and generates its notices from the lock plus a curated binary manifest.**

Full inventory: [`../licensing/attribution-inventory.md`](../licensing/attribution-inventory.md). Draft shipped notice: [`../licensing/THIRD_PARTY_NOTICES.draft.md`](../licensing/THIRD_PARTY_NOTICES.draft.md). ADR: [`docs/adr/0009-demucs-weights-are-downloaded-not-bundled.md`](../../../docs/adr/0009-demucs-weights-are-downloaded-not-bundled.md).

### Decisions

- **StemForge licence:** `GPL-3.0-only`. Every bundled component is compatible. GPLv2-or-later components (Rubber Band, x264/x265, `mutagen`) are used under their "or later" option; LGPL-2.1/3.0 components (libsndfile, LAME, `lameenc`) are compatible, with LAME covered by FFmpeg's GPLv3 source offer.
- **Model weights (the ticket 10 dependency): do not bundle, mirror, or host them.** The Demucs README says the weights' licence is unclear (MusDB research-only data; FAIR models "likely" CC-NC-BY), and the HF model card has no licence tag. The first Separation offers an explicit, user-pressed download from `adefossez/HTDemucs` at revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`, `955717e8.safetensors` (84,025,440 bytes, SHA-256 `d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd`), with a notice that the weights are not covered by StemForge's licence. No offline pack in v1. This settles the map's "Model-weight acquisition" fog.
- **FFmpeg variant:** BtbN FFmpeg Builds **GPL static on both Linux and Windows**, release `autobuild-2026-11-15-12-40`, `ffmpeg-n9.0.2-57-gd838f9f9ae`:
  - Linux `ffmpeg-n9.0.2-57-gd838f9f9ae-linux64-gpl-9.0.tar.xz`, 152,700,372 bytes, SHA-256 `72836132863e0d40e2af74a8ef59c32b10771aed0c8540b634fb77db2e6e3ae2`.
  - Windows `ffmpeg-n9.0.2-57-gd838f9f9ae-win64-gpl-9.0.zip`, 194,990,151 bytes, SHA-256 `11b387a88ad24ee6d78bae7d24a2417834737e6ac00f0a36ca67fead1e924eff`.
  - Exact configure line is BtbN's `--enable-gpl --enable-version3 --disable-debug` plus every enabled script's `ffbuild_configure` flag; the authoritative record is the shipped `ffmpeg -buildconf`, captured in `LICENSES/ffmpeg/BUILDINFO.txt`.
  - gyan.dev is rejected (no published configure line, no SoXR); LGPL is rejected (loses `librubberband`, adds relinking). Drop `ffplay`, docs, presets. A custom audio-only build is a post-v1 size/compliance optimization, not v1.
- **Rubber Band 4.0.0** is built from source on both OSes (commit `1d95888b`) with builtin FFT/resampler and dynamic libsndfile, so the source offer is exactly Rubber Band + libsndfile, not the official zip's unknown static FFT stack.
- **libsndfile 1.2.2** is built from source as a shared library, WAV-only (`ENABLE_EXTERNAL_LIBS=OFF`, `ENABLE_MPEG=OFF`), shipped as `libsndfile.so.1` / `sndfile.dll`; LGPL-2.1 dynamic linking preserves the relink right.
- **YouTube:** use the `yt-dlp` wheel (Unlicense), `yt-dlp-ejs` (Unlicense/MIT/ISC), and a vendored Deno 2.9.8 (MIT) — not the yt-dlp PyInstaller binary (GPLv3+ combined work).
- **Notices mechanism:** `THIRD_PARTY_NOTICES.md`, `LICENSES/`, and `SOURCE_OFFER.md` are generated from `uv.lock` + build-venv `dist-info` + curated native/frontend manifests, committed, and CI-verified. No hand-maintained Python list.
- **Source offer:** GPLv3 §6(d) same-place source assets plus a §6(b) written offer valid three years. The third-party source bundle contains the FFmpeg commit, BtbN's build scripts at the pinned tag, every external library from `ffmpeg -buildconf` at its `SCRIPT_COMMIT`, Rubber Band, libsndfile, `lameenc`'s source + LAME 4.0, PyInstaller's bootloader source, and sdists for Python packages with GPL/LGPL/MPL components (`mutagen`, `certifi`, `bidict`, `tqdm`, `orjson`).
- **About/attribution:** ticket 08's `Settings & About` modal already has the rows; ticket 13 supplies Licence (GPL-3.0-only), Third-party notices (renders the bundled markdown), Corresponding source (opens `SOURCE_OFFER.md` + release page), a "Licences folder" action, and the legal paragraph. The model gate's "Licence details" deep-links to the weights section.

### Verified findings worth carrying forward

- **`torchaudio` is not a Demucs runtime dependency.** It is imported only by `demucs.train`, `demucs.wav`, and `demucs.repitch`; the `Separator` path never loads them. Do not install or bundle it; add a frozen-app smoke test that asserts it is absent.
- **PyTorch's CPU wheel ships a complete `dist-info/licenses/` tree** (BSD-3 plus oneDNN, fbgemm, LLVM OpenMP, sleef, protobuf, etc.); copy it verbatim. Linux also bundles `libgomp`/`libgfortran`, and Windows bundles `libiomp5md.dll`, which need curated GCC/LLVM runtime notices.
- **NumPy's wheel bundles OpenBLAS and, on Linux, GCC runtime libraries** (`libscipy_openblas64_`, `libgfortran`, `libquadmath`) without enumerating them in its `LICENSE.txt`; add curated notices. Treat GCC runtime libraries as System Libraries under GPLv3 §1 (notice + source link, excluded from the source bundle).
- **NiceGUI's wheel vendors Quasar 2.35.2, Vue 3.5.44, Socket.IO 4.8.4, Tailwind 4.3.4, DOMPurify 3.4.17, UnoCSS, and Roboto/Material Icons** but ships only its own MIT `LICENSE`. These need explicit frontend notices.
- **`lameenc` 1.9.0 is LGPL-3.0-or-later** and is imported unconditionally by `demucs.audio`; keep it, ship its source + LAME 4.0 + licence, and keep the extension module replaceable.
- **GPLv2-or-later is compatible with GPLv3**; no GPLv2-only, CDDL, EPL, or `--enable-nonfree` components are present.

### Implementation files named by this answer

`LICENSE`; `packaging/licenses/{direct,native,frontend,models}.toml`; `packaging/licenses/texts/`; `scripts/licensing/{generate_notices,verify_bundle,fetch_sources}.py`; `packaging/ffmpeg/vendor.sh`; `packaging/rubberband/build.sh`; `packaging/libsndfile/build.sh`; the `LICENSES/`, `THIRD_PARTY_NOTICES.md`, and `SOURCE_OFFER.md` entries in the PyInstaller spec and installer/AppImage file lists.

### Open items

- Counsel review of the weights-download posture and the source-offer wording before public v1 — a release gate, not a planning blocker.
- Final repository/release URL still uses `<REPO_URL>` / `<RELEASE_URL>` placeholders until ticket 10 fixes release naming.
- Windows code signing must use the same tagged source; if AppImage ever bundles host WebKitGTK/GStreamer, this inventory reopens.
- If the weights ever receive a licence, revisit bundling as a fresh decision (ADR 0009).


## Comments

**2026-11-15 — amendment from 10.** Two layout changes, neither of which changes the licence analysis:

- The licence tree ships **once**, at the Bundle root (`<bundle>/{LICENSE,THIRD_PARTY_NOTICES.md,SOURCE_OFFER.md,LICENSES/}`), not duplicated under `_internal/`. Ticket 10 makes the Bundle root a first-class resolution root (`Path(sys.executable).parent` when frozen, repo root in development), so the About modal, the AppImage's `usr/share/doc/stemforge/`, and `verify_bundle.py` all read the same single copy. §9's "plus `_internal/` copies as PyInstaller data" is superseded.
- `libsndfile.so.1` / `sndfile.dll` ship **beside** the Rubber Band executable (`vendor/rubberband/`), not in their own directory: Linux needs the `$ORIGIN` rpath and Windows only searches the executable's own directory. They remain separate, replaceable files, and `LICENSES/libsndfile/` still documents them.

The machine-readable binary pins live in `packaging/vendor.lock.json` (read by `vendor.sh`/`vendor.ps1` and the CI cache key); the inventory here remains the prose record. Release URLs are read from `packaging/release.toml` (ticket 18), and the `prepare` job refuses a tag while that file still contains `CHANGEME`.

**2026-11-15 — release URLs resolved by 18.** The `<REPO_URL>` / `<RELEASE_URL>` placeholders now have values:

- `repo_url` = `https://github.com/alexmarincu/stemforge`
- `releases_url` = `https://github.com/alexmarincu/stemforge/releases`
- `source_offer_contact` = `https://github.com/alexmarincu/stemforge/issues`
- human release page = `<releases_url>/tag/v<version>`; asset download = `<releases_url>/download/v<version>/<asset>`

These live only in `packaging/release.toml`; the generator substitutes them into `THIRD_PARTY_NOTICES.md` and `SOURCE_OFFER.md`, and the Inno script and README read the same file. The repository is public from the first commit and is created at implementation start, so the three-year written offer always has a live address. Tags are immutable once published; never force-move a `v<version>` tag the source offer references.
