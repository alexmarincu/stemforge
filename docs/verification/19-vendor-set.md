# Ticket 19 — Vendor binaries and lock

Measured in the `ticket/19-vendor` worktree on Linux x86_64, 2026-11-17. Host:
Arch Linux kernel 7.3.4, glibc 2.44, Python 3.13.18 (uv), GCC 16.2.1, CMake
4.5.0, Meson 1.12.1, Ninja 1.13.2, patchelf 0.19.3. Network was live for the
fetch; `vendor/` is git-ignored and the repository commits no binaries.

## What was built

`packaging/vendor.lock.json` pins five components for both targets:

| Component | Version | Licence | Linux | Windows |
|---|---|---|---|---|
| `ffmpeg` | `n9.0.2-57-gd838f9f9ae` | GPL-3.0-or-later | BtbN GPL static archive | BtbN GPL static archive |
| `ffprobe` | `n9.0.2-57-gd838f9f9ae` | GPL-3.0-or-later | same BtbN archive | same BtbN archive |
| `deno` | `2.9.8` | MIT | Deno release zip | Deno release zip |
| `rubberband` | `4.0.0` | GPL-2.0-or-later | Meson build, `v4.0.0` tag | MSVC/Meson build line |
| `libsndfile` | `1.2.2` | LGPL-2.1-or-later | CMake shared build | MSVC/CMake build line |

The installed tree matches ticket 10 §2 and the preflight expectations:

```text
vendor/
├── ffmpeg/ffmpeg
├── ffmpeg/ffprobe
├── deno/deno
└── rubberband/
    ├── rubberband
    └── libsndfile.so.1
```

## Fetch and build measurement

`scripts/vendor-fetch.sh` with an empty `vendor/` and an empty cache:

| Step | Wall time |
|---|---:|
| FFmpeg download + SHA-256 + extract | 9 s |
| ffprobe (same cached archive) | 1 s |
| Deno download + SHA-256 + extract | 5 s |
| libsndfile CMake configure/build/install/strip | 18 s |
| Rubber Band Meson configure/build/strip/patchelf | 13 s |
| Verification, install, provenance, summary | ~7 s |
| **Cold total** | **53.416 s** |

A second cold run measured 51.164 s; the FFmpeg download alone measured 16.08 s
on an earlier run. The warm idempotent path (verify an already complete tree)
measured **3.350 s**, and `--verify-only` measured **3.282 s**. The script
reports per-component times on every run.

Commands:

```bash
scripts/vendor-fetch.sh              # fetch, build, verify, install
scripts/vendor-fetch.sh --verify-only
scripts/vendor-fetch.sh --force      # rebuild even when complete
```

## SHA-256 results

Every download matched the lock. The FFmpeg/Rubber Band/libsndfile values were
checked against the pins in `CONTEXT.md`; the Deno values came from the official
`deno-*.sha256sum` release files.

| Artifact | Size (bytes) | SHA-256 | Result |
|---|---:|---|---|
| `ffmpeg-n9.0.2-57-gd838f9f9ae-linux64-gpl-9.0.tar.xz` | 152,700,372 | `72836132863e0d40e2af74a8ef59c32b10771aed0c8540b634fb77db2e6e3ae2` | match |
| `deno-x86_64-unknown-linux-gnu.zip` | 41,963,064 | `e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609` | match |
| `rubberband-4.0.0.tar.gz` | 290,305 | `24300f48a8014b7c863b573a9647e61b1b19b37875e2cdd92005e64c6424d266` | match |
| `libsndfile-1.2.2.tar.xz` | 730,760 | `3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e` | match |

Extracted downloaded files, also recorded in the lock and re-checked by
`tests/integration/test_vendor_binaries.py`:

| Installed file | Size (bytes) | SHA-256 |
|---|---:|---|
| `vendor/ffmpeg/ffmpeg` | 173,024,816 | `3bfd3efb3208bcd1d7600bdd3f2bdc8b3ee0964265364414204a50b8bad9be25` |
| `vendor/ffmpeg/ffprobe` | 172,795,376 | `d9717831fbc178cc64232c944aa372cc726a5c1077f8b31e1e234a89ca534c9d` |
| `vendor/deno/deno` | 96,055,992 | `368bca08999e02c6911b8f248167b582276b1f547db5712b6e736d7eda1dbda7` |
| `vendor/rubberband/rubberband` | 528,656 | built output; hash varies per build (see below) |
| `vendor/rubberband/libsndfile.so.1` | 613,960 | `f8244f2494c26d49ca2f4da7a67481a1e809b626e66407970776338570407e5a` |

