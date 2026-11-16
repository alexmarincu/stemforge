# 01 — Desktop shell and audio-preview feasibility

**Type:** research
**Status:** resolved
**Blocked by:** none

## Question

Can NiceGUI 4.0 native mode plus pywebview 6.2 deliver a native-feeling Linux/Windows window with native open/save dialogs and reliable in-window audio preview?

Resolve:

- Pywebview backend per OS (GTK/WebKitGTK vs Qt on Linux; EdgeChromium/WebView2 on Windows), lifecycle, and threading constraints.
- Native file-dialog API for open (multi-select, audio filters) and save (suggested filename), including whether dialogs must run on the GUI thread.
- HTML5 `<audio>` codec support in each backend; which GStreamer plugins Linux needs; whether NiceGUI's local server supports range requests and seeking; whether `file://` URLs are viable.
- Packaging implications for each backend (GTK/WebKit libraries on Linux, WebView2 runtime on Windows).

The answer must recommend one backend and one preview strategy, list unsupported paths, and cite versions and URLs. Research report: `../research/01-desktop-shell-and-audio-preview.md`.

## Answer

**Recommendation: keep NiceGUI native mode (`ui.run(native=True)`) + pywebview; do not hand-roll a shell.**

Verified against NiceGUI 4.0.0 and pywebview 6.2.1 source, plus measured media routes. Full report: `../research/01-desktop-shell-and-audio-preview.md`.

Key findings:

- **Process model:** native mode runs uvicorn in the main process and the window in a spawned child; `app.native.*` config must be module-level and picklable; `freeze_support()` must be first in the main guard for frozen builds.
- **Backends:** Linux = GTK3 + WebKitGTK 4.1 (PyGObject is a distro dependency, not installed by the NiceGUI `native` extra); Windows = EdgeChromium/WebView2, which pywebview silently replaces with MSHTML/IE11 if the runtime is missing. Ship the WebView2 bootstrapper and assert the backend at startup.
- **Dialogs:** `app.native.main_window.create_file_dialog(...)` works for open/save/folder, but filters have a strict grammar (description `[\w ]+`, patterns `*.ext` separated by `;` with no spaces); Linux does not append the save extension, so the app must. Windows has a known cross-thread deadlock risk (pywebview #1823).
- **Preview:** serve media over NiceGUI's local HTTP route with `app.add_media_files()` and `<audio>`; range seeking works with `Range: bytes=N-` (measured 206). `file://` is out. MP3 preview is the safest codec; Linux needs `gst-plugins-good` (only an Arch optdepend), so a startup GStreamer preflight is required. AAC/Opus are not guaranteed; never preview `.m4a`/`.opus`.
- **Limits:** `ui.audio` is single-track only (no synchronized stem mixer); a custom Vue component would be needed post-v1. Window-close veto is not bridged, so design for autosave/checkpointing. `private_mode=True` disables GTK local storage and resets `app.storage.user`; persist preferences elsewhere or set `private_mode=False`.
- **Packaging:** Linux is the top risk. Bundling WebKitGTK/GStreamer into an AppImage is a documented failure mode; the low-risk v1 shape is tar.gz primary with host `webkit2gtk-4.1` + GStreamer dependencies, and AppImage only as a thin wrapper that bundles no webkit. Windows onedir + Inno Setup is viable; pywebview's PyInstaller hook covers Windows data/JS but nothing for Linux.

Open items for later tickets: real-device `HEAD`/suffix-range behavior, autoplay policy, GTK drop-path fidelity, and the AppImage shape (ticket 10).
