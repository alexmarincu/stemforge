# 04 — Packaging and CI for StemForge

**Status:** research complete; v1 build strategy proposed
**Date:** 2026-11-14
**Scope:** NiceGUI 4.0 + pywebview 6.2 desktop app, Python 3.12/3.13, PyTorch/Demucs, ffmpeg, yt-dlp, Linux x86_64 + Windows 10/11 x64.
**Non-goals:** application code changes, installer branding, auto-update.

**Companion research (same planning effort):** report 01 covers the shell/backend (MSHTML fallback, GStreamer, AppImage details);[^76] report 02 covers the separation engine (including a compatible 881 MB / 197 MB-zstd PyInstaller measurement);[^75] report 03 covers the media pipeline (FFmpeg 9.0.2 build choice, Deno).[^77] This report defers to 01 on WebKit/GStreamer specifics and to 03/13 on the final FFmpeg build licensing choice.

---

## TL;DR

1. **Use PyInstaller 6.22.x in `--onedir` mode**, built natively on each target OS. One-file is a trap for a torch app: it re-extracts hundreds of MB to `%TEMP%`/`/tmp` on every launch, slows startup, and increases antivirus false positives. Measured on Linux: a NiceGUI + torch 2.15 CPU + Demucs 4.1 one-dir bundle is **875 MB / 3,787 files**, builds in **120 s**, and reaches HTTP 200 in **2.2 s**. The same app without torch is **97 MB / 54 s / 0.9 s**.
2. **Linux release builds on `ubuntu-22.04`** (glibc 2.35). Torch 2.15's CPU wheel is `manylinux_2_28`, NumPy 2.5 is `manylinux_2_28`, and PySide6 6.12 (if chosen) is `manylinux_2_34`; all fit under 2.35. Building on 24.04 sets a glibc 2.39 floor and breaks older distros, which an AppImage cannot fix.[^41][^69][^70]
3. **Linux: tar.gz is the primary artifact; AppImage is an opt-in thin wrapper.** Do not bundle WebKitGTK in v1. WebKitGTK spawns `WebKitWebProcess`/`WebKitNetworkProcess` from a compile-time absolute path; linuxdeploy does not bundle auxiliary executables, and `WEBKIT_EXEC_PATH` was removed in WebKitGTK 2.52. Bundling requires byte-patching `libwebkit2gtk-4.1.so.0` or shipping QtWebEngine instead.[^49][^50] For v1, bundle Python/torch/ffmpeg, require host `libwebkit2gtk-4.1-0` + `gir1.2-webkit2-4.1` + GStreamer `good` plugins for native mode, and fall back to the system browser (or refuse to start natively with a clear message) when they are unavailable.
4. **Windows: portable ZIP + Inno Setup installer, per-user, with WebView2 detection.** Build on `windows-2022` (or `windows-2025` with a compatibility smoke test), never cross-build. Windows 10 is out of support since 2025-10-14, so treat Windows 11 as primary and Windows 10 22H2 + ESU as best-effort.[^61]
5. **Do not bundle Demucs weights in v1.** Their license is explicitly unclear and "likely" CC-NC-BY because of MusDB/Meta training data.[^30] Ship a first-run downloader pinned by revision + SHA-256, and revisit an optional offline model pack only after legal review. This also removes 84 MB (`htdemucs`) to 336 MB (`htdemucs_ft`) from every artifact.[^31][^32]
6. **Publish release assets, not Actions artifacts.** On GitHub Free, total artifact storage is 500 MB; the Linux and Windows artifacts together exceed that. Release assets allow 1,000 files per release, < 2 GiB each, and no total-size limit.[^63][^65] Use `upload-artifact` only for small logs/checksums, and upload installers directly to a draft GitHub Release.
7. **Do not use Nuitka for v1.** It works and has a pywebview plugin, but compiling torch's Python graph is routinely a 30–60+ minute job and its output is not smaller.[^20][^21][^22] Keep it as a v2 option if startup or source protection becomes a priority.
8. **Do not use Briefcase for this stack.** Its own AppImage implementation prints a warning that AppImage is "highly unreliable", "cannot use pre-compiled binary wheels", and has "significant problems" with GTK/PySide.[^24] Torch is a pre-compiled binary-wheel dependency.
9. **cx_Freeze is a credible fallback**, not the default: it has a real torch hook and a Linux AppImage command, but no one-file Windows mode and no pywebview hook, so more manual work for the exact two things this app needs.[^27][^28][^29]

---

## 0. Method and measurement notes

**Assumptions (from the map, 2026-11-14):** StemForge is GPL-3.0; v1 targets Linux x86_64 (glibc 2.35+) and Windows 10/11 x64; CPU-only torch in v1; no macOS/ARM/32-bit; PyInstaller onedir is the intended default unless this research says otherwise.

- Versions were resolved from PyPI/GitHub on 2026-11-14; documentation was read from the projects' current stable docs.
- Local measurements were made on Arch Linux x86_64 (Python 3.12.13, GCC 16.2.1), using PyInstaller 6.22.3, NiceGUI 4.0.0, torch 2.15.0+cpu, Demucs 4.1.0, and `nicegui-pack`:

| Measurement | Result |
|---|---|
| NiceGUI-only, `--onedir` | 97 MB, 54 s build, 0.91 s to HTTP 200 |
| NiceGUI-only, `--onefile` | 40 MB, 1.34 s to HTTP 200 (+0.43 s vs onedir) |
| NiceGUI + torch + Demucs, `--onedir` | **875 MB, 3,787 files, 120 s build, 2.21 s to HTTP 200** |
| `torch/test` + `torch/bin` inside that bundle | **162 MB** (109 MB + 53 MB) |
| NiceGUI + torch + Demucs, `--onefile` | **failed at 875 MB** in our shared 12 GB tmpfs (already ~76% full): `objcopy` hit `No space left on device` and left a truncated 32 MB binary. One-file needs roughly 2× the onedir size in free temporary space. |
| `import torch` outside a bundle | 3.8 s warm, 12.1 s cold |
| torch 2.15 CPU wheel | 209.5 MB compressed → 744 MB installed (Linux) |
| torch 2.15 CPU wheel (Windows) | 126.6 MB compressed → 487 MB installed |

- Everything marked **Est.** is a projection from component sizes or build mechanics, not a measured end-to-end result. The CI estimates assume GitHub's 4-vCPU public runners.
- **Limits:** only PyInstaller was built end-to-end locally. Nuitka, Briefcase, and cx_Freeze figures are from their docs/issues, not from builds of this app. No Windows, macOS, WebKitGTK-window, or WebView2 runtime test was executed on this host; those remain CI/manual-QA items.
- The local build deliberately imported `torch` and `demucs` at module scope. When they were only imported inside a function, PyInstaller silently omitted them and produced a 150 MB bundle. **Every heavyweight dependency must be visible to static analysis or listed in `hiddenimports`.**

---

## 1. Version landscape (2026-11-14)

| Component | Version | Python support | Notes |
|---|---|---|---|
| NiceGUI | **4.0.0** | >=3.11, <4; classifiers 3.11–3.15 | Native mode uses pywebview; `nicegui-pack` wraps PyInstaller.[^1] |
| pywebview | **6.2.1** | >=3.8; classifiers through 3.13 | Windows: pythonnet + WebView2; Linux: GTK3 + WebKit2 4.1 (4.0 fallback) or Qt.[^4][^6] |
| PyTorch | **2.15.0** | >=3.11 | PyPI Linux wheel pulls CUDA 13.2 deps; use the CPU index or `--no-deps`.[^34][^35] |
| torchaudio | 2.11.1 | >=3.10 | Not a Demucs 4.1 inference dependency; skip it. |
| Demucs | **4.1.0** | >=3.10 | Uses `sphn` for audio I/O, `huggingface_hub` + safetensors for weights, `lameenc` for MP3.[^30][^33] |
| yt-dlp | **2026.10.26** | >=3.11 | Full YouTube support now needs `yt-dlp-ejs` + a JS runtime (Deno recommended).[^37][^38] |
| ffmpeg | 9.0.2 builds | n/a | GPL vs LGPL builds; GPL is needed for the rubberband filter, LGPL is lighter if using the standalone Rubber Band CLI.[^67][^68] |
| PyInstaller | **6.22.3** | <3.16, >=3.8 | `pyinstaller-hooks-contrib` 2026.10. |
| Nuitka | **4.3.5** | 3.4–3.14 | AGPLv3 compiler, runtime exception for outputs.[^17] |
| Briefcase | **0.4.7** | >=3.11 | Linux AppImage / system packages; Windows MSI/ZIP.[^23][^24] |
| cx_Freeze | **8.7.4** | <3.16, >=3.10 | Torch hook, Linux AppImage command, no Windows one-file.[^27][^28] |

**Python choice:** 3.13 is the newest version supported by every runtime piece (NiceGUI, pywebview 6.2 classifiers, torch 2.15, Demucs, yt-dlp, `sphn`, `lameenc`); 3.12 is equally safe. 3.14 is supported by torch/NiceGUI but not yet classified by pywebview, so it is not a v1 target.

**A Python 3.10 trap:** `diffq` (needed only for the quantized `mdx_extra_q`/`mdx_q` Demucs models) ships binary wheels only up to cp310.[^36] On 3.12/3.13 it would have to be compiled from source. v1 should use `htdemucs` / `htdemucs_ft` (safetensors, no diffq) and explicitly exclude quantized models.

---

## 2. What the stack demands from a packager

### 2.1 NiceGUI 4.0 native mode