`libsndfile.so.1` was byte-identical across the two builds. The stripped
Rubber Band CLI was not: `0d40dc5b…fe3e`, `b99324c0…6c68`, and `b2faed72…ff59`
on successive builds, so the lock deliberately records `sha256: null` for
source-built outputs and pins the source archive instead. The archive SHA-256 is
the reproducibility input; the installed hash is informational in
`vendor/PROVENANCE.txt`.

Total installed vendor payload (excluding the 187 MB download cache):

| Path | Size |
|---|---:|
| `vendor/ffmpeg` | 329.8 MiB |
| `vendor/deno` | 91.6 MiB |
| `vendor/rubberband` | 1.1 MiB |
| **Total** | **422.5 MiB** (443,018,800 bytes) |

Ticket 20's bundle budget should treat this as the vendor contribution; the
`.cache/` archives are build scratch and are never packaged.

## `--version` outputs

```text
$ vendor/ffmpeg/ffmpeg -version
ffmpeg version n9.0.2-57-gd838f9f9ae-20261115 Copyright (c) 2000-2026 the FFmpeg developers
built with gcc 16.2.0 (crosstool-NG 1.29.0.36_fbb6372)

$ vendor/ffmpeg/ffprobe -version
ffprobe version n9.0.2-57-gd838f9f9ae-20261115 Copyright (c) 2007-2026 the FFmpeg developers

$ vendor/rubberband/rubberband --version
4.0.0

$ vendor/deno/deno --version
deno 2.9.8 (stable, release, x86_64-unknown-linux-gnu)
v8 15.0.245.2-rusty
typescript 6.0.3
```

`platform.preflight` matches the lock's `n9.0.2-57-gd838f9f9ae`, `4.0.0`, and
`2.9.8` substrings. FFmpeg uses `-version`; Rubber Band and Deno use
`--version`; both flags are recorded per component in the lock.

## Rubber Band loads its own libsndfile

The CLI was built with `--default-library=static` (so `librubberband` is inside
the executable) and with `-Wl,-rpath,$ORIGIN`; `patchelf --set-rpath $ORIGIN`
removed the build-prefix fallback. A clean-loader run resolves the co-located
library:

```text
$ readelf -d vendor/rubberband/rubberband | grep -E 'NEEDED|RUNPATH'
 (NEEDED) Shared library: [libsndfile.so.1]
 (NEEDED) Shared library: [libstdc++.so.6]
 (NEEDED) Shared library: [libm.so.6]
 (NEEDED) Shared library: [libgcc_s.so.1]
 (NEEDED) Shared library: [libc.so.6]
 (RUNPATH) Library runpath: [$ORIGIN]

$ env -u LD_LIBRARY_PATH ldd vendor/rubberband/rubberband | grep sndfile
 libsndfile.so.1 => .../vendor/rubberband/libsndfile.so.1
```

`platform.binaries.tool_environment()` additionally prepends the executable's
directory to `LD_LIBRARY_PATH` for every Rubber Band child process, so a rebuilt
or manually replaced CLI still finds its library even without an rpath. No
system libsndfile is required, and the only remaining shared dependencies are
glibc, libstdc++, libm, and libgcc_s.

## Lock schema

The full field table is in [`packaging/vendor/README.md`](../../packaging/vendor/README.md).
In short:

- `schema`, `vendor_root`, `targets` (`linux-x86_64`, `windows-x64`).
- `components.<tool>`: `version`, `version_flag`, `license`, `license_texts`,
  `provenance`, and one entry per target.
- A target entry is `kind: archive` (URL, filename, size, SHA-256,
  `archive_format`) or `kind: built` (the same fields under `source`, plus a
  `build` block with `system`, `configure` argv, `post_build`, and notes).
- Every target has an `install` list: `from` (archive member or build output),
  `to` (vendor-relative path), `executable`, and an optional extracted SHA-256.
- `source_offer` records the GPL-3.0 §6(b) written offer, three-year term,
  release/contact URLs, and every archive input for ticket 22.

`packaging/vendor/SOURCES.md` is the human-readable ticket 22 input: component
versions, licences, exact archives, sizes, hashes, FFmpeg build-source
requirements, and the no-weights statement.

