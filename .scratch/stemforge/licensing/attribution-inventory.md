# StemForge v0.1.0 — GPL-3.0 compliance and attribution inventory

**Status:** settled by ticket 13 (2026-11-15). This is the implementation input for packaging (10), testing (11), and the handoff (15).
**Scope:** every component StemForge v0.1.0 conveys to a user — the app, the Python runtime and wheels, the frozen binaries, the frontend assets, and the model weights it does *not* convey.
**Not legal advice.** Counsel should review the notices, the source offer, and the weights-download posture before the first public release.

Related artifacts:

- [`THIRD_PARTY_NOTICES.draft.md`](./THIRD_PARTY_NOTICES.draft.md) — the drafted shipped notice.
- [`../../../docs/adr/0009-demucs-weights-are-downloaded-not-bundled.md`](../../../docs/adr/0009-demucs-weights-are-downloaded-not-bundled.md)
- Research: [`../research/02-separation-engine.md`](../research/02-separation-engine.md), [`../research/03-pitch-shift-and-media-pipeline.md`](../research/03-pitch-shift-and-media-pipeline.md), [`../research/04-packaging-and-ci.md`](../research/04-packaging-and-ci.md).

---

## 1. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **StemForge is GPL-3.0-only** (SPDX: `GPL-3.0-only`). | Matches the map's standing decision. Every bundled component is GPLv3-compatible. GPLv2-or-later components (Rubber Band, x264/x265 inside FFmpeg, `mutagen`) are used under their "or later" option. |
| D2 | **Do not bundle, mirror, or redistribute the Demucs weights.** First-run download from Hugging Face at a pinned revision + SHA-256, behind an explicit consent gate. | The Demucs author states the weights' licence is unresolved and that MusDB contained research-only data; FAIR models are "likely" CC-NC-BY. Redistribution is a legal risk disproportionate to 84 MB. |
| D3 | **FFmpeg/ffprobe: BtbN FFmpeg Builds, GPL static, same provenance on Linux and Windows**, pinned to `autobuild-2026-11-15-12-40`, `ffmpeg-n9.0.2-57-gd838f9f9ae`. Drop `ffplay`, docs, and presets. | BtbN publishes the build scripts and exact dependency commits, so Corresponding Source is reproducible. gyan.dev's Windows build is smaller but publishes no configure line and omits libsoxr. LGPL builds lose `librubberband` and add relinking duties. |
| D4 | **Rubber Band 4.0.0 is built from source on both OSes**, with builtin FFT/resampler and a dynamically linked libsndfile. | Removes the official Windows zip's unknown static fftw/libsamplerate provenance. Source offer is then exactly Rubber Band + libsndfile. |
| D5 | **libsndfile 1.2.2 is built from source as a shared library**, WAV-only (`ENABLE_EXTERNAL_LIBS=OFF`, `ENABLE_MPEG=OFF`), shipped as `libsndfile.so.1` / `sndfile.dll`. | LGPL-2.1 dynamic linking keeps the relink right; WAV is the only format the Rubber Band CLI needs. |
| D6 | **Use the yt-dlp wheel, not its PyInstaller binary**, with `yt-dlp-ejs` and a vendored Deno 2.9.8 binary. | The wheel is Unlicense; the standalone binary is GPLv3+ as a combined work. This avoids a second GPL runtime to notice and ship. |
| D7 | **Notices are generated, committed, and CI-verified** — never hand-maintained for the Python dependency graph. | `uv.lock` + build-venv `dist-info` is the source of truth; a curated manifest covers native binaries and NiceGUI's frontend assets. |
| D8 | **Source offer uses GPLv3 §6(d) plus a §6(b) written offer**: source assets live on the same release page, and a three-year written offer names a contact. | Same-place download is the cleanest GPLv3 route for a downloadable binary; the written offer is a fallback. |
| D9 | **The About modal is the in-app attribution entry point**, reached from `Settings & About` in the rail footer; the first-run model gate deep-links its "Licence details" there. | Ticket 08 already fixed the entry point and the two placeholders ("Third-party notices", "Corresponding source"). Ticket 13 supplies the content and behaviour. |
| D10 | **The model gate must not download silently** and must state that the weights are not part of StemForge and are not covered by its licence. | Consent is the legal and product boundary; see §7. |

Rejected:

