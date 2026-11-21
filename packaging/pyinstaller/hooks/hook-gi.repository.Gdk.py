"""Keep this GI namespace on the host; the Bundle is a thin wrapper (ADR 0011).

PyInstaller's built-in ``gi.repository`` hooks collect the typelib and shared
libraries from the build machine. StemForge ships PyGObject itself (the frozen
CPython cannot import the host's ``gi``) but deliberately leaves GTK, WebKitGTK,
Soup, GStreamer, and their dependencies to the host. Overriding the hook with
this no-op file prevents the host GUI stack from leaking into ``_internal/``.
"""
