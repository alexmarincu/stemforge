# PyInstaller hooks for the Linux Bundle

PyInstaller's built-in `gi.repository.*` hooks copy the GTK/WebKitGTK/Soup/
GStreamer typelibs and shared libraries from the build machine. StemForge's
Linux Bundle is a thin wrapper (ADR 0011): it ships PyGObject itself, because
the frozen CPython cannot import the host's `gi`, but every GUI library and
typelib stays on the user's desktop.

- `hook-gi.py` patches `GiModuleInfo.collect_typelib_data` so no built-in
  `gi.repository` hook can collect host libraries or typelibs.
- `hook-gi.repository.*.py` are explicit no-ops for the namespaces the app
  imports or may import dynamically. They also stop the Gtk hook's icon, theme,
  fontconfig, and translation collection.
- The spec additionally calls `Analysis.exclude_system_libraries()` and filters
  `libstdc++.so.6`/`libgcc_s.so.1`, and `verify_bundle.py` fails the build if
  any host GUI data or library leaks back in (`bundle.host_gui`).

Do not replace these with the built-in hooks unless StemForge starts bundling
the complete GTK/WebKit stack, which is a different ADR.