- **Bundling `htdemucs`** (or an opt-in offline pack hosted by us) — same redistribution risk; revisit only after counsel clears it.
- **gyan.dev essentials on Windows** — 34 MB is attractive, but there is no published configure line to reproduce Corresponding Source, and libsoxr is absent.
- **BtbN LGPL shared** — no `librubberband`, and shared-library rpath/DLL search adds runtime failure modes for no licensing gain (the app is GPL anyway).
- **Official Rubber Band Windows zip** — `sndfile.dll` is fine, but the executable's static FFT/resampler provenance is not documented.
- **yt-dlp's standalone binary** — a second Python runtime and a GPLv3+ combined work to notice.
- **Bundling model weights** — see D2.

---

## 2. Licence compatibility table

Versions below are the current resolution; the generated `THIRD_PARTY_NOTICES.md` and `LICENSES/python/DEPENDENCIES.md` are authoritative if `uv.lock` moves.

| Component | Version / pin | Licence | Bundled? | GPL-3.0-only compatible? | Obligation |
|---|---|---|---|---|---|
| StemForge | `<version>` | `GPL-3.0-only` | yes (the app) | — | Ship `LICENSE`; offer Corresponding Source incl. build scripts. |
| Python (CPython) | 3.13.x per `uv.lock` | `PSF-2.0` | yes | yes | Notice + `LICENSE.txt`. |
| PyInstaller bootloader | 6.22.0 | `GPL-2.0-or-later` **with bootloader exception** | yes | yes (exception) | Notice + `COPYING.txt`; source link. |
| FFmpeg / ffprobe | `n9.0.2-57-gd838f9f9ae` (BtbN GPL static) | `GPL-3.0` | yes | yes | Notice, exact configure line, source offer incl. every linked external library. |
| Rubber Band | 4.0.0, commit `1d95888b` | `GPL-2.0-or-later` | yes | yes (v3 is an allowed later option) | Notice + source offer + build script. |
| libsndfile | 1.2.2 | `LGPL-2.1` | yes (shared) | yes | Notice + source + relink right (dynamic `.so`/`.dll`). |
| LAME (`libmp3lame`, inside FFmpeg) | SVN r7113 (BtbN pin) | `LGPL-2.1` | yes (static inside ffmpeg) | yes | Covered by FFmpeg's GPLv3 source offer; notice. |
| Demucs (code) | 4.1.0 | `MIT` | yes | yes | Notice. |
| Demucs weights (`htdemucs`) | HF revision `cbc8a9b1…` | **No licence granted; author says unclear, likely non-commercial** | **no** | n/a | Do not distribute; consent + notice in the app. |
| PyTorch | 2.15.0+cpu | `BSD-3-Clause` + bundled third-party (Apache-2.0, BSD-2/3, BSL-1.0, ISC, MIT, Zlib, LLVM exception) | yes | yes | Ship the wheel's `dist-info/licenses/` tree verbatim; curated GCC/LLVM runtime notices. |
| NumPy | 2.5.4 | `BSD-3-Clause` + bundled OpenBLAS (BSD-3) and, on Linux, GCC runtime (`libgfortran`, `libquadmath`) | yes | yes | Notice; curated OpenBLAS + GCC Runtime Library Exception notices. |
| `sphn` | 0.2.1 | `Apache-2.0` | yes | yes | Notice. |
| `julius` | 0.2.8 | `MIT` | yes | yes | Notice. |
| `safetensors` | 0.8.0 | `Apache-2.0` | yes | yes | Notice. |
| `huggingface-hub` | 2.4.0 | `Apache-2.0` | yes | yes | Notice. |
| `hf-xet` | 1.8.0 | `Apache-2.0` | yes | yes | Notice. |
| `einops` | 0.8.2 | `MIT` | yes | yes | Notice. |
| `PyYAML` | 6.0.3 | `MIT` | yes | yes | Notice. |
| `tqdm` | 4.70.1 | `MPL-2.0 AND MIT` | yes | yes | Notice + source (MPL-covered files; sdist). |
| `lameenc` | 1.9.0 | `LGPL-3.0-or-later` | yes | yes | Notice + source + relink right (separate extension module). |
| NiceGUI | 4.0.0 | `MIT` | yes | yes | Notice + frontend asset notices (§6). |
| pywebview | 6.2.1 | `BSD-3-Clause` | yes | yes | Notice. |
| pythonnet | 3.2.1 | `MIT` | yes (Windows) | yes | Notice. |
| clr-loader | 0.3.1 | `MIT` | yes (Windows) | yes | Notice. |
| proxy-tools | 0.1.0 | `MIT` | yes | yes | Notice. |
| bottle | 0.13.4 | `MIT` | yes | yes | Notice. |
| yt-dlp | 2026.10.26 | `Unlicense` | yes | yes | Notice. |
| yt-dlp-ejs | 0.8.0 | `Unlicense AND MIT AND ISC` | yes | yes | Notice. |
| Deno | 2.9.8 | `MIT` | yes | yes | Notice. |
| `mutagen` | 1.48.1 | `GPL-2.0-or-later` | yes (yt-dlp `default` extra) | yes (v3 is an allowed later option) | Notice + source (sdist in the third-party source bundle). |
| `certifi` | 2026.10.23 | `MPL-2.0` | yes | yes | Notice + source (sdist in the third-party source bundle). |
| `brotli` | 1.2.1 | `MIT` | yes | yes | Notice. |
| `pycryptodomex` | 3.24.1 | BSD / public domain | yes | yes | Notice. |
| `requests` | 2.34.2 | `Apache-2.0` | yes | yes | Notice. |
| `urllib3` | 2.8.0 | `MIT` | yes | yes | Notice. |
| `websockets` | 17.4 | `BSD-3-Clause` | yes | yes | Notice. |
| FastAPI / Starlette / Uvicorn | 0.163.1 / 1.10.0 / 0.54.0 | `MIT` / `BSD-3-Clause` / `BSD-3-Clause` | yes | yes | Generated notice. |
| aiohttp | 3.14.5 | `Apache-2.0 AND MIT` | yes | yes | Generated notice. |
| httpx2 | 2.16.0 | `BSD-3-Clause` | yes | yes | Generated notice. |
| python-socketio | 5.18.1 | `MIT` | yes | yes | Generated notice. |
| remaining transitive wheels | per `uv.lock` | permissive (MIT/BSD/Apache/PSF/MPL) | yes | yes | Generated table + `LICENSES/python/`. |

