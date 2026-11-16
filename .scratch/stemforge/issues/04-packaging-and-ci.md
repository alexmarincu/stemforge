# 04 — Packaging toolchain and CI feasibility

**Type:** research
**Status:** resolved
**Blocked by:** none

## Question

What is the most reliable way to build standalone Linux and Windows bundles for a NiceGUI/pywebview app that embeds Python, torch/Demucs, ffmpeg, and yt-dlp?

Resolve:

- PyInstaller vs Nuitka vs Briefcase vs cx_Freeze: NiceGUI/pywebview support, torch hidden imports and DLLs, data files, one-dir vs one-file, startup time, size, and cross-platform constraints.
- Linux: AppImage recipe (appimagetool/linuxdeploy, GTK/WebKitGTK bundling, glibc baseline, Wayland/X11) and tar.gz layout.
- Windows: portable ZIP, Inno Setup, WebView2 detection/bootstrapper, code signing/SmartScreen.
- GitHub Actions matrix (ubuntu-22.04/24.04, windows-2022/2025), caching torch/weights, artifact size/time limits, and the Windows-only build constraint.
- Model-weight bundling: release artifact vs first-run download.

Recommend a v0.1.0 build strategy, directory layout, scripts, and CI outline. Research report: `../research/04-packaging-and-ci.md`.

## Answer

**Recommendation: PyInstaller 6.22.x, `--onedir`, native builds per OS; Linux tar.gz primary with an opt-in thin AppImage; Windows portable ZIP + per-user Inno Setup; first-run model download.**

Measured on Linux; full report: `../research/04-packaging-and-ci.md`.

Key findings:

- **Packager:** PyInstaller onedir with NiceGUI 4.0 + torch 2.15 CPU + Demucs 4.1 measured at **875 MB / 3,787 files / 120 s build**, reaching HTTP 200 in **2.2 s**. Without torch: 97 MB / 54 s / 0.9 s. One-file failed at 875 MB during `objcopy` on a 12 GB tmpfs and re-extracts on every launch; never use onefile.
- **Frozen-build traps:** `freeze_support()` must be first in the main guard; omitting it produced a fork bomb. `nicegui-pack` works because it adds the whole NiceGUI package, including `.py` files, as data; the official hook alone misses lazy submodules.
- **Linux:** build on `ubuntu-22.04` (glibc 2.35). Bundle Python/torch/ffmpeg/Deno, **not** GTK/WebKitGTK/GStreamer. Require host `libwebkit2gtk-4.1-0`, the GIR typelib, and `gst-plugins-good`; preflight and fall back to browser mode. AppImage is a thin wrapper only: WebKitGTK helper paths are compile-time absolute, `WEBKIT_EXEC_PATH` was removed in 2.52, and linuxdeploy cannot bundle the helper executables.
- **Windows:** build on `windows-2022`; portable ZIP + per-user Inno Setup; WebView2 bootstrapper and backend assertion. Windows 10 is EOL since 2025-10-14; treat Windows 11 as primary and Windows 10 22H2 + ESU as best-effort.
- **Model weights:** do not bundle in v0.1.0. The licence is explicitly unclear and likely CC-NC-BY; ship a first-run download pinned by revision + SHA-256 with a licence notice, and revisit an offline pack only after legal review. This conflicts with the "self-contained bundle" principle; tickets 13 and 10 must decide.
- **CI:** publish release assets, not Actions artifacts (GitHub Free artifact storage is 500 MB). Windows bundles can only be produced on Windows runners. Cache pip and vendor binaries; keep PR CI under ~15 min; release builds ~12–20 min Linux, ~20–35 min Windows.
- **Size:** onedir ~1.0 GB Linux / ~830 MB Windows; tar.gz ~350–450 MB; AppImage ~280–350 MB; Windows ZIP ~350–420 MB; Inno ~300–350 MB. Trimming `torch/test` + `torch/bin` saves 162 MB.
- **Rejected:** Nuitka (30–60+ min builds, no size win, v0.1.0 option), Briefcase (its own AppImage warning; cannot use precompiled wheels), cx_Freeze (credible fallback, no pywebview hook).

Open items: Windows native-window CI (needs a self-hosted VM or manual test), WebKitGTK version floor, code-signing identity, FFmpeg build choice, Windows 10 support policy, and the model-weight redistribution decision.

## Comments

**2026-11-14 — nicegui-pack follow-up.** Yes: `nicegui-pack` was checked and used for the measured builds (NiceGUI-only and NiceGUI + torch + Demucs), and the linked docs page was read. It is a thin CLI wrapper over PyInstaller (`nicegui/scripts/pack.py`) that assembles a PyInstaller command line. It supports `--name`, `--onedir`/`--onefile`, `--windowed`, `--icon`, `--add-data`, `--clean`, `--noconfirm`; its default `--add-data` adds the whole installed `nicegui` directory, including `.py` files, which is what makes PEP 562 lazy imports resolve. It does **not** accept a custom `.spec` and exposes no `--hidden-import`, `--collect-submodules`, `--runtime-hook`, or `--additional-hooks-dir`.

Consequence for v0.1.0: the release build stays `python -m PyInstaller packaging/pyinstaller/stemforge_{linux,windows}.spec`. The spec must carry the same mechanism (the whole `nicegui` package as data, as measured, or `collect_submodules('nicegui')`), then add the torch `test`/`bin` trim, the vendored ffmpeg/ffprobe/Rubber Band/Deno binaries, the WebKit2 typelib hook, and Windows `console=False`/icon — none of which `nicegui-pack` can express. Keep `nicegui-pack --onedir` as an optional quick environment smoke build, not the release path.

Reference: <https://nicegui.io/documentation/section_configuration_deployment#package_for_installation> and `nicegui/scripts/pack.py` at NiceGUI 4.0.0.