- `ui.run(native=True)` launches a pywebview window in a **separate process** using an always-spawn multiprocessing context.[^2]
- The docs explicitly require `multiprocessing.freeze_support()` as the first statement inside the `if __name__ == '__main__'` guard, with `app.native.*` settings defined **outside** the guard.[^2]
- This is not theoretical. Our first frozen test omitted `freeze_support()` and the executable re-ran itself repeatedly (`multiprocessing.resource_tracker` spawn loop) until killed. Adding the guard produced a clean single server process and HTTP 200.
- Native mode picks an open port automatically (`native.find_open_port()`), which is important for packaged apps because multiple copies share a machine.[^2]
- `NICEGUI_STORAGE_PATH` defaults to `.nicegui` in the working directory. In a packaged app that is often read-only (Program Files) or shared between instances. Set it to a per-user writable path before `ui.run`.[^2]
- NiceGUI uses PEP 562 lazy imports (`nicegui.ui.__getattr__` → `importlib.import_module`).[^3] PyInstaller cannot follow those string references. `nicegui-pack` works around this by adding the **entire `nicegui` package directory, including `.py` files**, as data (`--add-data <site-packages>/nicegui:nicegui`). That is why lazy element imports still resolve at runtime. If you stop using `nicegui-pack`, you must either keep that `--add-data` or add `collect_submodules('nicegui')` yourself.
- PyInstaller ships an official `hook-nicegui.py` in `pyinstaller-hooks-contrib`; it collects package data but **not** submodules.[^13] pywebview ships its own PyInstaller hook entry point (`webview.__pyinstaller`), which collects `webview/lib` on Windows and `webview/js` everywhere.[^14]

### 2.2 pywebview 6.2 backends

| Platform | Backend | Runtime requirement | Packaging consequence |
|---|---|---|---|
| Windows | `edgechromium` via pythonnet | .NET Framework 4.6.2+ and WebView2 Evergreen Runtime | WebView2Loader.dll + managed DLLs are in the wheel; hook collects them. Runtime itself is a host dependency.[^6][^51] |
| Linux | `gtk` (default) | PyGObject, GTK3, WebKit2GTK 4.1 (falls back to 4.0), Soup 3 | `gi`/PyGObject is not a normal pip wheel; needs system packages or a source build. WebKitGTK is a large, multi-process host library.[^6] |
| Linux | `qt` | QtWebEngine via PyQt6/PySide6 | pip-installable Chromium; much larger, but relocatable with linuxdeploy-plugin-qt.[^46] |

Renderer names and defaults are documented in pywebview's web-engine table.[^7]

pywebview's GTK backend does `gi.require_version('Gtk','3.0')`, `gi.require_version('WebKit2','4.1')` and `gi.require_version('Soup','3.0')` at import time (with 4.0/2.4 fallbacks). The PyInstaller `gi` hooks collect GTK typelibs, but there is **no built-in `hook-gi.repository.WebKit2`**, so a custom hook or explicit typelib data entry is needed.[^15]

`pip install nicegui[native]` only pulls `pywebview` (>=5.0.1,<7); it does **not** pull PyGObject. On Linux the build environment must supply GTK/WebKit separately. PyGObject 3.50 itself only requires GLib >= 2.64 and girepository >= 1.64, so it can be built from source on Ubuntu 22.04 (glib 2.72) without pulling in a newer glib.[^72]

### 2.3 PyTorch 2.15

Measured wheel contents (CPU wheels from `download.pytorch.org/whl/cpu`):

| Platform | Wheel | Installed `torch/` | Largest binaries |
|---|---|---|---|
| Linux x86_64 | 209.5 MB | 744 MB | `libtorch_cpu.so` 440 MB, `libtorch_python.so` 33 MB |
| Windows x64 | 126.6 MB | 487 MB | `torch_cpu.dll` 310 MB, `torch_python.dll` 20 MB |

Breakdown (Linux): `include/` 62 MB, `test/` 109 MB, `bin/` 53 MB, `lib/` 461 MB, Python sources ~53 MB. PyInstaller's `hook-torch.py` excludes headers and `.lib` files, collects all submodules and dynamic libraries, and on Windows also collects MKL DLLs when the torch build depends on MKL.[^12] It does **not** exclude `torch/test` or `torch/bin`; our 875 MB bundle contained both (162 MB combined). A spec-level data filter can safely remove them for inference-only apps (verify with a full separation smoke test).

**CPU wheels only.** PyPI's default Linux `torch` 2.15 wheel now declares CUDA 13.2 dependencies (`cuda-toolkit`, `nvidia-cudnn-cu13`, `nvidia-nccl-cu13`, `triton`, …), which will pull gigabytes of NVIDIA packages into the build environment.[^34] Install from `--index-url https://download.pytorch.org/whl/cpu` or from a pinned local CPU wheel, and verify `torch.__version__` ends in `+cpu`. (`uv pip install` from a file whose Python tag does not match will silently fall back to the PyPI CUDA wheel — pin the interpreter and assert the version.)

**Startup:** `import torch` dominates cold start. Measured 3.8 s warm / 12.1 s cold on Linux; expect similar or worse on Windows due to Defender scanning the 310 MB DLL. Consider showing the NiceGUI window first and importing torch/Demucs in a worker thread for the first job, while still bundling them.

### 2.4 Demucs 4.1

- Pretrained models are now downloaded from HuggingFace (`hf_hub_download`) and loaded from `.safetensors`; there is a fallback to the legacy `dl.fbaipublicfiles.com` repo for single signatures.[^33]
- Model sizes (from the HF API):[^31][^32]

| Model | Files | Size | Use |
|---|---|---|---|
| `htdemucs` | 1 safetensors + yaml | **84 MB** | Default, fastest |
| `htdemucs_ft` | 4 safetensors + yaml | **336 MB** | Best quality, ~4× compute |
| `htdemucs_6s` | 1 safetensors + yaml | **55 MB** | 6 sources (incl. piano/guitar) |
| `mdx_extra` | 4 safetensors + yaml | **669 MB** | Legacy MDX |
| `mdx_extra_q` | 4 safetensors + yaml | **167 MB** | Requires `diffq` |

- Inference does **not** need torchaudio: Demucs 4.1 uses `sphn` for decoding/encoding and `lameenc` for MP3. It still shells out to `ffmpeg` for formats `sphn` cannot read and for FLAC writes, so ffmpeg remains a runtime dependency.[^30]
- The weights license is a blocker for redistribution: the Demucs README says the code is MIT, but "the license of the weights is not clear", MusDB contained research-only data, and the 6-source variant "likely means CC-NC-BY license is applicable".[^30]

### 2.5 yt-dlp, ffmpeg, and the new JavaScript runtime

- yt-dlp 2026.10.26 lists **ffmpeg, ffprobe, `yt-dlp-ejs`, and a supported JavaScript runtime/engine** as "highly recommended"; Deno is the recommended runtime, with Node, Bun, and QuickJS supported.[^37]
- `yt-dlp-ejs` itself is tiny (50 KB wheel), but the runtime is not: the Deno PyPI wheel is 42 MB compressed (Linux) / 42 MB (Windows), while QuickJS-ng binaries are ~2.5 MB (Linux) / 1.9 MB (Windows).[^38][^39] The official yt-dlp executable (23.6 MB on Windows) bundles more, but still needs a runtime.
- yt-dlp ships its own PyInstaller hook (`yt_dlp/__pyinstaller/hook-yt_dlp.py`) which collects `yt_dlp_ejs` JS data and several hidden imports, but not all extractor submodules. Add `collect_submodules('yt_dlp')` (11 MB of source) to be safe.
- ffmpeg size depends heavily on build flavor. BtbN's Windows zips: LGPL shared 80 MB compressed, LGPL static 168 MB, GPL shared 90 MB, GPL static 195 MB.[^68] The LGPL shared zip expands to ~160 MB once you keep `ffmpeg.exe`, `ffprobe.exe`, and the codec DLLs (and drop `ffplay`/docs); the Linux static build is ~80 MB per binary, so ffmpeg + ffprobe is ~160 MB. LGPL shared is the right default for distribution; yt-dlp also publishes its own recommended builds.[^74]

**Licensing:** FFmpeg is LGPL-2.1+ by default, but enabling GPL-only components makes the whole FFmpeg binary GPL. The FFmpeg legal page's LGPL checklist requires: build without `--enable-gpl`/`--enable-nonfree`, dynamic-link the libraries, distribute the exact corresponding source, and add attribution in the About box and download page.[^67] StemForge is GPL-3.0, so a **GPL FFmpeg build is license-compatible** — and research 03's pipeline needs the `rubberband` filter, which is GPL-only and absent from LGPL builds.[^68][^77] If v1 uses the separate Rubber Band CLI instead, an LGPL FFmpeg build is the lighter option. Settle the final build in ticket 13; either way, publish the corresponding source and attribution.

---

## 3. Packager comparison

### 3.1 Summary matrix

