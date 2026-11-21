# GCC runtime libraries

- Version: see the dependency table
- Licence: GPL-3.0-or-later WITH GCC-exception-3.1
- Copyright (C) 2009 Free Software Foundation, Inc.
- Homepage: https://gcc.gnu.org/
- Source: https://gcc.gnu.org/git/?p=gcc.git

The Linux PyTorch and NumPy wheels convey `libgomp`, `libgfortran`, and
`libquadmath` from the GCC runtime. They are covered by GPL-3.0-or-later with
the GCC Runtime Library Exception 3.1 and are treated as System Libraries under
GPLv3 section 1: they are noticed here and excluded from the Corresponding
Source bundle. StemForge deliberately does not bundle `libstdc++.so.6` or
`libgcc_s.so.1`: host WebKitGTK/ICU must resolve their C++ ABI against the
distribution's runtime. If you believe a stricter offer is required, contact us
through the source offer.

## Licence texts

- [`GPL-3.0-or-later`](../common/GPL-3.0-or-later.txt)
- [`GCC-exception-3.1`](../common/GCC-exception-3.1.txt)