**Not bundled (and must not be):** `torchaudio` (Demucs imports it only from `demucs.train`, `demucs.wav`, `demucs.repitch`; the `Separator` path never loads them), `pedalboard`, `librosa`, `SoundTouch`, `openunmix`, `htdemucs_ft` / `htdemucs_6s` weights.

**GPLv3 compatibility notes:**

- `GPL-2.0-or-later` → GPLv3 is one of the "later" options (GNU GPL FAQ).
- `LGPL-2.1` (libsndfile, LAME) and `LGPL-3.0-or-later` (lameenc) are compatible; the LGPL is satisfied by dynamic linking and a source/relink path, except LAME, which is statically linked into FFmpeg and therefore covered by the FFmpeg GPLv3 source offer.
- `Apache-2.0` and `MPL-2.0` are compatible with GPLv3; no additional restrictions.
- No GPLv2-only, CDDL, EPL, or `--enable-nonfree` components are present. BtbN's `nonfree` variants (fdk-aac, etc.) are not used.

---

## 3. FFmpeg build variant (D3)

**Pinned release:** BtbN `autobuild-2026-11-15-12-40` (2026‑11‑15).

| Platform | Asset | Bytes | SHA-256 |
|---|---|---|---|
| Linux x86_64 | `ffmpeg-n9.0.2-57-gd838f9f9ae-linux64-gpl-9.0.tar.xz` | 152,700,372 | `72836132863e0d40e2af74a8ef59c32b10771aed0c8540b634fb77db2e6e3ae2` |
| Windows x64 | `ffmpeg-n9.0.2-57-gd838f9f9ae-win64-gpl-9.0.zip` | 194,990,151 | `11b387a88ad24ee6d78bae7d24a2417834737e6ac00f0a36ca67fead1e924eff` |

**Licence:** GPLv3. BtbN's `variants/defaults-gpl.sh` sets `--enable-gpl --enable-version3 --disable-debug` and `LICENSE_FILE="COPYING.GPLv3"`.

**Exact configure line:** the base above plus every `ffbuild_configure` flag of the scripts enabled for the GPL variant at the pinned tag. The authoritative record is the shipped binary's own `ffmpeg -hide_banner -buildconf`, captured at vendor time into `LICENSES/ffmpeg/BUILDINFO.txt`. It includes the external library list (x264, x265, LAME, Opus, Vorbis, Rubber Band, SoXR, zimg, OpenSSL, …); that list drives the source bundle, not a hand-written list.