| Criterion | PyInstaller 6.22 | Nuitka 4.3 | Briefcase 0.4.7 | cx_Freeze 8.7.4 |
|---|---|---|---|---|
| NiceGUI support | Official hook + `nicegui-pack`; docs recommend it[^2][^13] | Docs now document Nuitka flags (`--include-package=nicegui`, `--include-package-data=nicegui`)[^2] | Generic only; no NiceGUI integration | Generic only |
| pywebview support | pywebview ships a PyInstaller hook and docs recommend PyInstaller[^5][^14] | Built-in `PywebViewPlugin`, auto-enabled in standalone; selects GTK/Qt/Windows backends[^18] | Generic only | No hook; manual `webview/lib` + `webview.platforms.*` |
| torch support | `hook-torch` collects submodules, libs, MKL/nvidia handling[^12] | Package config + deprecated torch plugin (no-op); JIT disabled by default in standalone[^19] | No torch handling; relies on installed wheels | `_torch_.py` hook collects libs/bin, excludes headers[^28] |
| Data files | `--add-data`, hooks, spec `datas` | `--include-package-data`, `--include-data-dir`[^16] | `briefcase.toml` resources | `include_files` |
| One-dir | Yes (default) | `--mode=standalone` | Always a folder/AppDir/MSI | Always one-dir on Windows |
| One-file | Yes; extracts to temp[^9] | `--mode=onefile`; extracts[^16] | No | Linux AppImage only; not Windows[^29] |
| Startup | Fastest in onedir; onefile pays extraction | Compiled; still dominated by torch import | Normal Python startup | Normal Python startup |
| Bundle size | 875 MB measured (torch, no ffmpeg/deno/GTK) | Similar or larger; includes `torch/include` + `torch/bin` by default[^19] | Installed wheels + support package; large | ~similar; includes `torch/bin`, excludes headers |
| Build time | 120 s measured (torch, Linux) | 30–60+ min with torch[^20][^21][^22] | Minutes + support-package download | Minutes |
| Cross-build | Native only; Wine "said to be possible", undocumented[^10] | Native only; `--target` is WASI-only/experimental[^16] | Native per platform; AppImage Linux/macOS+Docker[^24] | Native only; `bdist_appimage` Linux-only[^27] |
| License | GPL-2.0 with bootloader exception; commercial apps allowed[^11] | AGPLv3 compiler + runtime exception for outputs[^17] | See project license | See project license |

### 3.2 PyInstaller (recommended)

**Why it wins here:** it has the most mature, *official* hook chain for both NiceGUI and pywebview, plus the most complete torch hook. `nicegui-pack` is a thin wrapper that adds the NiceGUI package directory as data, which is exactly what NiceGUI's lazy imports need.[^3]

**One-dir vs one-file.** PyInstaller's own docs say one-file is "a little slower" because it extracts support files to `_MEI*` on every launch, leaves that folder behind on crashes, and cannot run from a no-exec `/tmp`.[^9] For a 875 MB app that means writing hundreds of MB to `%TEMP%` per launch and asking Defender to scan it. Our small-scale measurement showed +0.43 s for a 97 MB one-file; scaling to ~900 MB implies several seconds plus I/O and AV overhead (**Est. 5–12 s**). The 875 MB one-file build also exhausted a 12 GB tmpfs during `objcopy`, so even the build machine needs roughly 2× the onedir size in free space. Use one-dir.

**Bundle-size levers.** Filter `torch/test` and `torch/bin` out of the collected data (−162 MB measured), exclude `matplotlib` if unused (`MATPLOTLIB=false`), and avoid bundling both GTK and Qt backends. Do **not** exclude torch submodules speculatively; torch imports many modules dynamically.

**Windows specifics.** The pywebview hook collects `webview/lib` (managed WebView2 DLLs and `runtimes/win-x64/native/WebView2Loader.dll`). The torch hook collects `torch/lib/*.dll` (c10, torch_cpu, torch_python, libiomp5md, etc.) and MKL DLLs if applicable. Unlike cx_Freeze's explicit list (`torch._C`, `torch._VF`, `torch.return_types`, `distributions`, `testing`, `unittest`), PyInstaller collects **all** torch submodules and dynamic libraries; that is more robust but explains part of the size difference. Still smoke-test on a clean Windows VM: MSVC runtime (`vcruntime140.dll`, `msvcp140.dll`) and `libomp` are common failure points.

**Cross-build.** PyInstaller's output is specific to the OS and Python that ran it. The docs mention Wine cross-development only as "said to be possible… Further details are needed."[^10] Do not rely on it.

### 3.3 Nuitka (v2 option)

**Strengths:** compiles Python to C, supports standalone/onefile, has a built-in `PyWebViewPlugin` (auto-enabled, picks `webview.platforms.gtk` on Linux and the Windows backends on Windows), and ships a package config with NiceGUI data files and torch DLL/data rules.[^18][^19] It can even create an AppImage (since 4.2) and an NSIS installer (since 4.2) directly.[^16]

**Costs:**

- Build time: Nuitka issues report 30–60 minutes for torch standalone builds, and a January 2026 issue describes compiling numpy/torch as "30+ minutes" (with an unsupported `--nofollow-import-to` workaround to get under 5 minutes).[^20][^21][^22] On a 2-vCPU private runner, expect the upper end.
- Torch JIT: in standalone mode Nuitka **disables Torch JIT by default** (`PYTORCH_JIT=0`) unless you pass `--module-parameter=torch-disable-jit=no`. We found no `torch.jit`/TorchScript use in Demucs 4.1's inference modules, so the default looks acceptable, but this must be a conscious decision and smoke-tested with a real separation.[^19]
- Size: the package config includes `torch/bin` and `torch/include` as data dirs, which is 115 MB of build headers/test binaries on Linux; exclude them explicitly. Nuitka's maintainer has said size is not a goal, and torch apps are often ~1 GB.[^20]
- License: AGPLv3 for the compiler, with a runtime exception that does not affect the compiled program's license.[^17]

**Verdict:** keep as a spike for v2 if startup time or bytecode protection matters. Do not make it the v1 release path.

### 3.4 Briefcase (rejected)

Briefcase packages a Python app with a per-platform support package (python-build-standalone for AppImage/Flatpak; system Python for Linux system packages) and can produce Windows MSI (WiX 5) or ZIP.[^23][^25]

The blocker is its own Linux AppImage implementation, which prints:

> "Briefcase supports AppImage in a best-effort capacity. It has proven to be highly unreliable as a distribution platform. AppImages cannot use pre-compiled binary wheels, and has significant problems with most commonly used GUI toolkits (including GTK and PySide)."[^24]

Torch is a pre-compiled binary-wheel dependency, and this app is a GTK/pywebview app. That is two explicit disqualifiers from the tool's own source. Briefcase also has no NiceGUI/pywebview/torch hooks, so all binary collection would be manual. Its Windows MSI/ZIP path is viable in principle, but there is no reason to use two packagers when PyInstaller covers both.

### 3.5 cx_Freeze (fallback)

cx_Freeze is a long-standing freezer with a documented set of build commands and hooks.[^26]

**Strengths:** a real `_torch_.py` hook that collects `torch/lib` DLLs/`.so` files, `torch/bin`, `torch.distributions`, `torch.testing`, `unittest`, and config source files, while excluding headers/share.[^28] It also has a `bdist_appimage` command that downloads appimagetool + the type-2 runtime and wraps the `build_exe` directory.[^27]

**Limits:**

- No Windows one-file mode. The FAQ says single-file executables are only supported on Linux via `bdist_appimage`; on Windows use `bdist_msi` or a self-extracting archive.[^29]
- No pywebview hook. You must add `webview/lib` (Windows) and the correct `webview.platforms.*` modules manually.
- `bdist_appimage` does not run linuxdeploy or the GTK plugin; it only wraps the frozen directory, so GTK/WebKit bundling is still your problem.
- It includes `torch/bin` (53 MB on Linux) by default.

**Verdict:** a reasonable plan B if PyInstaller's torch hook regresses, but it adds manual pywebview work for no v1 benefit.

---

## 4. Linux distribution

### 4.1 AppImage mechanics

An AppImage is an AppDir (a directory tree with `AppRun`, a `.desktop` file, an icon, and `usr/`) plus a type-2 runtime and a SquashFS payload. `appimagetool` builds it; `linuxdeploy` is the higher-level tool that populates the AppDir and can invoke plugins (`gtk`, `qt`) before calling appimagetool.[^40][^43]

- `appimagetool` itself is distributed as an AppImage. In CI, either run it with `APPIMAGE_EXTRACT_AND_RUN=1` or use the static build; FUSE is often unavailable in containers.[^42]
- `--comp` selects SquashFS compression (zstd/xz/gzip); the default is fine for v1.
- The current type-2 runtime statically links libfuse2, so the `libfuse2` package is no longer required on the target. FUSE itself (kernel support + `fusermount`) is still needed to mount the payload; otherwise use `--appimage-extract-and-run`.[^42][^44]
- Older AppImage docs (2022) still say to install `libfuse2` on Ubuntu ≥22.04 (renamed `libfuse2t64` on 24.04); treat that as a fallback for older runtimes, not a hard requirement for current ones.[^42]

### 4.2 glibc and library baseline

AppImage does not bundle glibc. The official best-practice is: "The binaries contained in the AppImage need to be compiled on a system not newer than the oldest base system that the AppImage is intended to run on", and libstdc++ should be no newer than the oldest supported LTS Ubuntu.[^41]

Concrete floors:

| Component | Wheel tag | Minimum glibc |
|---|---|---|
| torch 2.15 CPU | `manylinux_2_28_x86_64` | 2.28 |
| NumPy 2.5.4 | `manylinux_2_28_x86_64` | 2.28 |
| PySide6 6.12 (Qt option) | `manylinux_2_34_x86_64` | 2.34 |
| Ubuntu 22.04 (jammy) | — | 2.35 |
| Ubuntu 24.04 (noble) | — | 2.39 |

Sources: wheel filenames from PyPI/PyTorch index;[^34][^73] glibc versions from Ubuntu package metadata.[^69][^70] **Build on ubuntu-22.04.** Building on 24.04 raises the floor to 2.39 and excludes 22.04 users, which is exactly the failure mode AppImage cannot repair.

### 4.3 GTK/WebKitGTK bundling (the hard part)

`linuxdeploy-plugin-gtk` bundles GTK3 libraries, GLib schemas, GIRepository typelibs, gdk-pixbuf loaders, immodules, Adwaita themes, and sets GTK path variables via AppRun hooks. It does **not** bundle WebKitGTK.[^45]

