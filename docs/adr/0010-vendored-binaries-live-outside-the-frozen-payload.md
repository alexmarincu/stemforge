---
status: accepted
---

# Vendored binaries live outside the frozen payload

PyInstaller's `binaries=` mechanism collects executables into `_internal/`, alongside the frozen CPython and torch libraries. StemForge instead ships FFmpeg, ffprobe, Rubber Band, its libsndfile, and Deno in `<bundle>/vendor/`, a sibling of `_internal/`, `LICENSES/`, and the launcher executable. Frozen code and data still resolve from `sys._MEIPASS`; vendored binaries and licence files resolve from the Bundle root, `Path(sys.executable).resolve().parent` when frozen and the repository root in development. The separation worker inherits the rule unchanged because it is the same executable re-invoked as `sys.executable --worker separation`.

**Considered options:** letting `binaries=` collect them into `_internal/` (rejected — the licence tree is buried, libsndfile cannot be shipped as a separately replaceable file, and PyInstaller rewrites the files); downloading the tools on first run (rejected — four more moving parts, no offline install, and a second consent surface); keeping them in `_internal/` with symlinks from the Bundle root (rejected — Windows does not preserve that layout reliably).

**Consequences:** `platform/resources.py` and `platform/binaries.py` carry two roots rather than one, and `scripts/licensing/verify_bundle.py` must walk the Bundle root, not only `_internal/`; the licence tree is visible in the file manager, the Start Menu, and the AppImage's `usr/share/doc`, which is what the GPL notices are for; the vendor files can be inspected and replaced without unpacking the frozen payload; and a one-file PyInstaller build, which has no Bundle root, can no longer express this layout.
