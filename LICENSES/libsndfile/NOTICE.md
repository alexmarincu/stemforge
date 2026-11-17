# libsndfile

- Version: 1.2.2
- Licence: LGPL-2.1-or-later
- Copyright (c) 1999-2024 Erik de Castro Lopo and contributors
- Homepage: https://libsndfile.github.io/libsndfile/
- Source: https://github.com/libsndfile/libsndfile/releases/tag/1.2.2

libsndfile 1.2.2 is built from the pinned source tarball as a shared, WAV-only
library (`ENABLE_EXTERNAL_LIBS=OFF`, `ENABLE_MPEG=OFF`). It ships as
`libsndfile.so.1` or `sndfile.dll` beside the Rubber Band executable, as a
separate and replaceable file, so the LGPL relink right is preserved. The CMake
configure line is recorded in `LICENSES/libsndfile/BUILDINFO.txt`.

## Licence texts

- [`LGPL-2.1-or-later`](../common/LGPL-2.1-or-later.txt)