WebKitGTK is not a single relocatable library. It spawns `WebKitWebProcess` and `WebKitNetworkProcess` from a directory compiled into `libwebkit2gtk-4.1.so.0` (typically `/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/`). linuxdeploy bundles shared libraries, not auxiliary executables. `WEBKIT_EXEC_PATH` used to override the path, but per the Lutris AppImage fix, "WEBKIT_EXEC_PATH was removed upstream — the path is only overridable by byte-patching the .so."[^49] WebKit's own PR confirms these paths are compile-time defaults with runtime overrides, and the override behavior has been conditional on build flags.[^50]

In practice, bundling WebKitGTK requires:

1. Copying `webkit2gtk-4.1/` helper binaries and the injected bundle into the AppDir.
2. Setting `$ORIGIN` RPATHs on the helpers.
3. Byte-patching the absolute path in `libwebkit2gtk-4.1.so.0` to a shorter path (same byte length).
4. Creating that path as a symlink to the AppDir in `AppRun` (usually under `/tmp`).
5. Testing across WebKitGTK versions; 2.52 changed rendering to Skia and has open NVIDIA/Wayland blank-window reports.[^8][^48]

This is doable (Lutris and others do it) but it is version-specific, security-sensitive, and easy to ship broken. **v1 should not do it.**

### 4.4 Wayland/X11

GTK3 supports both backends; `GDK_BACKEND` selects `wayland`, `x11`, or a comma-separated fallback list (e.g. `wayland,x11`).[^47] WebKitGTK 4.1 runs natively on Wayland; no XWayland is required for a GTK3 app.

Known issues to plan for:

- WebKitGTK 2.52 (Skia renderer) + NVIDIA + Wayland can render a blank web view while GTK chrome works. The pywebview maintainer could not reproduce under Xvfb/software rendering; workarounds include `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1`, `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`, `WEBKIT_DMABUF_RENDERER_DISABLE_GBM=1`, or downgrading WebKitGTK.[^8]
- Provide a `STEMFORGE_BROWSER=1` escape hatch that skips the native window and opens the system browser. That turns a hard failure into a usable app.
- Do not set `GDK_BACKEND=x11` by default; it forces XWayland and hurts fractional scaling. Use it only as a troubleshooting override.

### 4.5 tar.gz fallback

The tar.gz is not just a legacy format; it is the reliable escape hatch when AppImage/FUSE/WebKit fails:

```
StemForge-<version>-linux-x86_64/
  StemForge                 # PyInstaller onedir executable
  _internal/                # PyInstaller payload
  vendor/ffmpeg/ffmpeg
  vendor/ffmpeg/ffprobe
  vendor/deno/deno
  vendor/rubberband/rubberband
  LICENSES/
  README.txt
  run.sh                    # checks GTK/WebKit/GStreamer, launches native or browser
```

Release-level `SHA256SUMS` is published alongside the tar.gz/AppImage (not inside the payload).

`run.sh` should detect `gi`, `Gtk-3.0.typelib`, `WebKit2-4.1.typelib`, and the GStreamer elements used for preview (`wavparse`, `mpg123audiodec`, `autoaudiosink`); on failure print the distro package names and start in browser mode rather than exiting. GStreamer is not optional for audio preview: `gst-plugins-good` is a hard dependency on Debian/Ubuntu but only an optdepend on Arch, so a preflight check is required, not just a README note.

### 4.6 Linux recommendation

**v1:** PyInstaller onedir → tar.gz always; AppImage only as an opt-in thin wrapper over the same bundle.

- Build on `ubuntu-22.04`.
- Bundle Python, torch, Demucs, ffmpeg, Deno, and PyGObject/typelibs.
- **Do not bundle GTK, WebKitGTK, or GStreamer.** Declare `libgtk-3-0`, `libwebkit2gtk-4.1-0`, `gir1.2-webkit2-4.1`, and `gstreamer1.0-plugins-good` as host requirements for native mode. Make the AppRun/launcher check them and **never export `GST_PLUGIN_SYSTEM_PATH*`** — an empty bundled plugin path disables GStreamer's default search and breaks audio.
- If `gi`/WebKit/GStreamer is missing or the window fails to create, fall back to `ui.run(native=False, show=True)` and print the install command.
- Do **not** make QtWebEngine the v1 plan: report 01 classifies it as a fallback because of extra dependencies, `--no-sandbox` hacks on some distros, and unverified codec build flags.[^76] A time-boxed spike is still worthwhile for v2, since QtWebEngine is the most self-contained native-window path on Linux: it is designed to ship its `QtWebEngineProcess` and resources alongside the Qt libraries, and linuxdeploy-plugin-qt knows how to bundle them.[^46] Cost: ~+250–400 MB and a second backend to test.

---

## 5. Windows distribution

### 5.1 Portable ZIP vs installer

Ship **both**:

- **Portable ZIP** for users who want no install, USB use, or locked-down machines. It cannot register uninstallers, file associations, or shortcuts, and SmartScreen may mark the downloaded ZIP.
- **Inno Setup installer** for the normal path: Start Menu shortcut, uninstaller, per-user install without UAC, WebView2 bootstrap, and a signed `Setup.exe`.

Both should contain the same PyInstaller onedir payload; the installer is effectively a compressed copy plus metadata.

### 5.2 WebView2 runtime

Microsoft's guidance:[^51]

- Windows 11 includes the Evergreen WebView2 Runtime; most Windows 10 devices have it, but some do not.
- Detect it by reading the `pv` registry value under
  `HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}`
  or `HKCU\Software\Microsoft\EdgeUpdate\Clients\{...}`, or by calling `GetAvailableCoreWebView2BrowserVersionString`.
- If missing, run the ~2 MB Evergreen Bootstrapper with `/silent /install` (online), or the larger Standalone Installer for offline deployment.
- Do **not** use the Fixed Version runtime by default: it is >250 MB and adds update/servicing work.

Implementation: put `MicrosoftEdgeWebview2Setup.exe` next to the app (or embed it in the installer), check the registry before launch, run the bootstrapper if needed, then start the app. For the portable ZIP, a tiny launcher/`StemForge.exe` startup check is enough; a missing runtime should produce a dialog with the Microsoft download link, not a silent crash.

**Do not rely on pywebview to fail loudly.** On Windows, pywebview's renderer order is `edgechromium` then `mshtml`; if WebView2 or .NET is missing it can silently load MSHTML/IE11, and NiceGUI then fails with a confusing "Vue requires Chrome 89+" blank window.[^6][^7][^76] Assert the backend at startup (or call `webview.start(gui='edgechromium')` in a way that fails hard) and show an actionable error instead of a broken window.

Also ship `WebView2Loader.dll` for x64. pywebview's wheel places it under `webview/lib/runtimes/win-x64/native/`; the PyInstaller hook collects `webview/lib`, and the NiceGUI docs note a known workaround of moving the DLL up one directory if it is not found.[^2]

### 5.3 Inno Setup configuration

Key directives (Inno 6.x):[^52][^53][^54][^55][^56]

```ini
[Setup]
AppId={{7E2F6C2A-...}}
AppName=StemForge
AppVersion={#Version}
DefaultDirName={localappdata}\Programs\StemForge
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\stemforge.ico
UninstallDisplayIcon={app}\StemForge.exe
OutputBaseFilename=StemForge-{#Version}-win64
SignTool=artifactSigning
DiskSpanning=yes
DiskSliceSize=max
```

- `PrivilegesRequired=lowest` gives a per-user install with no UAC; `DefaultDirName={localappdata}\Programs\StemForge` keeps it writable. Use `admin` + `{autopf}` only if you need a machine-wide install.
- `ArchitecturesAllowed=x64compatible` prevents the installer from running on 32-bit Windows; `ArchitecturesInstallIn64BitMode=x64compatible` enables 64-bit install mode.
- LZMA2/max is the default and needs ~98 MB of compression memory; decompression needs ~8 MB. For a ~700 MB payload, measure compile time; `lzma2/normal` or `LZMANumBlockThreads=4` may be a better trade.
- Inno's `SignTool` docs warn that with a large amount of data and an external signature, Windows may take a long time verifying the signature. They recommend `DiskSpanning=yes` with `DiskSliceSize=max` for large installs; the setup remains a single logical install even if it emits `.bin` slices.[^55][^56]
- Add `[Run]` to optionally launch StemForge after install, and `CloseApplications=yes` for upgrades.

### 5.4 Code signing and SmartScreen

SmartScreen decides based on file/certificate reputation: a signed file with no reputation can still warn, and an unsigned file almost certainly will. Signing removes "Unknown publisher" and lets reputation accumulate; it does not guarantee a warning-free first download.[^57]

Options as of 2026:

1. **Azure Artifact Signing (formerly Trusted Signing)** — Microsoft-managed certificates in FIPS 140-3 Level 3 HSMs, short-lived (72 h) certificates, timestamping via `http://timestamp.acs.microsoft.com`. Integrations include SignTool, GitHub Actions (`azure/artifact-signing-action`), Azure DevOps, and PowerShell.[^58][^60]
   - Public Trust eligibility is geographically restricted: organizations in the US, Canada, EU, UK, Australia, NZ, Japan, South Korea, Singapore, Switzerland, Norway, Israel; individual developers in the US or Canada. Identity validation takes 1–20 business days.[^59]
   - SignTool invocation uses the Artifact Signing dlib and a metadata JSON:
     `signtool sign /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib ...\Azure.CodeSigning.Dlib.dll /dmdf metadata.json <file>`.[^60]
2. **Traditional OV/EV certificate** from a CA, with a hardware token/HSM and `signtool`. EV certificates are often reported to accumulate SmartScreen reputation faster, but Microsoft does not guarantee that; budget for warnings either way.
3. **Unsigned v1** with published SHA-256 checksums and clear install instructions. Accept the SmartScreen warning as a known cost, and revisit once there is an organization/identity that can pass Artifact Signing validation.