**Vendoring:** mirror both archives into StemForge's release assets, verify the SHA-256 above, then unpack only `bin/ffmpeg`, `bin/ffprobe`, and the licence/notice files. Drop `ffplay`, `doc/`, `man/`, and `presets/`.

**Why not gyan.dev:** its Windows essentials build is 34 MB and includes `librubberband`/LAME/Opus/Vorbis, but the page does not publish the configure line or exact library commits, and it omits SoXR. That is a weaker Corresponding Source story for a 160 MB saving we do not need.

**Why not LGPL:** the LGPL variants disable `librubberband`, and a static LGPL binary adds relinking obligations. Since StemForge is GPLv3 anyway, the GPL build is the simpler boundary.

**Post-v0.1.0 option:** a project-built, audio-only GPLv3 FFmpeg (all audio decoders, WAV/FLAC/MP3 muxers, `libmp3lame`, no video/network) would shrink both the bundle and the source offer. Research 03 scored it a v0.2.0 optimization, not a v0.1.0 blocker; it is deliberately **not** in v0.1.0.

---

## 4. Other bundled binaries

### Rubber Band 4.0.0 (D4)

- **Source:** `https://github.com/breakfastquay/rubberband` tag `v4.0.0`, commit `1d95888bec3ae0a17c0c4af791810d5a63f6bc35`; tag archive `rubberband-4.0.0.tar.gz` is 290,305 bytes, SHA-256 `24300f48a8014b7c863b573a9647e61b1b19b37875e2cdd92005e64c6424d266` (the commit is authoritative).
- **Licence:** GPL-2.0-or-later (`COPYING`). No commercial licence needed for a GPLv3 app.
- **Build (both OSes):**
  ```
  meson setup build \
    -Dfft=builtin -Dresampler=builtin \
    -Dcmdline=enabled -Dtests=disabled \
    -Dladspa=disabled -Dlv2=disabled -Dvamp=disabled -Djni=disabled \
    --prefix=<bundle>/vendor/rubberband
  ninja -C build && ninja -C build install
  ```
- **Link:** dynamic libsndfile only; no FFTW, no libsamplerate. Ship `rubberband` / `rubberband.exe`, `COPYING`, and `BUILDINFO.txt` (meson options + `--version` output).
- **Why build:** the official Windows zip's static FFT/resampler provenance is undocumented; building with builtin FFT/resampler makes the source offer exact and removes two dependencies.

### libsndfile 1.2.2 (D5)

- **Source:** `https://github.com/libsndfile/libsndfile/releases/tag/1.2.2`; `libsndfile-1.2.2.tar.xz` is 730,760 bytes, SHA-256 `3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e`.
- **Licence:** LGPL-2.1 (`COPYING`).
- **Build (both OSes):**
  ```
  cmake -S . -B build \
    -DBUILD_SHARED_LIBS=ON \
    -DENABLE_EXTERNAL_LIBS=OFF \
    -DENABLE_MPEG=OFF \
    -DBUILD_PROGRAMS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_TESTING=OFF \
    -DINSTALL_PKGCONFIG_MODULE=OFF \
    -DCMAKE_BUILD_TYPE=Release
  ```
- **Link:** shared `libsndfile.so.1` / `sndfile.dll`, kept as a separate replaceable file next to `rubberband`. Ship `COPYING`, `BUILDINFO.txt`, and the source.
- **Relink right:** the user can replace the shared library; no static linking into `rubberband`.

### Deno 2.9.8 (D6)

| Platform | Asset | SHA-256 |
|---|---|---|
| Linux x86_64 | `deno-x86_64-unknown-linux-gnu.zip` | `e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609` |
| Windows x64 | `deno-x86_64-pc-windows-msvc.zip` | `cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf` |

- **Licence:** MIT. Notice only; source link.
- **Reason:** yt-dlp's EJS solver needs Deno; no runtime updater (ADR 0007).

---

## 5. Python runtime

**Source of truth:** the committed `uv.lock`, plus the build virtualenv's `*.dist-info/licenses/` directories. The generator copies every wheel's licence tree verbatim into `LICENSES/python/<dist>/` and emits the component table. The compatibility table above covers the components that need human judgement; the rest are permissive and mechanically listed.

Special cases the generator cannot infer:

| Package | Issue | Action |
|---|---|---|
| PyTorch | Wheel's `dist-info/licenses/third_party/` is the authoritative notice set. Linux bundles `libgomp`/`libgfortran`; Windows bundles `libiomp5md.dll`. | Copy the whole `licenses/` tree; add GCC Runtime Library Exception and LLVM OpenMP notices (§8). |
| NumPy | Wheel bundles `libscipy_openblas64_`, `libgfortran`, `libquadmath`; its `LICENSE.txt` does not enumerate them. | Add curated OpenBLAS BSD-3 notice and GCC Runtime Library Exception notice. |
| `lameenc` | LGPL-3.0-or-later; a compiled extension loaded in-process, but not used by StemForge (Demucs imports it unconditionally). | Ship source (GitHub `v1.9.0`, commit `77aba334`) + LAME 4.0 (SHA-256 `3df5124d…16eb`), licence, and keep the extension module replaceable. |
| `torchaudio` | Not a Demucs runtime dependency; only `demucs.train`/`wav`/`repitch` import it, and the `Separator` path never loads them. | **Do not install or bundle**; CI smoke test asserts it is absent. |
| `mutagen` | Pulled by `yt-dlp[default]`; GPL-2.0-or-later. | Notice + source (sdist in the third-party source bundle). |
| `certifi`, `bidict` | MPL-2.0-only; no permissive alternative. | Notice + source (sdists in the third-party source bundle). |
| `tqdm`, `orjson` | MPL-2.0 combined with MIT/Apache; the MPL applies to the MPL-covered files. | Notice + source (sdists in the third-party source bundle). |
| `docutils` | Public-domain/BSD core with some GPL-2 parts; inspect the wheel's licence files. | Notice; include the sdist if the shipped files carry the GPL parts. |
| `setuptools`, `pip` | Build/loader tooling may be collected by PyInstaller. | Generated notice if present; exclude `pip` if not needed at runtime. |

**Frozen-build smoke gate:** import `demucs.api.Separator`, load the pinned weights, and run a 2-second fixture; assert `torchaudio` is not importable in the frozen app. This is both a licensing check and a size check.

---

## 6. NiceGUI frontend assets

NiceGUI 4.0.0's wheel ships only its own MIT `LICENSE`; it also vendors the following into `nicegui/static/`. These are conveyed, so they need notices.

| Asset | Version | Licence | Notice |
|---|---|---|---|
| Quasar Framework (`quasar.umd*.js`, CSS, `lang/`) | 2.35.2 | MIT | yes |
| Vue (`vue.esm-browser*.js`) | 3.5.44 | MIT | yes |
| Socket.IO client (`socket.io.min.js`) | 4.8.4 | MIT | yes |
| Tailwind CSS (`tailwindcss.min.js`) | 4.3.4 | MIT | yes |
| DOMPurify (`dompurify.mjs`) | 3.4.17 | Apache-2.0 OR MPL-2.0 | yes |
| UnoCSS (`unocss/*.global.js`) | bundled by NiceGUI 4.0.0 | MIT | yes |
| Roboto + Material Icons (`fonts/*.woff2`) | Google Fonts | Apache-2.0 | yes |

The generator adds `packaging/licenses/frontend.toml` entries; `LICENSES/nicegui/frontend/` holds the texts. Do not rely on NiceGUI's own `LICENSE` to cover these.

---

## 7. Model weights (D2)

**Fact:** the weights have **no granted licence**. The Demucs README (§License) says:

> "The license of the weights is not clear: MusDB contains some data that were only usable for research purpose. I sadly cannot help granting a license… The 6 source variant of htdemucs was trained only on Meta internal dataset, which likely means CC-NC-BY license is applicable, as it was the case for all models released by FAIR."

The Hugging Face model card (`adefossez/HTDemucs`, revision `cbc8a9b1a87023b7fd74e7b3412e6321c0eab003`) carries **no `license` tag and no licence section**.

**Pinned artifact (for the downloader, not for redistribution):**

| File | Bytes | SHA-256 |
|---|---|---|
| `955717e8.safetensors` | 84,025,440 | `d9fa14133cfcc034a6758923bb3a8ca9f8dfd0b582134643bbf83f72c17576dd` |
| `955717e8.json` | 12,087 | record at vendor time |
| `htdemucs.yaml` | 21 | record at vendor time |

**Policy:** StemForge never hosts, mirrors, or ships these files. The app downloads them from Hugging Face at the user's explicit request, verifies the SHA-256, stores them in the cache, and re-verifies before each Separation. "Clear cache" may delete them; the user can re-download.

**In-app copy (model gate):**

