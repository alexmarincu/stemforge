#!/usr/bin/env python3
"""Bump StemForge's single version source and regenerate the licence tree.

``src/stemforge/__init__.py`` is the only place the version is written; the
packaging metadata reads it through ``[tool.hatch.version]``. This script
rewrites that one assignment, regenerates the notices, and prints the remaining
manual steps. Stdlib only, so it runs before ``uv sync``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_LINE = re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE)
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def _release_settings(root: Path) -> tuple[Path, int]:
    """Return the module that holds ``__version__`` and the offer's first year."""
    with (root / "packaging/release.toml").open("rb") as handle:
        data = tomllib.load(handle)
    version_file = root / str(data["project"]["version_file"])
    start_year = int(data["source_offer"]["start_year"])
    return version_file, start_year


def _rewrite_version(version_file: Path, version: str) -> bool:
    """Replace the one ``__version__`` assignment; false when there is not one."""
    text = version_file.read_text(encoding="utf-8")
    if len(VERSION_LINE.findall(text)) != 1:
        return False
    version_file.write_text(
        VERSION_LINE.sub(f'__version__ = "{version}"', text, count=1),
        encoding="utf-8",
    )
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="new version, for example 0.2.0")
    args = parser.parse_args(argv)

    version = str(args.version)
    if SEMVER.fullmatch(version) is None:
        print(f"bump_version: {version!r} is not MAJOR.MINOR.PATCH", file=sys.stderr)
        return 2

    version_file, start_year = _release_settings(REPO_ROOT)
    relative = version_file.relative_to(REPO_ROOT).as_posix()
    if not _rewrite_version(version_file, version):
        print(
            f'bump_version: expected exactly one `__version__ = "..."` line in {relative}',
            file=sys.stderr,
        )
        return 1

    try:
        subprocess.run(
            [sys.executable, "scripts/generate_notices.py"],
            cwd=REPO_ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        print(f"bump_version: generate_notices.py failed ({error.returncode})", file=sys.stderr)
        return error.returncode or 1

    year = datetime.now().year
    if start_year != year:
        print(
            "bump_version: packaging/release.toml's source_offer.start_year is "
            f"{start_year}, but the current year is {year}; the GPL-3.0 written offer "
            "may need updating.",
            file=sys.stderr,
        )

    print(f"bumped the version to {version}")
    print("next steps:")
    print("  git diff                  # review the version and the generated notices")
    print("  scripts/check.sh          # full local gate")
    print(f"  git tag v{version}        # once the release commit is on main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
