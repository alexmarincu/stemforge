---
status: accepted
---

# Linux disables the WebKit DMABUF renderer

WebKitGTK 2.52 and newer can fail to allocate a GBM buffer on NVIDIA/X11 and paint the whole web view white while the GTK frame renders. The previous guard, `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`, kept the view on the shared-memory renderer but made every Linux host pay its default-size cost: at 1280x820 the renderer used 56.9% of a core while scrolling and 180% during a resize burst, against 22.7% and 75% with the DMABUF renderer disabled. The page-wide hover paint that first motivated the guard was a Quasar button layer promotion, fixed separately by replacing the button's animated focus helper with a non-animated overlay; it was not a renderer cost.

**Decision.** On Linux, `configure_window` sets `WEBKIT_DISABLE_DMABUF_RENDERER=1` with `os.environ.setdefault`, so an explicit user value still wins. The renderer guard keeps WebKitGTK off the DMABUF path that paints the blank view. StemForge no longer sets `WEBKIT_DMABUF_RENDERER_FORCE_SHM`; a user who exported the old variable may remove it.

**Considered options:** keeping `FORCE_SHM` (rejected: measured default-size CPU cost on every Linux host, while the DMABUF-disabled path passed the white-screen check on the validated host); gating the guard by WebKitGTK version, GPU vendor, driver, or session type (rejected: the failure boundary is not characterised, host detection would guess at the predicate, and a gate that failed open would reintroduce the blank view); removing the guard (rejected: the blank view reproduces at all three measured sizes). Unverified third-party reports that the DMABUF-disabled path crashes or partially paints on some newer NVIDIA/WebKitGTK combinations are recorded in the verification record's limits; they were not reproduced on the validated host and are the reason `FORCE_SHM` remains the documented fallback, not the shipped default.

**Consequences:** the shipped guard is validated on WebKitGTK 2.54.3 with NVIDIA driver 470.256.02 on X11; the white-screen check passes at 1280x820, 1920x1080, and 3640x1920, and the corrected latency and cost pairs are in [the verification record](../verification/linux-white-window.md). Other WebKitGTK versions, drivers, and Wayland are unverified. The user override is `WEBKIT_DISABLE_DMABUF_RENDERER=0`; `WEBKIT_SKIA_ENABLE_CPU_RENDERING=1` remains the next fallback if the view is still blank. This decision does not change the thin-AppImage support case in [ADR-0011](0011-the-linux-appimage-is-a-thin-host-dependent-wrapper.md).
