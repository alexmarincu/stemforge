# PyGObject

- Version: 3.50.2
- Licence: LGPL-2.1-or-later
- Copyright (C) the PyGObject contributors
- Homepage: https://pygobject.gnome.org/
- Source: https://gitlab.gnome.org/GNOME/pygobject/-/tags/3.50.2

PyGObject is the GTK/WebKit bridge used by pywebview in the Linux bundle. It is
built for the frozen CPython 3.13 and shipped as a separate, replaceable `gi`
package under `_internal/`, so the LGPL relinking right is preserved. The GTK 3,
WebKitGTK 4.1, and Soup 3 typelibs and shared libraries remain host-provided;
PyGObject does not carry them.

## Licence texts

- [`LGPL-2.1-or-later`](../common/LGPL-2.1-or-later.txt)
