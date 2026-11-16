---
status: accepted
---

# The Linux AppImage is a thin, host-dependent wrapper

The AppImage mounts the same onedir Bundle as the tarball, but it does not carry GTK, WebKitGTK, or GStreamer. WebKitGTK spawns `WebKitWebProcess` and `WebKitNetworkProcess` from a path compiled into `libwebkit2gtk-4.1.so.0`; `WEBKIT_EXEC_PATH` was removed in 2.52, so bundling the library means byte-patching it, recreating its helper path at runtime, and retesting every WebKit release. StemForge v0.1.0 therefore requires host `libgtk-3-0`, `libwebkit2gtk-4.1-0` (2.44+), `gir1.2-webkit2-4.1`, and `gstreamer1.0-plugins-good` for native mode; `run.sh` and `--preflight` check them and fall back to browser mode instead of failing. The tarball is the primary Linux artifact and the AppImage is an opt-in convenience for hosts that already satisfy the dependencies.

**Considered options:** bundling WebKitGTK and GStreamer (rejected — version-specific byte-patching, a security-sensitive update surface, and a documented blank-window failure mode on Wayland + NVIDIA); switching the Linux backend to QtWebEngine (rejected for v0.1.0 — about 250–400 MB more, `--no-sandbox` workarounds on some distributions, unverified codec builds; worth a post-v0.1.0 spike); shipping the tarball only (rejected — the AppImage still gives desktop integration where the host libraries exist, at the cost of one extra asset).

**Consequences:** the AppImage is not a "download and run" promise and must say so wherever it is offered; the tarball and the AppImage share one payload, so a fix never needs two builds; a host without WebKitGTK, GStreamer, or FUSE still has a usable app through browser mode or the tarball; Wayland + NVIDIA + WebKitGTK 2.52+ remains a support case with documented environment workarounds; and a fully self-contained Linux artifact would require a new decision, not a quiet change to the recipe.
