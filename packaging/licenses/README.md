# Licence tree: inputs, outputs, and the bundle contract

This directory holds the curated inputs for
[`scripts/generate_notices.py`](../../scripts/generate_notices.py), which
generates the committed licence artifacts from `uv.lock`, the build virtualenv's
dist-info, and these manifests. Nothing here is hand-maintained for the Python
dependency graph.

## Commands

```bash
uv sync --frozen                                  # the generator reads .venv dist-info
uv run python scripts/generate_notices.py --check # CI gate: fail on any drift
uv run python scripts/generate_notices.py --check-release  # release.toml placeholder gate
uv run python scripts/generate_notices.py         # regenerate the committed tree
```

The sync must include the default `dev` group: the tree also lists build/test
packages, so their dist-info must be present (or curated in `python.toml`).
`uv sync --frozen` and `uv sync --frozen --group build` both satisfy this.

`--check` regenerates into a temporary directory and byte-compares it with the
committed `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and
`LICENSES/`. It reports the first differing file and a unified diff summary. It
also fails when:

- `packaging/release.toml` contains `CHANGEME`, `example.com`, `example.org`, an
  empty value, or a `<PLACEHOLDER>` token;
- a package in `uv.lock` has no SPDX expression or licence files;
- a generated file has a binary/weight suffix, or the string `torchaudio`.

The committed tree is the **Linux reference output**. `--check` is a CI gate on
`ubuntu-24.04`. The generator is deterministic for a given lock and host; a
Windows checkout can differ only in wheel-vendored third-party files (notably
PyTorch's platform-specific notice set).

## Inputs

| Path | Purpose |
|---|---|
| `packaging/release.toml` | The only source of the repository, releases, and source-offer URLs; project licence, offer years, asset names, and paths. |
| `packaging/vendor.lock.json` | Pinned vendor versions, archives, SHA-256s, build lines, and `source_offer.sources`. |
| `packaging/vendor/SOURCES.md` | Human-readable vendor source record. |
| `packaging/licenses/native.toml` | FFmpeg, Rubber Band, libsndfile, Deno, yt-dlp/EJS role, PyTorch, NumPy, GCC/LLVM runtime, OpenBLAS, OpenSSL, WebView2, and NiceGUI frontend assets. |
| `packaging/licenses/python.toml` | Overrides for Windows-only, build-only, and no-licence-file wheels (and `sphn`, whose METADATA has no declaration). |
| `packaging/licenses/texts/` | Canonical SPDX texts and the two exception texts. Copied to `LICENSES/common/`; never edited by the generator. |
| `uv.lock` + `.venv/**/dist-info` | Every resolved package and version, plus each wheel's actual `licenses/`/`License-File` contents. |

## Outputs

```text
LICENSE                      # GPL-3.0-only, byte-identical to LICENSES/common/GPL-3.0-only.txt
THIRD_PARTY_NOTICES.md       # curated sections + principal package table + source offer
SOURCE_OFFER.md              # GPL-3.0 §6(b) written offer and every source archive
LICENSES/
  README.md                  # component index
  common/<SPDX>.txt          # canonical texts
  texts/<name>-<sha256:12>.txt   # deduplicated upstream texts
  python/DEPENDENCIES.md     # every uv.lock package: version, SPDX, source, files
  ffmpeg/{NOTICE.md,BUILDINFO.txt}
  rubberband/{NOTICE.md,BUILDINFO.txt}
  libsndfile/{NOTICE.md,BUILDINFO.txt}
  deno/NOTICE.md
  nicegui/frontend/{quasar,vue,...}.txt
  pytorch/, numpy/, gcc-runtime/, .../NOTICE.md
```

The pool under `LICENSES/texts/` is content-addressed: identical licence files
are stored once and every package row links the same file. No model weights,
`*.safetensors`, vendor executables, or shared libraries are ever written.

## Bundle contract for the build scripts

Tickets #20, #21, and #23 consume this tree. The licence tree is a **first-class
Bundle root** member, beside `_internal/` and `vendor/` — never duplicated
inside the frozen payload (ticket 10 §3, ADR 0010):

```text
StemForge-<ver>/
├── StemForge[.exe]
├── _internal/                 # PyInstaller payload
├── vendor/                    # FFmpeg, Deno, Rubber Band, libsndfile
├── LICENSES/                  # this generator's output
├── LICENSE
├── THIRD_PARTY_NOTICES.md
└── SOURCE_OFFER.md
```

Build scripts must:

1. run `uv sync --frozen` and `python scripts/generate_notices.py --check`
   before assembling the bundle;
2. copy `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `LICENSES/`
   to the Bundle root (the directory holding the launcher), not under
   `_internal/`;
3. keep `source_offer_contact`, `releases_url`, and the tag immutable once the
   release is published;
4. leave `BUILDINFO.txt` provenance in place; the release build appends the
   shipped `ffmpeg -buildconf`/`--version` output to the same component
   directory.

The AppImage places the same tree under `usr/share/doc/stemforge/` and the Inno
installer under `{app}`. `verify_bundle.py` (#20/#21) asserts all four entries
exist and that no weights appear.

## Adding or bumping a component

- **Python package:** update `uv.lock`, run `uv sync --frozen`, regenerate. If
  the wheel ships no licence declaration or file, add an `[[override]]` to
  `python.toml` with an SPDX expression, source URL, and `texts`.
- **Vendor binary:** update `packaging/vendor.lock.json` and `SOURCES.md` first
  (ticket 19), then bump `native.toml` only if the notice prose or build line
  changes. Versions come from the vendor lock.
- **Frontend asset:** add a `[[component.assets]]` row with `slug`, name,
  version, SPDX, copyright, URL, and `texts`.
- Then run the generator and commit the regenerated tree with the input change.