> **Separation model not installed** — `htdemucs`, 84 MB, downloaded once from `huggingface.co/adefossez/HTDemucs` at revision `cbc8a9b1…` and verified by SHA-256.
>
> These weights are **not part of StemForge** and are **not covered by StemForge's GPL-3.0 licence**. The Demucs author states their licence is unclear: the training data included research-only MusDB material, and FAIR's models are likely non-commercial. Download only if that is acceptable to you.
>
> [Download model] [Licence details]

The About modal repeats this in one sentence and links to the Demucs README and the HF model page. The consent is a button press, not a checkbox pre-ticked by default.

**No offline model pack** in v0.1.0. Revisit only after counsel clears redistribution; an offline pack hosted by us is still redistribution.

---

## 8. Curated native-library notices

The following are conveyed inside wheels but are not enumerated by the wheels' own licence files. Add them manually to `packaging/licenses/native.toml` and `LICENSES/native/`:

| Library | Where | Licence | Notice |
|---|---|---|---|
| `libgomp` (Linux) | torch wheel | GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1 | GCC runtime notice + source link; system-library treatment. |
| `libgfortran`, `libquadmath` (Linux) | torch and numpy wheels | GPL-3.0-or-later WITH GCC Runtime Library Exception 3.1 | same. |
| `libiomp5md.dll` (Windows) | torch wheel | Apache-2.0 WITH LLVM-exception (LLVM OpenMP) | `llvm-openmp/LICENSE.txt` from the wheel + notice. |
| `libscipy_openblas64_` | numpy wheel | BSD-3-Clause (OpenBLAS) | OpenBLAS copyright + licence. |
| OpenSSL (`libssl`/`libcrypto`, Windows Python) | CPython runtime | Apache-2.0 | Apache-2.0 text + notice. |
| `libffi`, `zlib`, `expat`, `bzip2`, `xz`, `sqlite3`, `libmpdec` | CPython runtime | MIT / Zlib / MIT / bzip2-1.0.6 / 0BSD / public domain / BSD-2 | Notice per bundled file; sourced from the Python distribution's licence files where present. |
| MSVC/UCRT runtime DLLs (Windows) | CPython runtime | Microsoft redistributable terms | Microsoft notice; system-library treatment. |

**System-library treatment:** GCC runtime libraries and the OS/compiler runtime DLLs are "System Libraries" under GPLv3 §1. They still get a notice and a source link, but are excluded from the Corresponding Source bundle. If counsel prefers a stricter reading, add the GCC release source to the source bundle; the mechanism is the same.

---

## 9. Release licensing tree

Every artifact must contain the following. The app resolves them from the PyInstaller data directory (`sys._MEIPASS`) when frozen, and from the repo root in development; a duplicate human-readable copy sits next to the executable for file-manager/Start-Menu access.

```
LICENSE                              # GPL-3.0-only, repo root and bundle root
THIRD_PARTY_NOTICES.md               # generated, committed, shipped
SOURCE_OFFER.md                      # generated, committed, shipped
LICENSES/
  README.md                          # index: component -> licence -> file
  GPL-3.0-only.txt
  GPL-2.0-or-later.txt
  LGPL-2.1.txt
  LGPL-3.0-or-later.txt
  Apache-2.0.txt
  MIT.txt
  BSD-2-Clause.txt
  BSD-3-Clause.txt
  ISC.txt
  MPL-2.0.txt
  Unlicense.txt
  PSF-2.0.txt
  Zlib.txt
  BSL-1.0.txt
  bzip2-1.0.6.txt
  GCC-exception-3.1.txt
  LLVM-exception.txt
  ffmpeg/
    COPYING.GPLv3
    BUILDINFO.txt                    # ffmpeg -version + -buildconf + asset SHA-256
  rubberband/
    COPYING
    BUILDINFO.txt
  libsndfile/
    COPYING
    BUILDINFO.txt
  python/
    LICENSE.txt
    <per-wheel licences copied from dist-info>
  pytorch/
    <verbatim dist-info/licenses tree>
  numpy/
    LICENSE.txt
    OPENBLAS.txt
    GCC-RUNTIME.txt
  nicegui/
    LICENSE
    frontend/
      quasar.txt vue.txt socket.io.txt tailwind.txt dompurify.txt unocss.txt fonts.txt
  native/
    libgomp.txt libgfortran.txt libiomp5md.txt libscipy_openblas64_.txt openssl.txt ...
```

Artifact-specific placement:

| Artifact | Placement |
|---|---|
| Linux `tar.gz` | `StemForge/LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/` at the bundle root, plus `_internal/` copies as PyInstaller data. |
| Linux `AppImage` | Same tree inside the AppDir; `usr/share/doc/stemforge/` symlink or copy for desktop discoverability. No host WebKitGTK/GStreamer files are bundled, so no new notices. |
| Windows portable ZIP | Same tree at the ZIP root next to `StemForge.exe`. |
| Windows Inno installer | `{app}\LICENSES`, `{app}\THIRD_PARTY_NOTICES.md`, `{app}\SOURCE_OFFER.md`; Start Menu shortcuts "Licences" and "Source offer"; `[Files]` includes the tree; the embedded Inno Setup loader is credited in the notices (its licence permits royalty-free distribution). |
| Release page | `StemForge-<ver>-src.tar.gz`, `StemForge-<ver>-third-party-sources.tar.zst`, `SHA256SUMS`. |

The Windows installer may bundle Microsoft's WebView2 Evergreen Bootstrapper. It is an unmodified Microsoft redistributable covered by Microsoft's terms; include its notice next to the installer and do not imply StemForge's GPL covers it.

---

## 10. In-app About / attribution

Ticket 08's `Settings & About` modal already has the rows. Ticket 13 fills them:

| Row / control | Behaviour |
|---|---|
| **Version** | `StemForge <version>` |
| **Licence** | `GPL-3.0-only`; button opens `LICENSE` in a scrollable dialog. |
| **Third-party notices** | Opens a scrollable dialog rendering `THIRD_PARTY_NOTICES.md` (bundled markdown). |
| **Corresponding source** | Opens `SOURCE_OFFER.md`; buttons: "Open release page", "Copy source URL", "Open licences folder". |
| **Source code** (optional row) | Opens the project repository in the default browser. |
| **Licences folder** | Opens the OS file manager at the bundle's `LICENSES/` directory. |
| **Legal** | The YouTube ToS/copyright sentence from ticket 09, plus: "htdemucs weights are downloaded from Hugging Face at runtime and are not covered by StemForge's licence." |
| Model gate **Licence details** | Opens the same modal, scrolled to the weights paragraph. |

Requirements:

- The modal must work offline (no web fetch); all notices are bundled.
- The markdown renderer must escape HTML and must not need network assets.
- The "Licences folder" action must fall back to a dialog path listing if the platform file manager fails.
- The About entry point is one click from the main surface (ticket 08).
- Together, the Licence row, copyright line, no-warranty line, and source link are StemForge's GPLv3 **Appropriate Legal Notices** (§5(d)). No **Installation Information** obligation applies (§6): StemForge is not a User Product and does not lock hardware.

---

## 11. Source offer

**GPLv3 §6(d) same-place offer.** Every release page carries:

1. `StemForge-<version>-src.tar.gz` — the exact tagged source, including `packaging/` (PyInstaller specs, licence generator, build scripts).
2. `StemForge-<version>-third-party-sources.tar.zst` — containing:
   - `ffmpeg/` at commit `d838f9f9ae`;
   - `btbn/` at tag `autobuild-2026-11-15-12-40` (build scripts, variants, patches);
   - `deps/<lib>/` for **every** external library named by the shipped `ffmpeg -buildconf`, at the `SCRIPT_COMMIT` in BtbN's scripts;
   - `rubberband/` at commit `1d95888b`;
   - `libsndfile/` 1.2.2;
   - `lameenc/` at commit `77aba334` (LGPL-3.0-or-later) plus `lame-4.0.tar.gz` (SHA-256 `3df5124d5ad3a98312ffd7ba6a9b36230e4f8a3e66d3ce0f425e336c32d216eb`, the version `lameenc` builds);
   - `pyinstaller/` 6.22.0 sdist (bootloader source);
   - `python-copyleft/` — sdists for every Python package whose shipped licence includes GPL/LGPL/MPL (`mutagen`, `certifi`, `bidict`, `tqdm`, `orjson`, and any others the generator flags; `lameenc` is handled above);
   - `SOURCES.md` — name, repo, commit/tag, licence, SHA-256 for each;
   - `BUILDINFO/` — exact configure/meson/cmake lines, compiler versions, and our build scripts.
3. `SHA256SUMS` covering 1 and 2.

**GPLv3 §6(b) written offer.** `SOURCE_OFFER.md` states that the Corresponding Source is available at the release page and that, for three years from the release date, a machine-readable copy will be provided on request for no more than the cost of physical distribution. Contact: the project repository's issue tracker (and a postal/email contact if counsel requires one).