Signing order: sign `StemForge.exe` and any first-party DLLs before packaging; sign the Inno `Setup.exe` and uninstaller via `SignTool`; verify with `signtool verify /pa /all`. Do not sign third-party `ffmpeg.exe` or `deno.exe` with your certificate — ship their own licenses and checksums instead.

### 5.5 Windows 10 end-of-support

Windows 10 Home/Pro reached end of support on **2025-10-14**; 22H2 was the final version. Extended Security Updates are the only Microsoft-supported way to keep receiving security fixes.[^61] As of November 2026:

- Treat Windows 11 x64 as the primary target.
- Keep Windows 10 22H2 + ESU as best-effort; test WebView2 detection there because a small number of Windows 10 devices still lack the runtime.[^51]
- State the support policy in the README/release notes rather than silently claiming Windows 10 support.

### 5.6 Windows recommendation

- Build on `windows-2022` for release (broad compatibility, stable image) and add `windows-2025` as a smoke-test job. `windows-latest` currently points to Windows Server 2025.[^62]
- Ship `StemForge-<ver>-win64-setup.exe` (Inno, per-user, LZMA2) and `StemForge-<ver>-win64-portable.zip`.
- Detect/install WebView2 before first window; bundle the ~2 MB bootstrapper.
- Sign if an Artifact Signing identity is available; otherwise publish checksums and document the SmartScreen warning.

---

## 6. Model weights: bundle or download?

### 6.1 The license problem

This is the decisive constraint. Demucs' README states:

> "Demucs code is released under the MIT license… The license of the weights is not clear: MusDB contains some data that were only usable for research purpose. I sadly cannot help granting a license… The 6 source variant of htdemucs was trained only on Meta internal dataset, which likely means CC-NC-BY license is applicable, as it was the case for all models released by FAIR."[^30]

Redistributing the weights inside StemForge release artifacts would make StemForge a redistributor of questionably licensed, possibly non-commercial-only weights. That is a legal risk disproportionate to the 84 MB saved.

### 6.2 Technical options

| Option | Pros | Cons |
|---|---|---|
| **A. First-run download** (recommended) | No redistribution; smallest artifacts; user gets latest weights | Requires internet on first run; HF availability/rate limits; needs progress/error UX |
| **B. Bundle `htdemucs` in the installer** | Works offline immediately | +84 MB per artifact; license risk; must regenerate installers for weight updates |
| **C. Separate "offline model pack" release asset** | Core installer stays small; users can opt in | Still redistribution if hosted by us; license risk; extra download step |
| **D. Pre-seed HF cache inside the bundle** | Reuses Demucs' loader | HF cache uses symlinks/metadata; Windows installers and ZIPs do not preserve symlinks reliably; brittle |
| **E. Legacy `.th` + `LocalRepo`** | Demucs' `LocalRepo` scans `.th` files directly[^33] | Duplicates the older format; still redistribution; `.th` loading uses `torch.load` |
| **F. App-level safetensors loader** | No HF cache semantics; ship `{yaml, safetensors}` plainly | Small adapter around `load_safetensors_model` + `BagOfModels`; still redistribution if shipped |

### 6.3 Recommended approach

**v1: Option A, first-run download, with a clean cache and a pinned manifest.**

- On first launch, show a modal: model name, size (84 MB for `htdemucs`), source (HuggingFace `adefossez/HTDemucs`), license notice ("weights license unclear; likely non-commercial; see Demucs README"), and a Download button. Do not download silently.
- Cache under `%LOCALAPPDATA%\StemForge\models` / `$XDG_DATA_HOME/stemforge/models`; set `HF_HOME` **before** importing `huggingface_hub`/`demucs`, because `hf_hub_download` reads it at import/call time.
- Set `HF_HUB_DISABLE_SYMLINKS_WARNING=1` and prefer copies on Windows. If the cache directory is on a different volume, HF falls back to copying anyway.
- Pin `repo_id`, filename, revision, size, and SHA-256 in `packaging/models/models.lock.json`; verify after download and on every load (cheap for 84 MB).
- Fall back to the legacy `dl.fbaipublicfiles.com` URL if HF fails; Demucs already implements this fallback for single signatures.[^33]
- Offer `htdemucs_ft` (336 MB) as an optional "HQ" download, not the default: it is 4× the compute and 4× the download.
- Exclude `mdx_extra_q`/`mdx_q` from v1: they require `diffq`, which has no wheels past cp310.[^36]
- If offline use is a hard requirement, publish a separate model pack **only after legal review**; it must be a separate release asset so the core installer remains redistributable.

This also keeps CI simple: the release build does not download weights at all (except an optional smoke test), and the Linux onedir stays ~1.0 GB instead of ~1.09 GB.

---

## 7. GitHub Actions CI

### 7.1 Runner matrix and constraints

Current GitHub-hosted runner facts (2026-11):[^62]

| Label | OS | vCPU / RAM / SSD (public) | vCPU / RAM (private) |
|---|---|---|---|
| `ubuntu-22.04` | Ubuntu 22.04 | 4 / 16 GB / 14 GB | 2 / 8 GB |
| `ubuntu-24.04`, `ubuntu-latest` | Ubuntu 24.04 | 4 / 16 GB / 14 GB | 2 / 8 GB |
| `windows-2022` | Windows Server 2022 | 4 / 16 GB / 14 GB | 2 / 8 GB |
| `windows-2025`, `windows-latest` | Windows Server 2025 | 4 / 16 GB / 14 GB | 2 / 8 GB |

- **Windows bundles can only be produced on Windows runners.** PyInstaller output is OS/Python-specific and its Wine path is undocumented;[^10] Nuitka's only cross-compilation target is WASI/experimental;[^16] Briefcase's Windows/MSI tooling is Windows-only;[^25] cx_Freeze freezes native Windows binaries and its `bdist_msi` is a Windows command.[^26] Plan for a Windows job.
- Linux AppImages must be built on Linux. Briefcase can build them on macOS via Docker, but PyInstaller cannot; native Linux is the supported path.
- Pin `ubuntu-22.04` for the glibc baseline. If that runner is retired, move the build into an `ubuntu:22.04` container on `ubuntu-24.04` rather than building on 24.04 directly; do not let the runner image silently raise the glibc floor.
- Job timeout is 6 hours; matrix limit is 256 jobs per workflow.[^63] PyInstaller fits comfortably; Nuitka with torch (30–60+ min) still fits, but is the job to watch on a 2-vCPU private runner.
- The runner's 14 GB SSD is a real constraint once you add the torch install, pip + HF caches, the onedir tree, and the AppImage/installer output. Clean between stages.

### 7.2 Artifact vs release-asset limits

| Resource | Limit | Implication |
|---|---|---|
| Actions artifact storage (Free) | **500 MB total** | A ~300 MB AppImage plus a ~350 MB Windows installer already exceed it. |
| Actions artifact storage (Pro/Team/Enterprise) | 1 GB / 2 GB / 50 GB | Still tight for 1 GB+ bundles. |
| Artifacts per job / retention | 500 artifacts/job; 90-day default retention | Not the binding limit here. |
| Upload-artifact permissions | Files become 755/644; executable bit lost in zipped artifacts | AppImage must be uploaded as a tar or with `archive: false`. |
| Release assets | 1,000 files/release, **< 2 GiB each**, no total limit | Use release assets for installers/AppImages. |
| Actions cache | 10 GB/repo default, 7-day unused eviction | Enough for pip + one model + vendor binaries; do not cache build outputs. |
| Release asset download bandwidth | No limit | Fine for a public project. |

Sources: Actions limits/storage table,[^63] cache eviction policy,[^64] release quotas,[^65] upload-artifact behavior.[^66]

**Recommended:** create a **draft GitHub Release** first, upload installers/AppImages directly from each build job with `gh release upload`, then publish the draft after both platforms and signatures are verified. Do not route large binaries through `actions/upload-artifact`. Use artifacts only for `SHA256SUMS`, SBOM, logs, and test reports.

### 7.3 Caching

| Cache | Path | Key | Size |
|---|---|---|---|
| pip (Linux) | `~/.cache/pip` | `${{ runner.os }}-pip-${{ hashFiles('packaging/requirements/*.lock') }}` | ~300–500 MB with torch wheel |
| pip (Windows) | `~\AppData\Local\pip\Cache` | same | ~300–500 MB |
| torch wheel | covered by pip cache | — | 209 MB Linux / 127 MB Windows |
| HF models (smoke only) | `~/.cache/huggingface` / `%USERPROFILE%\.cache\huggingface` | `demucs-${{ hashFiles('packaging/models/models.lock.json') }}` | 84 MB (htdemucs) |
| vendor binaries | `packaging/vendor` | `ffmpeg-<ver>-<sha>` + `deno-<ver>` | ~120–200 MB |
| Nuitka (if used) | `~/.cache/Nuitka` / `%LOCALAPPDATA%\Nuitka` | source hash | large; only worth it for repeated builds |

`actions/setup-python` with `cache: pip` plus `cache-dependency-path` is the lowest-friction option. Do not cache the PyInstaller `build/` directory; the analysis cache is not reliable across dependency changes.

### 7.4 Build-time budget

| Stage | Linux (PyInstaller) | Windows (PyInstaller) |
|---|---|---|
| Checkout + Python setup | 1 min | 1–2 min |
| apt/vendor downloads | 1–2 min | 1–2 min |
| `pip install` cold / cached | 3–5 min / 1–2 min | 3–5 min / 1–2 min |
| PyInstaller build | 2–4 min (measured 2 min locally on 4+ cores) | 4–8 min (Defender, slower I/O) |
| Trim + smoke test | 2–3 min | 2–4 min |
| AppImage / Inno package | 2–5 min | 5–15 min (LZMA2) |
| Sign + checksums + upload | 1–2 min | 1–3 min |
| **Total** | **~12–20 min** | **~20–35 min** |

