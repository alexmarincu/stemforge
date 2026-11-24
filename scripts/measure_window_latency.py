"""Measure input-to-paint latency for the native window.

Hovering a track row and clicking it both change pixels. The probe reports the
time from the warp or click until the change appears, which is the cost the user
feels; CPU samples miss it because live resize stretches the last frame rather
than repainting it.

Manual procedure: needs X11, the real window, and an unlocked session. Run with::

    uv run python -m scripts.measure_window_latency \\
        --condition default --size 1280x820

The row positions are the first two rows of the rail at the shipped 1280x820
layout; the rail's top is fixed and the window is pinned to the root origin, so
the same coordinates exist at every size. The Workspace is session-only, so the
probe drives the real import dialog before it measures.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from Xlib import X, display
from Xlib.ext import xtest

from scripts.measure_window_rendering import (
    DEFAULT_WINDOW_SIZE,
    MeasurementError,
    launch,
    parse_size,
    place_window,
    screen_size,
    seed_session,
    session_locked,
    stop,
    wait_for_window,
    window_origin,
)

ROW_CENTRES: Final = (240, 299)
# A blank view has no rows to hover and browser mode has no native window.
LATENCY_CONDITIONS: Final = ("default", "disable-dmabuf", "disable-dmabuf-only")
POLL_S: Final = 0.004
SETTLE_S: Final = 0.8
AWAY_S: Final = 1.5


def probe(
    origin: tuple[int, int],
    size: tuple[int, int],
    *,
    trials: int,
) -> dict[str, list[float]]:
    """Time the hover and selection changes on two track rows."""
    display_connection = display.Display()
    root = display_connection.screen().root
    offset_x, offset_y = origin

    def snapshot(x: int, y: int, width: int = 20, height: int = 16) -> bytes:
        image = root.get_image(offset_x + x, offset_y + y, width, height, X.ZPixmap, 0xFFFFFFFF)
        return image.data

    def warp(x: int, y: int) -> None:
        root.warp_pointer(offset_x + x, offset_y + y)
        display_connection.sync()

    def click() -> None:
        xtest.fake_input(display_connection, X.ButtonPress, 1)
        xtest.fake_input(display_connection, X.ButtonRelease, 1)
        display_connection.sync()

    def wait_change(
        probes: dict[str, tuple[int, int, int, int]],
        baseline: dict[str, bytes],
        *,
        timeout: float = 5.0,
    ) -> float | None:
        started = time.perf_counter()
        while True:
            for name, area in probes.items():
                if snapshot(*area) != baseline[name]:
                    return (time.perf_counter() - started) * 1000
            if time.perf_counter() - started > timeout:
                return None
            time.sleep(POLL_S)

    backgrounds = {row: (255, centre - 8, 20, 16) for row, centre in enumerate(ROW_CENTRES)}
    icons = {row: (14, centre - 10, 20, 20) for row, centre in enumerate(ROW_CENTRES)}
    title = (330, 8, 260, 34)

    hover: list[float] = []
    click_times: list[float] = []
    for trial in range(trials):
        row = trial % len(ROW_CENTRES)
        centre = ROW_CENTRES[row]
        warp(size[0] // 2, size[1] // 2)
        time.sleep(AWAY_S)
        hover_base = snapshot(*backgrounds[row])
        warp(270, centre)
        took = wait_change({f"row{row}": backgrounds[row]}, {f"row{row}": hover_base})
        if took is not None:
            hover.append(took)
        time.sleep(SETTLE_S)
        icon_base = snapshot(*icons[row])
        title_base = snapshot(*title)
        click()
        probes = {f"icon{row}": icons[row], "title": title}
        baseline = {f"icon{row}": icon_base, "title": title_base}
        took = wait_change(probes, baseline)
        if took is not None:
            click_times.append(took)
        time.sleep(SETTLE_S)
    display_connection.close()
    return {"hover_ms": hover, "click_ms": click_times}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--condition", choices=LATENCY_CONDITIONS, default="default")
    parser.add_argument("--size", default="1280x820", help="WxH, default, or maximized")
    parser.add_argument("--trials", type=int, default=8)
    parser.add_argument("--tracks", type=int, default=8)
    parser.add_argument("--home", type=Path, default=Path("/tmp/stemforge-window-latency"))
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if session_locked():
        raise MeasurementError("the session is locked; unlock before measuring")
    size = parse_size(args.size, default=DEFAULT_WINDOW_SIZE, screen=screen_size())
    args.home.mkdir(parents=True, exist_ok=True)
    process = launch(args.home, args.condition, args.home / "latency.log")
    try:
        window = wait_for_window(process.pid, timeout=120)
        place_window(window, size, settle=2.5)
        imported, problem = seed_session(window, args.home, args.tracks)
        time.sleep(12)
        origin = window_origin(window)
        measured = probe(origin, size, trials=args.trials)
    finally:
        stop(process)

    result = {
        "condition": args.condition,
        "size": f"{size[0]}x{size[1]}",
        "requested": list(size),
        "seeded": f"{imported}/{args.tracks}",
        "problem": problem,
        "hover_ms": [round(value, 1) for value in measured["hover_ms"]],
        "click_ms": [round(value, 1) for value in measured["click_ms"]],
        "hover_median_ms": (
            round(statistics.median(measured["hover_ms"]), 1) if measured["hover_ms"] else None
        ),
        "click_median_ms": (
            round(statistics.median(measured["click_ms"]), 1) if measured["click_ms"] else None
        ),
    }
    print(json.dumps(result, indent=2))
    if args.json is not None:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeasurementError as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
