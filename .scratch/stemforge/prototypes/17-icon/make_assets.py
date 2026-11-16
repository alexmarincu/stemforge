#!/usr/bin/env python3
"""Build the StemForge icon assets from the winning candidate (A — Badge).

Deliverables (written to src/stemforge/assets/):
  stemforge.svg        master, 256 grid, amber disc + ink bars
  stemforge.ico        16, 20, 24, 32, 40, 48, 64, 128, 256 px
  stemforge-512.png    AppImage / About raster
  stemforge-1024.png   high-DPI raster
  stemforge-mono.svg   currentColor fallback for trays/forced themes

Requires rsvg-convert and Pillow. Run from anywhere:
  python3 .scratch/stemforge/prototypes/17-icon/make_assets.py
"""
from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ASSETS = ROOT / "src" / "stemforge" / "assets"

MASTER = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="StemForge">
  <title>StemForge</title>
  <desc>Amber circular waveform mark; four bars for the four Demucs stems.</desc>
  <circle cx="128" cy="128" r="116" fill="#F2A65A"/>
  <g fill="#1D1204">
    <rect x="40" y="90" width="32" height="76" rx="16"/>
    <rect x="88" y="62" width="32" height="132" rx="16"/>
    <rect x="136" y="78" width="32" height="100" rx="16"/>
    <rect x="184" y="58" width="32" height="140" rx="16"/>
  </g>
</svg>
"""

MONO = """<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-label="StemForge">
  <title>StemForge (monochrome)</title>
  <desc>One-colour fallback: the badge silhouette with the four bars knocked out.</desc>
  <path fill="currentColor" fill-rule="evenodd" d="M128 12a116 116 0 1 0 0 232a116 116 0 0 0 0-232Z
    M56 90a16 16 0 0 0-16 16v44a16 16 0 0 0 32 0v-44a16 16 0 0 0-16-16Z
    M104 62a16 16 0 0 0-16 16v100a16 16 0 0 0 32 0v-100a16 16 0 0 0-16-16Z
    M152 78a16 16 0 0 0-16 16v68a16 16 0 0 0 32 0v-68a16 16 0 0 0-16-16Z
    M200 58a16 16 0 0 0-16 16v108a16 16 0 0 0 32 0v-108a16 16 0 0 0-16-16Z"/>
</svg>
"""

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_png(svg: str, size: int) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "icon.svg"
        dst = Path(td) / "icon.png"
        src.write_text(svg)
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(dst), str(src)],
            check=True,
            capture_output=True,
        )
        return dst.read_bytes()


def dib_entry(png: bytes) -> bytes:
    """32-bit bottom-up DIB with an AND mask, the traditional ICO payload."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "frame.png"
        path.write_bytes(png)
        img = Image.open(path).convert("RGBA")
    width, height = img.size
    pixels = img.load()

    xor = bytearray()
    for y in range(height - 1, -1, -1):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            xor += bytes((b, g, r, a))

    mask_row = ((width + 31) // 32) * 4
    mask = bytearray(mask_row * height)
    for y in range(height - 1, -1, -1):
        row = height - 1 - y
        for x in range(width):
            if pixels[x, y][3] == 0:
                mask[row * mask_row + (x // 8)] |= 0x80 >> (x % 8)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        len(xor) + len(mask),
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor) + bytes(mask)


def ico_bytes(frames: list[tuple[int, bytes]]) -> bytes:
    entries = []
    offset = 6 + 16 * len(frames)
    for size, payload in frames:
        dim = 0 if size == 256 else size
        entries.append(
            struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(payload), offset)
        )
        offset += len(payload)
    return struct.pack("<HHH", 0, 1, len(frames)) + b"".join(entries) + b"".join(p for _, p in frames)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "stemforge.svg").write_text(MASTER)
    (ASSETS / "stemforge-mono.svg").write_text(MONO)

    for size in (512, 1024):
        (ASSETS / f"stemforge-{size}.png").write_bytes(render_png(MASTER, size))

    frames: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        png = render_png(MASTER, size)
        frames.append((size, png if size == 256 else dib_entry(png)))
    ico = ico_bytes(frames)
    (ASSETS / "stemforge.ico").write_bytes(ico)

    # Verification: every requested ICO size must load, and the PNGs must be RGBA.
    with Image.open(ASSETS / "stemforge.ico") as im:
        assert set(im.info["sizes"]) == {(s, s) for s in ICO_SIZES}, im.info["sizes"]
    for size in (512, 1024):
        with Image.open(ASSETS / f"stemforge-{size}.png") as im:
            assert im.size == (size, size) and im.mode == "RGBA"

    # Mono render sanity: the path must produce visible pixels.
    mono = render_png(MONO, 64)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "mono.png"
        p.write_bytes(mono)
        with Image.open(p) as im:
            assert im.convert("RGBA").getbbox() is not None

    for path in sorted(ASSETS.iterdir()):
        print(f"{path.name:24} {path.stat().st_size:>9,} bytes")


if __name__ == "__main__":
    main()