**Completeness check:** `scripts/licensing/fetch_sources.py` derives the external-library list from the shipped `ffmpeg -buildconf`, maps each `--enable-lib*`/`--enable-*` to its `scripts.d` entry, and fails if any is missing from the source bundle. No hand-written list.

---

## 12. Generation and verification mechanics

Files to create in the implementation effort:

| Path | Purpose |
|---|---|
| `LICENSE` | GPL-3.0-only text. |
| `packaging/licenses/direct.toml` | Curated SPDX id + source URL for every direct dependency. |
| `packaging/licenses/native.toml` | Every non-Python native file expected in the bundle, mapped to a licence text. |
| `packaging/licenses/frontend.toml` | NiceGUI-vendored frontend assets (§6). |
| `packaging/licenses/models.toml` | The weights: `distributed = false`, pin, SHA-256, notice text. |
| `packaging/licenses/texts/` | Canonical SPDX licence texts. |
| `scripts/licensing/generate_notices.py` | Reads `uv.lock`, the build venv's `dist-info`, the curated manifests, and `ffmpeg -buildconf`; writes `THIRD_PARTY_NOTICES.md`, `LICENSES/`, `SOURCE_OFFER.md`. |
| `scripts/licensing/verify_bundle.py` | Asserts every shipped native file has a notice, every direct dependency has an SPDX id, no `*.safetensors`/weights are in the bundle, and the licence tree is complete. |
| `scripts/licensing/fetch_sources.py` | Builds and checks the third-party source bundle (§11). |
| `packaging/ffmpeg/vendor.sh` | Downloads the pinned BtbN assets, verifies SHA-256, unpacks `bin/ffmpeg`, `bin/ffprobe`, licence files, and records `ffmpeg -buildconf`. |
| `packaging/rubberband/build.sh` | Meson build (D4). |
| `packaging/libsndfile/build.sh` | CMake build (D5). |

CI gates:

1. `generate_notices.py --check` — the committed notices match the lock/build.
2. `verify_bundle.py` — run on each assembled bundle before upload.
3. `fetch_sources.py --verify` — the source bundle covers `ffmpeg -buildconf` and every LGPL/GPL component.
4. A frozen-app smoke test that imports `demucs.api` and asserts `torchaudio` is absent.
5. A licence-header check on first-party source (SPDX `GPL-3.0-only`).

---

## 13. Release checklist (licensing)

1. `uv lock --check`; no floating versions; no `--enable-nonfree` binary anywhere.
2. Run the generator; commit `THIRD_PARTY_NOTICES.md`, `LICENSES/`, `SOURCE_OFFER.md`; CI diff must be clean.
3. Verify the BtbN archive SHA-256, unpack only `ffmpeg`/`ffprobe`, record `-buildconf`.
4. Build Rubber Band and libsndfile from the pinned sources; record `--version`/`BUILDINFO`.
5. Build the third-party source bundle; verify every external library from `-buildconf` is present.
6. Assemble each artifact; run `verify_bundle.py`; confirm `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/` are present.
7. Confirm no model weights (`*.safetensors`) are in any artifact or repository release asset.
8. Smoke-test About → Licence / Third-party notices / Corresponding source / Licences folder; model gate → Licence details.
9. Publish release assets + `SHA256SUMS`; verify the source URLs resolve; archive the written offer and release date.
10. Counsel review before public v0.1.0 (weights posture, source bundle completeness, notices).

---

## 14. Open items

- **Counsel review** of the weights-download posture and the source-offer wording. Not a planning blocker; a release gate.
- **Final repository/tag URL** — the notices and `SOURCE_OFFER.md` use a `<REPO_URL>` placeholder until ticket 10 fixes the release naming and repository home.
- **Windows code signing** does not change licensing, but the signed binary must be built from the same tagged source and the source bundle must match. Ticket 10 owns signing.
- **AppImage** is a thin wrapper over the same bundle; if the packaging plan ever bundles host WebKitGTK/GStreamer files, this inventory must be reopened (those are LGPL/BSD and would add notices and relink duties).
- **GCC runtime libraries** are treated as System Libraries (§8). If counsel prefers a stricter reading, add the GCC release source to the source bundle.
- **Model-weight licence change** — if the Demucs author or Meta ever grants a licence, D2 can be revisited; until then, no bundling and no offline pack.