Nuitka: add 30–60+ minutes for the compile, plus packaging. A Nuitka matrix may still fit in the 6-hour job limit, but it is a poor fit for per-PR CI; run it nightly or on tags only.

### 7.5 Workflow outline

```yaml
name: release
on:
  push:
    tags: ['v*']
  workflow_dispatch:

permissions:
  contents: write
  id-token: write        # only if using Artifact Signing OIDC

jobs:
  prepare:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
      - run: gh release create "$GITHUB_REF_NAME" --draft --title "$GITHUB_REF_NAME" || true
        env: { GH_TOKEN: '${{ github.token }}' }

  linux:
    needs: prepare
    runs-on: ubuntu-22.04        # glibc 2.35 baseline
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
          cache: pip
          cache-dependency-path: packaging/requirements/*.lock
      - name: System deps
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends \
            libgtk-3-0 libgtk-3-dev libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1 \
            libgirepository1.0-dev libcairo2-dev pkg-config patchelf \
            gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
            xvfb libfuse2 fuse desktop-file-utils librsvg2-dev
      - name: Build
        run: bash packaging/linux/build.sh
      - name: Smoke test (headless)
        run: xvfb-run -a bash packaging/linux/smoke-test.sh
      - name: Package
        run: bash packaging/linux/package.sh   # tar.gz + AppImage + SHA256SUMS
      - name: Upload release assets
        run: gh release upload "$GITHUB_REF_NAME" dist/release/* --clobber
        env: { GH_TOKEN: '${{ github.token }}' }

  windows:
    needs: prepare
    runs-on: windows-2022
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
          cache: pip
          cache-dependency-path: packaging/requirements/*.lock
      - name: Build
        run: packaging/windows/build.ps1
      - name: Smoke test
        run: packaging/windows/smoke-test.ps1
      - name: Package + sign
        run: packaging/windows/package.ps1
        env:
          AZURE_TENANT_ID: ${{ secrets.AZURE_TENANT_ID }}
          AZURE_CLIENT_ID: ${{ secrets.AZURE_CLIENT_ID }}
      - name: Upload release assets
        run: gh release upload $env:GITHUB_REF_NAME dist/release/* --clobber
        env: { GH_TOKEN: '${{ github.token }}' }

  publish:
    needs: [linux, windows]
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v6
      - run: bash packaging/verify-release.sh   # checksums, asset set, signatures
      - run: gh release edit "$GITHUB_REF_NAME" --draft=false
        env: { GH_TOKEN: '${{ github.token }}' }
```

Add a separate `ci.yml` for PRs that builds one platform, runs unit tests, and does a **quick** PyInstaller smoke (no packaging, no weights) — keep PR feedback under ~15 minutes. For the release matrix, use `ubuntu-22.04` and `windows-2022` as the release builders, and add `ubuntu-24.04` and `windows-2025` as smoke-only jobs so the next runner migration is caught early. Cache the HF weights only for the smoke job that loads a model; the release build itself does not need them.

---

## 8. Recommended v1 build strategy

### 8.1 Directory layout

```
packaging/
  requirements/
    app.lock                 # pinned runtime deps incl. torch CPU wheel + hashes
    build.lock               # pyinstaller==6.22.3, pyinstaller-hooks-contrib==2026.10
  pyinstaller/
    stemforge_linux.spec
    stemforge_windows.spec
    hooks/
      hook-gi.repository.WebKit2.py   # typelib collection for pywebview GTK
      hook-stemforge_models.py        # only if weights are ever bundled
  linux/
    build.sh                 # venv, install, PyInstaller
    package.sh               # AppDir + linuxdeploy/appimagetool + tar.gz
    smoke-test.sh            # xvfb + curl + model-load check
    AppRun
    stemforge.desktop
    stemforge.svg
  windows/
    build.ps1
    package.ps1              # Inno Setup + ZIP + signing
    smoke-test.ps1
    stemforge.iss
    webview2-check.ps1
  models/
    models.lock.json
    fetch_models.py
  licenses/
    THIRD_PARTY_NOTICES.md
    ffmpeg-LGPL.txt
.github/workflows/
  ci.yml
  release.yml
```

### 8.2 PyInstaller spec essentials

```python
# packaging/pyinstaller/stemforge_linux.spec (sketch)
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports = (
    collect_submodules('nicegui') +      # lazy ui elements
    collect_submodules('demucs') +
    collect_submodules('yt_dlp') +       # extractors are lazy
    ['webview.platforms.gtk', 'gi.repository.WebKit2']
)

datas = [
    ('assets', 'assets'),
    ('packaging/licenses', 'LICENSES'),
]
datas += collect_data_files('yt_dlp_ejs', includes=['**/*.js'])
# NiceGUI data + .py files: reproduce nicegui-pack's default --add-data entry here; the CLI cannot take a custom spec.

binaries = [
    ('vendor/ffmpeg/ffmpeg', 'vendor/ffmpeg'),
    ('vendor/ffmpeg/ffprobe', 'vendor/ffmpeg'),
    ('vendor/deno/deno', 'vendor/deno'),
    ('vendor/rubberband/rubberband', 'vendor/rubberband'),
]

a = Analysis(
    ['src/stemforge/__main__.py'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['packaging/pyinstaller/hooks'],
    # ... excludes, runtime_hooks, noarchive, etc.
)
```

Then, after `Analysis`, filter torch test/bin data:

```python
def _is_torch_bulk(dest: str) -> bool:
    p = dest.replace('\\', '/')
    return p.startswith(('torch/test/', 'torch/bin/'))

a.datas = [d for d in a.datas if not _is_torch_bulk(d[0])]
a.binaries = [b for b in a.binaries if not _is_torch_bulk(b[0])]
```

Verify with a real separation job, not just an import: `torch.test` is not needed for inference, but a future torch feature could import it.

Windows spec differences: `console=False`, `.ico`, `webview.platforms.edgechromium`/`winforms`, WebView2Loader via the pywebview hook, and no `webview.platforms.gtk`.

### 8.3 Entry point

```python
from multiprocessing import freeze_support
from nicegui import app, native, ui

# Must be outside the main guard so the native subprocess sees it.
app.native.window_args['resizable'] = True

@ui.page('/')
def index():
    ...

if __name__ == '__main__':
    freeze_support()          # first statement in the guard
    ui.run(
        native=True,
        reload=False,
        port=native.find_open_port(),
        title='StemForge',
        favicon='assets/stemforge.ico',   # Windows native window icon
        show=False,
    )
```

Set `NICEGUI_STORAGE_PATH` and `HF_HOME` before importing NiceGUI/Demucs (runtime hook is the clean place).

### 8.4 Linux scripts

`build.sh`:

1. Create a venv with the pinned Python.
2. Install `packaging/requirements/app.lock` from the PyTorch CPU index.
3. Assert `torch.__version__.endswith('+cpu')`.
4. Install `build.lock`.
5. Run `python -m PyInstaller packaging/pyinstaller/stemforge_linux.spec --noconfirm --clean`.
6. Copy `vendor/ffmpeg`, `vendor/deno`, `LICENSES`.

`package.sh`:

1. `mkdir -p AppDir/usr/bin AppDir/usr/lib/stemforge AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/scalable/apps`
2. Copy the onedir tree to `AppDir/usr/lib/stemforge/`.
3. Write a small `AppDir/usr/bin/stemforge` launcher that execs `../lib/stemforge/StemForge`.
4. Copy `.desktop` + icon.
5. v1 (no GTK bundling): `ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 appimagetool AppDir StemForge-<ver>-linux-x86_64.AppImage`. PyInstaller already bundled the Python/torch libraries, so linuxdeploy adds little; use `linuxdeploy --plugin gtk` only if you decide to bundle GTK later.
6. `tar -C dist -czf StemForge-<ver>-linux-x86_64.tar.gz StemForge/`.
7. `sha256sum` everything into `SHA256SUMS`.

`smoke-test.sh`:

- `xvfb-run -a dist/StemForge/StemForge --smoke-test` (add a hidden flag that starts the server, waits for `/`, optionally loads `htdemucs`, prints OK, exits non-zero on failure).
- Then `xvfb-run -a ./StemForge-*.AppImage --appimage-extract-and-run --smoke-test` to prove the packaged AppImage actually runs.
- Keep the model load optional in PR CI; run it on tags/nightly with the HF cache.

### 8.5 Windows scripts

`build.ps1`:

1. `py -3.13 -m venv .venv`
2. Install pinned requirements from the CPU index; assert `+cpu`.
3. `python -m PyInstaller packaging\pyinstaller\stemforge_windows.spec --noconfirm --clean`.
4. Copy `vendor\ffmpeg\ffmpeg.exe`, `ffprobe.exe`, `vendor\deno\deno.exe`, `LICENSES`.

`package.ps1`:

1. Sign `dist\StemForge\StemForge.exe` (and any first-party DLLs).
2. `Compress-Archive` the onedir + vendor + LICENSES + `README.txt` into `StemForge-<ver>-win64-portable.zip`.
3. Run Inno Setup (`ISCC.exe packaging\windows\stemforge.iss`); Inno's `SignTool` signs setup + uninstaller.
4. `Get-FileHash -Algorithm SHA256` over the ZIP and setup into a release-level `SHA256SUMS`.

`smoke-test.ps1`:

- Run `StemForge.exe --smoke-test` (server only, no window) and `Invoke-WebRequest http://127.0.0.1:<port>/`.
- Assert `torch.__version__` and `demucs` import inside the frozen app via a `--self-test` flag.
- A full native-window test needs an interactive desktop; GitHub's Windows runners are Server SKUs without one. Plan a self-hosted Windows 11 VM (or manual test) for WebView2/window validation.

