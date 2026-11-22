# Vendor lock format

[`../vendor.lock.json`](../vendor.lock.json) is the machine-readable pin list for
the vendor set. The bundle assembly and the licence/source generator consume it; `scripts/vendor-fetch.sh` and
`scripts/vendor-fetch.ps1` are its only writers.

## Top level

| Field | Meaning |
|---|---|
| `schema` | `1`. Bump when a required field changes. |
| `vendor_root` | Always `"vendor"`; the directory beside `_internal/` (ADR 0010). |
| `targets` | `linux-x86_64` and `windows-x64`, each with `system` and `arch`. |
| `components` | One record per installed component: `ffmpeg`, `ffprobe`, `deno`, `libsndfile`, `rubberband`. |
| `source_offer` | Written-offer metadata and the archive inputs for the source offer. |

## Component record

| Field | Meaning |
|---|---|
| `version` | The substring `preflight` matches against the tool's version output. FFmpeg uses `n9.0.2-57-gd838f9f9ae`; Rubber Band uses `4.0.0`; Deno `2.9.8`; libsndfile records `1.2.2` without a runtime probe. |
| `version_flag` | `-version` for FFmpeg/ffprobe, `--version` for Rubber Band/Deno, `null` for a shared library. |
| `license` | SPDX expression of the shipped build. |
| `license_texts` | Files the licence generator places under `LICENSES/<component>/`. |
| `provenance` | Project, homepage, build project/tag/commit and the `-buildconf` note. |
| `targets` | One entry per key in the top-level `targets`. |

## Target entry

| Field | Meaning |
|---|---|
| `kind` | `archive` (extract a pinned download) or `built` (compile a pinned source). |
| `url`, `filename`, `size`, `sha256` | The pinned download. For `kind: "built"` these live under `source`. |
| `archive_format` | `tar.xz`, `tar.gz`, or `zip`. For a built component it is under `build`. |
| `build` | `system`, `archive_format`, the `configure` argv (with `<source>`/`<prefix>` placeholders), `post_build`, and `notes`. Required for `built`. |
| `install` | Files to place under `vendor/`: `from` (archive member or build output), `to` (vendor-relative path), `executable`, and an optional extracted-file `sha256`. |

## Rules the unit test enforces

- Every component has both targets and a valid 64-character lowercase SHA-256
  for each target's downloaded artifact.
- Every installed path is relative, has no `..`, and starts with `ffmpeg`,
  `deno`, or `rubberband`.
- The installed names match `platform.binaries.vendor_path()` and
  `platform.preflight.shared_library_name()`: `ffmpeg/ffmpeg`,
  `ffmpeg/ffprobe`, `rubberband/rubberband`, `deno/deno`, plus
  `rubberband/libsndfile.so.1` or `rubberband/sndfile.dll`.
- No weights, no `.safetensors`/`.pt`/`.pth`/`.onnx` entry, and no Hugging Face
  URL.
- `source_offer.sources` covers every archive input and the written offer names
  the three-year term and the issue-tracker contact.

## Adding or bumping a component

1. Update the pin and its target entries here.
2. Update `SOURCES.md` with the new archive, size, SHA-256, and licence.
3. Run `scripts/vendor-fetch.sh --force` (Linux) and
   `scripts/vendor-fetch.ps1 -Force` (Windows; it builds Rubber Band and
   libsndfile from these sources with MSVC/CMake/Meson, or accepts a pinned pair
   via `-RubberBandPrebuilt`).
4. Run `uv run pytest tests/unit/test_vendor_lock.py
   tests/integration/test_vendor_binaries.py -m "unit or (integration and slow)"`.
5. `scripts/generate_notices.py --check` must stay green (see
   [`../licenses/README.md`](../licenses/README.md)).
