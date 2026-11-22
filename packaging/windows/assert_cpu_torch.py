#!/usr/bin/env python3
"""Assert the build virtualenv resolved the CPU-only torch wheel.

A PyPI fallback silently pulls CUDA wheels. PyInstaller
would happily bundle them and blow the size budget, so the Windows build fails
closed before it runs.
"""

from __future__ import annotations

import importlib.util
import sys


def main() -> int:
    if importlib.util.find_spec("torch") is None:
        # The lightweight unit environment legitimately omits torch. The build
        # environment does not; build.ps1 installs the full lock first.
        print("torch is not installed; the release bundle needs it", file=sys.stderr)
        return 1
    import torch

    if not torch.__version__.endswith("+cpu"):
        print(
            f"build.ps1: torch {torch.__version__} is not a +cpu build; "
            "a PyPI fallback would ship CUDA wheels",
            file=sys.stderr,
        )
        return 1
    print(f"torch {torch.__version__} is the CPU build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