---

## 9. Size and time budget

### 9.1 Linux (x86_64)

| Component | Size | Basis |
|---|---|---|
| Python + stdlib + NiceGUI + core deps (no torch) | 97 MB | measured |
| torch 2.15 CPU as collected (incl. `test/` + `bin/`) | 679 MB | measured |
| less `torch/test` + `torch/bin` | −162 MB | measured, removable |
| Demucs 4.1 + sphn + numpy + HF stack (residual) | ~99 MB | measured bundle delta |
| ffmpeg + ffprobe (static, build choice TBD) | ~160–200 MB | measured 79.8 MB + 79.7 MB for a minimal static build; GPL/rubberband builds are larger |
| Deno | ~96 MB | measured: 42 MB zip → 96 MB binary |
| Rubber Band CLI + yt-dlp/EJS | ~5 MB | Est. (research 03) |
| PyGObject + typelibs | ~5 MB | Est. |
| GTK3 + WebKitGTK (if bundled) | ~195 MB | Arch package metadata 141 MiB + 53 MiB[^71] |
| **Onedir, v1 (no GTK/WebKit, no weights)** | **~1.0 GB** | Est. |
| **AppImage, v1** | **~280–350 MB** | Est., anchored on research 02's 881 MB → 197 MB zstd measurement + ffmpeg/deno[^75] |
| **tar.gz, v1** | **~350–450 MB** | Est. (gzip is weaker than zstd) |

### 9.2 Windows (x64)

| Component | Size | Basis |
|---|---|---|
| Python + NiceGUI + deps | ~90 MB | Est. |
| torch 2.15 CPU payload | ~390 MB | measured 487 MB install minus headers/.lib |
| numpy + Demucs + sphn + HF stack | ~70 MB | Est. |
| ffmpeg + ffprobe | ~160–200 MB | BtbN LGPL shared zip 80 MB compressed → ~160 MB with codec DLLs; research 03's gyan essentials build is smaller[^68] |
| Deno | ~96 MB | measured: 42 MB zip → 96 MB binary |
| Rubber Band CLI + yt-dlp/EJS | ~5 MB | Est. (research 03) |
| **Onedir, v1** | **~830 MB** | Est. |
| **Portable ZIP** | **~350–420 MB** | Est. (deflate, weaker than zstd) |
| **Inno installer (LZMA2)** | **~300–350 MB** | Est. |

Weights add 84 MB (`htdemucs`) or 336 MB (`htdemucs_ft`) if ever bundled. Since v1 downloads them, they are not in these numbers.

### 9.3 Time

| Operation | Time | Basis |
|---|---|---|
| PyInstaller build, no torch | 54 s | measured |
| PyInstaller build, torch + Demucs | 120 s | measured (fast local CPU) |
| PyInstaller build on 4-vCPU CI | 3–6 min | Est. |
| AppImage packaging | 2–5 min | Est. |
| Inno LZMA2 packaging | 5–15 min | Est. |
| Cold start, onedir, torch eager | 2.2 s to HTTP 200 | measured (warm disk) |
| Cold start, onefile, torch | +5–12 s | Est. from 97 MB measurement |
| `import torch` | 3.8 s warm / 12.1 s cold | measured |

---

## 10. Open questions

1. **Demucs weights license (blocking for bundling).** The README says the weights' license is unclear and likely CC-NC-BY. Legal review is needed before any redistribution. If non-commercial-only, a commercial StemForge cannot ship them at all, even as a download it hosts. This is the single biggest unknown.
2. **Linux native backend.** GTK + host WebKitGTK vs QtWebEngine vs browser-only. Needs a spike on Ubuntu 22.04/24.04, Fedora, Arch, Wayland + NVIDIA, and a decision on whether +250–400 MB for QtWebEngine is acceptable.
3. **WebKitGTK version floor.** If we require host `libwebkit2gtk-4.1`, what is the minimum version that renders reliably? 2.44 (Cairo) works in the reported NVIDIA case; 2.52 (Skia) does not.[^8] Document a floor and a fallback.
4. **`diffq` / quantized models.** If `mdx_extra_q` is required, either pin Python 3.10 or build `diffq` from source for 3.12/3.13. Current PyPI wheels stop at cp310.[^36]
5. **Deno vs QuickJS.** Deno is recommended and 42 MB compressed; QuickJS is ~2.5 MB but less tested. Does yt-dlp's EJS work fully under QuickJS for the sites we care about?
6. **Windows native-window CI.** GitHub's Windows runners are Server 2022/2025 without an interactive desktop, and WebView2 runtime presence is not guaranteed. We need a self-hosted Windows 11 VM or a documented manual test for the actual pywebview window.
7. **Code signing identity.** Artifact Signing Public Trust requires an eligible organization/individual and 1–20 business days of identity validation.[^59] Is there an entity that qualifies, or does v1 ship unsigned?
8. **FFmpeg build/license.** LGPL shared vs static, and whether we can satisfy the source-offer/attribution checklist in the installer and About box.[^67]
9. **AppImage host dependencies.** If we require host GTK/WebKit, the AppImage is not fully self-contained. Is that acceptable, or do we need the QtWebEngine path for a true "download and run" promise?
10. **Torch trimming.** Removing `torch/test`/`torch/bin` saves 162 MB, but must be validated with a full separation run, including any code path that imports `torch.testing` or `torch.utils`.
11. **Windows 10 support policy.** With Windows 10 out of support since 2025-10-14, do we officially support 22H2 + ESU, or Windows 11 only?[^61]
12. **Auto-update.** Not in scope for v1, but AppImage/zsync and Inno/WinSparkle choices affect the release layout. Leave room for `SHA256SUMS` + a version manifest.

---

## 11. Sources

