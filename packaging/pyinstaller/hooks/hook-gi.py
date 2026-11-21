"""Collect PyGObject itself, never the host's GI libraries or typelibs (ADR 0011).

The built-in ``hook-gi.repository.<Namespace>`` hooks copy the namespace's
typelib and shared libraries from the build machine. StemForge takes GTK,
WebKitGTK, Soup, and GStreamer from the host, so strip that collection centrally:
only the Python ``gi`` package (including the overrides, collected by the spec)
belongs in ``_internal/``.
"""

from PyInstaller.utils.hooks.gi import GiModuleInfo


def _collect_python_only(self: GiModuleInfo) -> tuple[list, list, list]:
    del self
    return [], [], []


GiModuleInfo.collect_typelib_data = _collect_python_only  # type: ignore[method-assign]

# The built-in hook for the pinned PyGObject 3.50.x. Bump these if PyGObject
# moves past 3.52 (which also needs ``gi._enum``).
hiddenimports = ["gi._error", "gi._option", "asyncio"]