## Frozen bundle check

`packaging/linux/build.sh` built successfully with the ticket 19 spec change; the
frozen payload contains `_internal/vendor.lock.json`. Copying the vendor tree
beside the launcher (ticket 20 performs this in the release job) and running the
frozen binary with `STEMFORGE_REQUIRE_VENDOR=1`:

```text
$ STEMFORGE_REQUIRE_VENDOR=1 dist/StemForge/StemForge --self-test
exit 0; summary {failed: 0, missing: 0, ok: 19, skipped: 0}
vendor.ffmpeg     ok  .../vendor/ffmpeg/ffmpeg: ffmpeg version n9.0.2-57-gd838f9f9ae...
vendor.ffprobe    ok  .../vendor/ffmpeg/ffprobe: ffprobe version n9.0.2-57-gd838f9f9ae...
vendor.rubberband ok  .../vendor/rubberband/rubberband: 4.0.0
vendor.deno       ok  .../vendor/deno/deno: deno 2.9.8 ...
```

`--preflight --json` reported `bundle ok: ffmpeg, ffprobe, rubberband, and deno
match vendor.lock.json; models.lock.json is readable`. The process exited 3 on
this host because GTK/WebKitGTK are absent — the documented degraded native
window, not a bundle failure.

A real pitch render from the frozen vendor tree also succeeded with a clean
loader environment:

```text
$ env -u LD_LIBRARY_PATH dist/StemForge/vendor/rubberband/rubberband \
    -3 -F -p 3 --ignore-clipping tone.wav out.wav
elapsed time: 0.216699 sec
$ dist/StemForge/vendor/ffmpeg/ffprobe ... out.wav
pcm_f32le, 44100 Hz, 2 channels, duration 1.000000
```

## Tests and gates

Run in this worktree with `vendor/` present:

| Gate | Result |
|---|---|
| `pytest -m "unit or ui"` | 721 passed, 1 skipped, 137 deselected |
| `pytest -m "integration and not slow"` | 128 passed |
| `pytest -m "integration and slow"` | 9 passed (includes 7 vendor tests) |
| `stemforge --self-test` with `STEMFORGE_REQUIRE_VENDOR=1` | exit 0; all four vendor checks `ok` |
| `stemforge --smoke-test` | exit 0; HTTP 200 |
| `scripts/vendor-fetch.sh --verify-only` | exit 0; 6 install rows verified |
| `ruff format --check`, `ruff check` | clean |
| `mypy src/stemforge` (strict) | clean |
| `lint-imports` | 2 contracts kept |
| `shellcheck -x scripts/vendor-fetch.sh` | clean |

The vendor integration test skips cleanly when `vendor/` is absent. The
`tests/unit/test_self_test.py::test_self_test_requires_vendor_binaries_when_asked`
test skips in a checkout that has `vendor/`, because its premise is an empty
vendor tree; CI's unit job (no vendor) still runs it.

## Windows status and deviations

- `scripts/vendor-fetch.ps1` is committed and syntactically parsed with
  PowerShell 7.5.4. Its `-VerifyOnly` path was exercised against a fake
  `windows-x64` tree (with shell-script stand-ins for the `.exe` files) and
  exited 0. It has **not** been executed on a Windows host; the `windows` unit
  job parses it, and ticket 20's Windows build job is the first real execution.
- The decision record names no prebuilt public Rubber Band archive. The Windows
  script therefore downloads FFmpeg and Deno from the pinned URLs and requires a
  pinned `rubberband.exe` + `sndfile.dll` pair via `-RubberBandPrebuilt`, which
  it verifies against `--version` 4.0.0 and an optional archive SHA-256. The
  MSVC/CMake/Meson build lines for that pair are recorded in the lock for the
  release job; no URL was invented.
- The task text mentioned "static libsndfile into the Rubber Band CLI". The
  resolved decision record (ticket 10 §4 and ticket 13) requires a **shared**
  WAV-only `libsndfile.so.1` / `sndfile.dll` beside the CLI to preserve the LGPL
  relink right. The implementation follows the decision record. The CLI has no
  system dependency beyond glibc/libstdc++ because the shared library is
  co-located and found through `$ORIGIN`.
- The script paths are `scripts/vendor-fetch.{sh,ps1}` per the ticket 19
  work order rather than ticket 10's `packaging/vendor.{sh,ps1}`.
