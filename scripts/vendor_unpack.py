#!/usr/bin/env python3
"""Safely unpack a pinned vendor source archive.

The Windows vendor script used ``tar.exe``, which is a different bsdtar build on
each runner image and stalled on ``windows-2022``. Python's stdlib ``tarfile``
(with ``lzma``) is the same on every runner, rejects path escapes, and lets the
release job stream progress around it.

Usage:
    python scripts/vendor_unpack.py --archive FILE --destination DIR --strip 1
"""

from __future__ import annotations

import argparse
import sys
import tarfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath


def _safe_parts(name: str) -> tuple[str, ...]:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or "\\" in name:
        raise ValueError(f"unsafe archive path: {name!r}")
    return pure.parts


def unpack(archive: Path, destination: Path, *, strip: int = 0) -> int:
    """Extract ``archive`` under ``destination`` and return the file count."""
    if strip < 0:
        raise ValueError("--strip must be non-negative")
    destination.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        with tarfile.open(archive, "r:*") as handle:
            members: list[tarfile.TarInfo] = []
            for info in handle.getmembers():
                parts = _safe_parts(info.name)
                if len(parts) <= strip:
                    continue
                info.name = "/".join(parts[strip:])
                members.append(info)
            handle.extractall(destination, members=members, filter="data")
            count = sum(1 for item in members if item.isfile())
    except (tarfile.TarError, OSError, ValueError) as error:
        raise ValueError(f"cannot extract {archive}: {error}") from error
    if count == 0:
        raise ValueError(f"{archive} contained no regular files after stripping")
    return count


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--strip", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        count = unpack(args.archive, args.destination, strip=args.strip)
    except ValueError as error:
        print(f"vendor_unpack: {error}", file=sys.stderr)
        return 1
    print(f"vendor_unpack: extracted {count} files from {args.archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
