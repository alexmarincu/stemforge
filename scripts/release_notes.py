#!/usr/bin/env python3
"""Render the release notes used by ``release.yml``'s ``prepare`` job.

Inputs are the repository's single version source, ``uv.lock`` (the shipped
yt-dlp version), ``packaging/vendor.lock.json`` (the pinned native tools), and
``packaging/release.toml`` (the only URL source). The output deliberately names
the unsigned-build caveat, the source offer, and the manual QA checklist; a
release page missing those is a gate failure, not a style preference.
"""

from __future__ import annotations

import argparse
import ast
import sys
import tomllib
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def _version_file(root: Path) -> str:
    with (root / "packaging" / "release.toml").open("rb") as handle:
        config = tomllib.load(handle)
    return str(config["project"]["version_file"])


def read_version(root: Path) -> str:
    """Read ``__version__`` without importing the package."""
    source = root / _version_file(root)
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str) and value:
                        return value
    raise ValueError(f"{source} does not define __version__")


def package_version(root: Path, name: str) -> str:
    """Return the locked version of ``name`` from ``uv.lock``."""
    with (root / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)
    for package in lock.get("package", []):
        if isinstance(package, dict) and package.get("name") == name:
            return str(package["version"])
    raise ValueError(f"uv.lock has no package named {name}")


def _vendor_versions(root: Path) -> dict[str, str]:
    with (root / "packaging" / "vendor.lock.json").open(encoding="utf-8") as handle:
        import json

        lock = json.load(handle)
    return {
        name: str(record.get("version", ""))
        for name, record in lock.get("components", {}).items()
        if isinstance(record, dict)
    }


def render(root: Path) -> str:
    """Render the release notes for the version in ``root``."""
    version = read_version(root)
    yt_dlp = package_version(root, "yt-dlp")
    vendor = _vendor_versions(root)
    with (root / "packaging" / "release.toml").open("rb") as handle:
        release = tomllib.load(handle)
    repo = release["repo"]
    repo_url = str(repo["url"])
    releases_url = str(repo["releases_url"])
    tag = f"v{version}"
    source_archive = str(release["assets"]["source_archive"]).format(version=version)
    third_party = str(release["assets"]["third_party_sources_archive"]).format(version=version)

    lines = [
        f"# StemForge {version}",
        "",
        "Local stem separation, integer pitch shifting, preview, and export. No",
        "accounts, no cloud processing, no telemetry.",
        "",
        "## Downloads",
        "",
        "| Asset | Purpose |",
        "|---|---|",
        "| `StemForge-" + version + "-linux-x86_64.tar.gz` | Linux, canonical bundle |",
        "| `StemForge-" + version + "-linux-x86_64.tar.zst` | Same tree, smaller |",
        "| `StemForge-" + version + "-linux-x86_64.AppImage` | Thin host-dependent wrapper |",
        "| `StemForge-" + version + "-win64-portable.zip` | Windows, portable |",
        "| `StemForge-" + version + "-win64-setup.exe` | Windows, per-user installer |",
        "| `" + source_archive + "` | First-party source for this tag |",
        "| `" + third_party + "` | Third-party corresponding source |",
        "| `SHA256SUMS` | Integrity check for every asset above |",
        "",
        "## Shipped versions",
        "",
        f"- **yt-dlp {yt_dlp}** (pinned; YouTube changes can break import until a new",
        "  StemForge release — there is no runtime updater).",
        f"- FFmpeg {vendor.get('ffmpeg', 'see vendor.lock.json')} (GPL static; includes"
        " `ffprobe`).",
        f"- Rubber Band {vendor.get('rubberband', 'see vendor.lock.json')} and"
        f" libsndfile {vendor.get('libsndfile', 'see vendor.lock.json')}.",
        f"- Deno {vendor.get('deno', 'see vendor.lock.json')} for the pinned yt-dlp EJS runtime.",
        "- Demucs `htdemucs`: the CPU model weights are **not bundled**; the first",
        "  Separation downloads 84 MB at a pinned revision after an explicit consent",
        "  gate and verifies SHA-256.",
        "",
        "## Unsigned build",
        "",
        f"StemForge v{version} is **unsigned**. Windows SmartScreen may show",
        '"Windows protected your PC" or "Unknown publisher" the first time a',
        "download runs. That is expected for an unsigned build. Compare each file",
        "with `SHA256SUMS` from this release page before choosing **More info** and",
        "**Run anyway**. Never disable SmartScreen globally.",
        "",
        "Linux bundles are unsigned too. `SHA256SUMS` is the integrity check.",
        "",
        "## Verify and install",
        "",
        "```sh",
        "sha256sum -c SHA256SUMS",
        "```",
        "",
        "Linux needs GTK 3, WebKitGTK 4.1 (2.44+), and GStreamer good plugins for",
        "the native window and preview. `run.sh` falls back to browser mode when the",
        "native stack is missing; the tar.gz is the primary artifact. The AppImage",
        "is thin and host-dependent; use `--appimage-extract-and-run` on hosts without",
        "FUSE.",
        "",
        "## Manual QA and source offer",
        "",
        f"- Release checklist: {repo_url}/blob/{tag}/docs/qa/release-checklist.md",
        f"- Source offer: {repo_url}/blob/{tag}/SOURCE_OFFER.md",
        f"- Full source: {releases_url}/download/{tag}/{source_archive}",
        f"- Third-party source: {releases_url}/download/{tag}/{third_party}",
        "",
        "## Known limitations",
        "",
        "- 48 kHz sources are resampled to 44.1 kHz, including untouched exports;",
        "  multichannel input is downmixed.",
        "- Preview is MP3 192; there is no `HEAD` route and suffix or multi-range",
        "  requests return 416.",
        "- No loudness normalization, waveform display, stem mixer, or batch export.",
        "- One active Job: an export waits behind a Separation. The Workspace is",
        "  ephemeral; the cache and recent list survive.",
        "- The native window, native dialogs, and live YouTube import are manual QA.",
        "  CI covers preflight, the frozen server, and the frozen separation.",
        "- **Windows figures are unmeasured** in this effort (startup, render-to-first-",
        "  frame, and RSS); measuring them is hardening work, and this release does",
        "  not claim they meet the envelope.",
        "- The Linux AppImage is not self-contained. It is a thin wrapper over the",
        "  host GTK/WebKitGTK/GStreamer stack, not the primary artifact.",
        "- No auto-update and no version check. Patch releases are best effort; pinned",
        "  yt-dlp can lag YouTube changes.",
        "",
        f"Report issues at {repo['source_offer_contact']}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, help="write the notes here instead of stdout")
    args = parser.parse_args(argv)
    try:
        text = render(args.root)
    except (OSError, KeyError, ValueError) as error:
        print(f"release_notes: {error}", file=sys.stderr)
        return 1
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"release_notes: wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