[^1]: NiceGUI 4.0.0, PyPI. https://pypi.org/project/nicegui/4.0.0/ (accessed 2026-11-14)
[^2]: NiceGUI, "Configuration & Deployment" (native mode, freeze_support, nicegui-pack, Nuitka). https://nicegui.io/documentation/section_configuration_deployment (accessed 2026-11-14)
[^3]: NiceGUI, `nicegui/scripts/pack.py` and `nicegui/ui.py` (lazy imports, `--add-data`). Inspected from the 4.0.0 wheel; source: https://github.com/zauberzeug/nicegui/blob/main/nicegui/scripts/pack.py
[^4]: pywebview 6.2.1, PyPI. https://pypi.org/project/pywebview/6.2.1/
[^5]: pywebview, "Freezing". https://pywebview.flowrl.com/guide/freezing
[^6]: pywebview, "Installation" (GTK/WebKit2 4.1, pythonnet, WebView2). https://pywebview.flowrl.com/guide/installation
[^7]: pywebview, "Web engine". https://pywebview.flowrl.com/guide/web_engine
[^8]: pywebview issue #1875, "Blank/white window on Linux with WebKitGTK 2.52 (Skia renderer) + NVIDIA", 2026-10-27. https://github.com/r0x0r/pywebview/issues/1875
[^9]: PyInstaller, "What PyInstaller Does and How It Does It" (one-folder vs one-file, `_MEI` extraction). https://pyinstaller.org/en/stable/operating-mode.html
[^10]: PyInstaller, "Using PyInstaller" (output is OS/Python-specific; Wine cross-development "said to be possible"). https://pyinstaller.org/en/stable/usage.html
[^11]: PyInstaller, "License" (GPL-2.0 with bootloader exception). https://pyinstaller.org/en/stable/license.html
[^12]: PyInstaller hooks-contrib, `hook-torch.py`. https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/src/_pyinstaller_hooks_contrib/stdhooks/hook-torch.py
[^13]: PyInstaller hooks-contrib, `hook-nicegui.py`. https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/src/_pyinstaller_hooks_contrib/stdhooks/hook-nicegui.py
[^14]: PyInstaller hooks-contrib, `hook-webview.py`. https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/src/_pyinstaller_hooks_contrib/stdhooks/hook-webview.py
[^15]: PyInstaller built-in `gi` hooks, `hook-gi.repository.Gtk.py` and `PyInstaller/utils/hooks/gi.py` (`collect_typelib_data`). https://github.com/pyinstaller/pyinstaller/tree/develop/PyInstaller/hooks
[^16]: Nuitka, "Nuitka User Manual" (standalone/onefile, data files, AppImage/NSIS installers, `--target` experimental WASI). https://nuitka.net/user-documentation/user-manual.html
[^17]: Nuitka, "Nuitka Downloads" (AGPLv3 + runtime exception; version 4.3.5). https://nuitka.net/doc/download.html
[^18]: Nuitka, `PywebViewPlugin.py` (auto-enabled, backend selection). https://github.com/Nuitka/Nuitka/blob/develop/nuitka/plugins/standard/PywebViewPlugin.py
[^19]: Nuitka, `standard.nuitka-package.config.yml` (nicegui data files; torch DLLs/data; JIT disabled by default in standalone). https://github.com/Nuitka/Nuitka/blob/develop/nuitka/plugins/standard/standard.nuitka-package.config.yml
[^20]: Nuitka issue #2199, "Build time is too slow and executable is huge size" (45–60 min builds; ~1 GB torch apps). https://github.com/Nuitka/Nuitka/issues/2199
[^21]: Nuitka issue #3740, "NUITKA_PYTHONPATH not work" (numpy/torch 30+ min compile; `--nofollow-import-to` workaround unsupported), 2026-01-28. https://github.com/Nuitka/Nuitka/issues/3740
[^22]: Nuitka issue #754, "Very long compile times for pytorch standalone build". https://github.com/Nuitka/Nuitka/issues/754
[^23]: Briefcase documentation. https://briefcase.readthedocs.io/en/stable/
[^24]: Briefcase 0.4.7, `src/briefcase/platforms/linux/appimage.py` (AppImage warning banner: unreliable, no pre-compiled wheels, GTK/PySide problems). https://github.com/beeware/briefcase/blob/main/src/briefcase/platforms/linux/appimage.py
[^25]: Briefcase 0.4.7, `src/briefcase/platforms/windows/__init__.py` (MSI via WiX 5, ZIP; Windows-only). https://github.com/beeware/briefcase/blob/main/src/briefcase/platforms/windows/__init__.py
[^26]: cx_Freeze documentation. https://cx-freeze.readthedocs.io/en/stable/
[^27]: cx_Freeze, `bdist_appimage` command documentation and source (downloads appimagetool + type-2 runtime; no linuxdeploy). https://cx-freeze.readthedocs.io/en/stable/bdist_appimage.html
[^28]: cx_Freeze, `cx_Freeze/hooks/_torch_.py` (torch libs/bin, distributions/testing, excludes headers). https://github.com/marcelotduarte/cx_Freeze/blob/main/cx_Freeze/hooks/_torch_.py
[^29]: cx_Freeze FAQ, "Single-file executables" (only Linux AppImage; Windows MSI/self-extracting archive). https://cx-freeze.readthedocs.io/en/stable/faq.html#single-file-executables
[^30]: Demucs README, "License" (code MIT; weights license unclear; likely CC-NC-BY) and audio pipeline (`sphn`, `lameenc`, `ffmpeg`). https://github.com/adefossez/demucs#license
[^31]: HuggingFace `adefossez/HTDemucs` (84.0 MB safetensors). https://huggingface.co/adefossez/HTDemucs
[^32]: HuggingFace `adefossez/HTDemucs-ft` (4 × 84 MB = 336 MB). https://huggingface.co/adefossez/HTDemucs-ft
[^33]: Demucs 4.1.0, `demucs/hf.py` (HF download + safetensors) and `demucs/repo.py` (`LocalRepo` scans `.th`). https://github.com/adefossez/demucs/blob/main/demucs/hf.py
[^34]: torch 2.15.0, PyPI (wheel sizes, CUDA dependencies on Linux, Python >=3.11). https://pypi.org/project/torch/2.15.0/
[^35]: PyTorch CPU wheel index. https://download.pytorch.org/whl/cpu/torch/
[^36]: diffq, PyPI (wheels only through cp310). https://pypi.org/project/diffq/
[^37]: yt-dlp README, dependencies (ffmpeg/ffprobe, `yt-dlp-ejs`, JS runtime; Deno recommended; `--js-runtimes`). https://github.com/yt-dlp/yt-dlp#dependencies
[^38]: yt-dlp/ejs. https://github.com/yt-dlp/ejs
[^39]: `deno` PyPI package (42 MB wheels). https://pypi.org/project/deno/
[^40]: AppImage docs, "Manual packaging" (AppDir, AppRun, appimagetool). https://docs.appimage.org/packaging-guide/manual.html
[^41]: AppImage docs, "Best practices" (glibc: build on oldest base system; libstdc++ rule). https://docs.appimage.org/reference/best-practices.html
[^42]: AppImage docs, "I get some errors related to something called FUSE" (libfuse2, extract-and-run). https://docs.appimage.org/user-guide/troubleshooting/fuse.html
[^43]: appimagetool README (usage, `--comp`, `ARCH`). https://github.com/AppImage/appimagetool
[^44]: AppImage type-2 runtime README (statically linked runtime; libfuse2 no longer required on target; still mounts via FUSE). https://github.com/AppImage/type2-runtime
[^45]: linuxdeploy-plugin-gtk README (bundles GTK libs, GLib schemas, typelibs, gdk-pixbuf; not WebKit). https://github.com/linuxdeploy/linuxdeploy-plugin-gtk
[^46]: linuxdeploy-plugin-qt README (Qt plugins, QML resources). https://github.com/linuxdeploy/linuxdeploy-plugin-qt
[^47]: GTK3 documentation, "Running GTK Applications" (`GDK_BACKEND` wayland/x11 list). https://docs.gtk.org/gtk3/running.html
[^48]: WebKitGTK 2.52.0 release notes. https://www.webkitgtk.org/2026/03/18/webkitgtk2.52.0-released.html
[^49]: Lutris commit `dcb0f51`, "appimage: make WebKit login work on non-Ubuntu hosts" (helper path baked into `libwebkit2gtk-4.1.so.0`; `WEBKIT_EXEC_PATH` removed upstream; byte-patch + `/tmp` symlink). https://github.com/lutris/lutris/commit/dcb0f518e1dd7240348a29703fb26dd4f2d8bb60
[^50]: WebKit PR #6557, "[GTK][WPE] Fix relocation issues…" (WEBKIT_EXEC_PATH / WEBKIT_INJECTED_BUNDLE_PATH are compile-time defaults). https://github.com/WebKit/WebKit/pull/6557
[^51]: Microsoft Edge WebView2, "Distribute your app and the WebView2 Runtime" (Evergreen/Windows 11, bootstrapper ~2 MB, registry detection, Fixed Version >250 MB). https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
[^52]: Inno Setup help, `ArchitecturesInstallIn64BitMode` / `x64compatible`. https://jrsoftware.org/ishelp/topic_setup_architecturesinstallin64bitmode.htm
[^53]: Inno Setup help, `PrivilegesRequired` (admin vs lowest / per-user). https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm
[^54]: Inno Setup help, `Compression` (lzma2/max default, memory requirements). https://jrsoftware.org/ishelp/topic_setup_compression.htm
[^55]: Inno Setup help, `SignTool` (signs Setup, uninstaller, and signed files; large-data advice). https://jrsoftware.org/ishelp/topic_setup_signtool.htm
[^56]: Inno Setup help, `DiskSpanning` (required above 4.2 GB compressed; slices). https://jrsoftware.org/ishelp/topic_setup_diskspanning.htm
[^57]: Microsoft Learn, "Microsoft Defender SmartScreen overview" (reputation-based checks for files and certificates). https://learn.microsoft.com/en-us/windows/security/operating-system-security/virus-and-threat-protection/microsoft-defender-smartscreen/
[^58]: Microsoft Learn, "What is Artifact Signing?" (formerly Trusted Signing; FIPS 140-3 L3 HSMs). https://learn.microsoft.com/en-us/azure/artifact-signing/overview
[^59]: Microsoft Learn, "Quickstart: Set up Artifact Signing" (Public Trust geography; identity validation 1–20 business days). https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart
[^60]: Microsoft Learn, "Set up signing integrations" (SignTool `/dlib` + `/dmdf`, GitHub Actions `azure/artifact-signing-action`, timestamp endpoint). https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-signing-integrations
[^61]: Microsoft Lifecycle, "Windows 10 Home and Pro" (end of support 2025-10-14). https://learn.microsoft.com/en-us/lifecycle/products/windows-10-home-and-pro
[^62]: GitHub Docs, "GitHub-hosted runners reference" (ubuntu-22.04/24.04, windows-2022/2025, 4 vCPU/16 GB/14 GB; windows-latest = 2025). https://docs.github.com/en/actions/reference/runners/github-hosted-runners
[^63]: GitHub Docs, "Actions limits" (artifact storage 500 MB Free/1 GB Pro/2 GB Team/50 GB Enterprise; cache 10 GB; 6-hour job; 256-job matrix). https://docs.github.com/en/actions/reference/limits
[^64]: GitHub Docs, "Dependency caching reference" (10 GB per repo default; 7-day unused eviction). https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
[^65]: GitHub Docs, "About releases" (1,000 assets/release; each file < 2 GiB; no total limit). https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
[^66]: actions/upload-artifact README (v7; artifact permissions become 755/644; `archive: false` for single files). https://github.com/actions/upload-artifact
[^67]: FFmpeg, "License and Legal Considerations" (LGPL checklist: no `--enable-gpl`, dynamic linking, source distribution, attribution). https://www.ffmpeg.org/legal.html
[^68]: BtbN/FFmpeg-Builds README (gpl/lgpl/shared variants; Windows asset sizes from the release). https://github.com/BtbN/FFmpeg-Builds
[^69]: Ubuntu package `libc6` in jammy (2.35). https://packages.ubuntu.com/jammy/libc6
[^70]: Ubuntu package `libc6` in noble (2.39). https://packages.ubuntu.com/noble/libc6
[^71]: Arch Linux package metadata, `webkit2gtk-4.1` 2.54.2-1 (installed size 141.22 MiB; GTK3 53.22 MiB; `pacman -Si`, 2026-11-14). https://archlinux.org/packages/extra/x86_64/webkit2gtk-4.1/
[^72]: PyGObject 3.50.0 `meson.build` (GLib >= 2.64, girepository >= 1.64). https://gitlab.gnome.org/GNOME/pygobject/-/blob/3.50.0/meson.build
[^73]: PySide6 6.12.0, PyPI (`manylinux_2_34_x86_64` wheels). https://pypi.org/project/PySide6/6.12.0/
[^74]: yt-dlp/FFmpeg-Builds (recommended ffmpeg builds). https://github.com/yt-dlp/FFmpeg-Builds
[^75]: Companion research 02, `.scratch/stemforge/research/02-separation-engine.md` (PyInstaller worker 881 MB on disk / 197 MB zstd; model-weight licence analysis).
[^76]: Companion research 01, `.scratch/stemforge/research/01-desktop-shell-and-audio-preview.md` (MSHTML fallback, GStreamer dependency, AppImage/WebKit findings).
[^77]: Companion research 03, `.scratch/stemforge/research/03-pitch-shift-and-media-pipeline.md` (FFmpeg 9.0.2 build choice, rubberband filter, Deno).
