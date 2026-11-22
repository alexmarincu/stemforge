#!/usr/bin/env python3
"""Write and verify ``SHA256SUMS`` for the Windows release artifacts.

The portable ZIP and the Inno installer ship unsigned, so the hash list is the
integrity check. ``packaging/windows/package.ps1`` calls this script;
``verify-release.sh`` and the manual release matrix use ``--check``.

Format: the GNU coreutils form, ``<lowercase hex>  <basename>``, sorted by name.
Only basenames are written, so the list verifies from the download directory on
any OS.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

_LINE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<name>[^/\\]+)$")


def digest(path: Path) -> str:
    """Return the lowercase SHA-256 of ``path``."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def render(paths: Iterable[Path]) -> str:
    """Render a manifest for ``paths``; duplicate names are rejected."""
    entries: dict[str, Path] = {}
    for path in paths:
        path = Path(path)
        if path.name in entries:
            raise ValueError(f"duplicate artifact name: {path.name}")
        entries[path.name] = path
    lines = [f"{digest(entries[name])}  {name}" for name in sorted(entries)]
    return "\n".join(lines) + ("\n" if lines else "")


def write_manifest(paths: Iterable[Path], output: Path) -> None:
    """Write the manifest to ``output``."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(paths), encoding="utf-8")


def parse_manifest(text: str) -> list[tuple[str, str]]:
    """Parse manifest text into ``(digest, name)`` rows, rejecting bad lines."""
    rows: list[tuple[str, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        match = _LINE.match(line)
        if match is None:
            raise ValueError(f"line {number} is not '<sha256>  <basename>': {raw!r}")
        rows.append((match.group("digest"), match.group("name")))
    if not rows:
        raise ValueError("the manifest is empty")
    return rows


def verify_manifest(manifest: Path) -> list[str]:
    """Return one message per mismatch; an empty list means the list is good."""
    manifest = Path(manifest)
    try:
        rows = parse_manifest(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"{manifest}: {error}"]
    problems: list[str] = []
    for expected, name in rows:
        target = manifest.parent / name
        if not target.is_file():
            problems.append(f"missing: {name}")
            continue
        actual = digest(target)
        if actual != expected:
            problems.append(f"SHA-256 mismatch: {name} (expected {expected}, got {actual})")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write SHA256SUMS here")
    parser.add_argument("--check", type=Path, help="verify the SHA256SUMS file at this path")
    parser.add_argument("files", nargs="*", type=Path)
    args = parser.parse_args(argv)

    if args.check is not None:
        problems = verify_manifest(args.check)
        for problem in problems:
            print(f"sha256sums: {problem}")
        if problems:
            return 1
        print(f"sha256sums: {args.check} verified")
        return 0

    if args.output is None:
        parser.error("--output is required when not checking")
    if not args.files:
        parser.error("at least one artifact is required")
    try:
        write_manifest(args.files, args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"sha256sums: wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
