"""Measure input-to-paint latency for the native window.

Hovering a track row and clicking it both change pixels. The probe reports the
time from the warp or click until the change appears, which is the cost the user
feels; CPU samples miss it because live resize stretches the last frame rather
than repainting it.

Manual procedure: needs X11, the real window, and an unlocked session. Run with::

    uv run python -m scripts.measure_window_latency \\
        --condition default --size 1280x820

The row probe hovers and clicks the row body, not its play or remove controls: a
control hover measures a Quasar button, and at the default window size the rail's
overlay scrollbar can cover the row's right edge. The row positions are the first
two rows of the rail at the shipped 1280x820 layout; the rail's top is fixed and
the window is pinned to the root origin, so the same coordinates exist at every
size. The Workspace is session-only, so the probe drives the real import dialog
before it measures.

``--probe import`` times the fixed Import audio button instead. That probe skips
the click because the button opens the Import dialog.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final, NamedTuple

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

# Row 1 spans 203.75..255.25 and row 2 255.25..306.75 at the shipped layout;
# these are their centres, so the hover and click land inside each row's hit area.
ROW_CENTRES: Final = (230, 281)
# The row body between the icon tile and the play control. The row's right edge
# carries the play and remove buttons, and the rail's overlay scrollbar covers
# that edge at the default window size; hovering either measures a control.
ROW_HOVER_X: Final = 100
ROW_BACKGROUND_LEFT: Final = 9
ROW_BACKGROUND_WIDTH: Final = 4
ROW_BACKGROUND_HEIGHT: Final = 16
ROW_ICON_LEFT: Final = 16
ROW_ICON_SIZE: Final = 26
IMPORT_HOVER: Final = (146, 86)
# The rail's left padding is the one point inside the window that is clear of
# rows, controls, and the scroll area's content.
AWAY_X: Final = 4
# A blank view has no rows to hover and browser mode has no native window.
LATENCY_CONDITIONS: Final = ("default", "disable-dmabuf", "disable-dmabuf-only")
PROBES: Final = ("row", "import")
POLL_S: Final = 0.004
SETTLE_S: Final = 0.8
AWAY_S: Final = 1.5


class Rect(NamedTuple):
    """A page rectangle in window pixels."""

    left: int
    top: int
    width: int
    height: int


IMPORT_BACKGROUND: Final = Rect(230, 92, 20, 10)
# The detail title is the first line of the selected track's pane; sample the
# glyphs, not the banner above the pane.
DETAIL_TITLE: Final = Rect(330, 72, 260, 20)


def probe_areas(centre: int) -> dict[str, Rect]:
    """The row-background and row-icon patches the hover and click compare.

    The background patch sits in the row's left padding, clear of the icon; the
    icon patch covers the icon tile and carries the selection change the click
    looks for.
    """
    return {
        "background": Rect(
            ROW_BACKGROUND_LEFT,
            centre - ROW_BACKGROUND_HEIGHT // 2,
            ROW_BACKGROUND_WIDTH,
            ROW_BACKGROUND_HEIGHT,
        ),
        "icon": Rect(
            ROW_ICON_LEFT,
            centre - ROW_ICON_SIZE // 2,
            ROW_ICON_SIZE,
            ROW_ICON_SIZE,
        ),
    }


def away_point(size: tuple[int, int]) -> tuple[int, int]:
    """The pointer's resting point between trials, clear of rows and controls.

    A resting point over the detail pane can leave a button hover fading while
    the next row is measured, which is a full-window repaint of its own.
    """
    return (AWAY_X, size[1] // 2)


class _Probe:
    """The display connection and pixel helpers the two probes share."""

    def __init__(self, origin: tuple[int, int]) -> None:
        self._display = display.Display()
        self._root = self._display.screen().root
        self._origin = origin

    def close(self) -> None:
        self._display.close()

    def snapshot(self, area: Rect) -> bytes:
        image = self._root.get_image(
            self._origin[0] + area.left,
            self._origin[1] + area.top,
            area.width,
            area.height,
            X.ZPixmap,
            0xFFFFFFFF,
        )
        return image.data

    def warp(self, point: tuple[int, int]) -> None:
        self._root.warp_pointer(self._origin[0] + point[0], self._origin[1] + point[1])
        self._display.sync()

    def click(self) -> None:
        xtest.fake_input(self._display, X.ButtonPress, 1)
        xtest.fake_input(self._display, X.ButtonRelease, 1)
        self._display.sync()

    def wait_change(
        self,
        areas: dict[str, Rect],
        baseline: dict[str, bytes],
        *,
        timeout: float = 5.0,
    ) -> float | None:
        started = time.perf_counter()
        while True:
            for name, area in areas.items():
                if self.snapshot(area) != baseline[name]:
                    return (time.perf_counter() - started) * 1000
            if time.perf_counter() - started > timeout:
                return None
            time.sleep(POLL_S)


def probe(
    origin: tuple[int, int],
    size: tuple[int, int],
    *,
    trials: int,
) -> dict[str, list[float]]:
    """Time the hover and selection changes on two track rows."""
    live = _Probe(origin)
    try:
        rows = {row: probe_areas(centre) for row, centre in enumerate(ROW_CENTRES)}
        hover: list[float] = []
        click_times: list[float] = []
        for trial in range(trials):
            row = trial % len(ROW_CENTRES)
            centre = ROW_CENTRES[row]
            areas = rows[row]
            live.warp(away_point(size))
            time.sleep(AWAY_S)
            hover_base = live.snapshot(areas["background"])
            live.warp((ROW_HOVER_X, centre))
            took = live.wait_change({"row": areas["background"]}, {"row": hover_base})
            if took is not None:
                hover.append(took)
            time.sleep(SETTLE_S)
            icon_base = live.snapshot(areas["icon"])
            title_base = live.snapshot(DETAIL_TITLE)
            live.click()
            probes = {"icon": areas["icon"], "title": DETAIL_TITLE}
            baseline = {"icon": icon_base, "title": title_base}
            took = live.wait_change(probes, baseline)
            if took is not None:
                click_times.append(took)
            time.sleep(SETTLE_S)
        return {"hover_ms": hover, "click_ms": click_times}
    finally:
        live.close()


def probe_import(
    origin: tuple[int, int],
    size: tuple[int, int],
    *,
    trials: int,
) -> dict[str, list[float]]:
    """Time the fixed Import audio button's hover; a click would open its dialog."""
    live = _Probe(origin)
    try:
        hover: list[float] = []
        for _ in range(trials):
            live.warp(away_point(size))
            time.sleep(AWAY_S)
            baseline = live.snapshot(IMPORT_BACKGROUND)
            live.warp(IMPORT_HOVER)
            took = live.wait_change({"import": IMPORT_BACKGROUND}, {"import": baseline})
            if took is not None:
                hover.append(took)
            time.sleep(SETTLE_S)
        return {"hover_ms": hover, "click_ms": []}
    finally:
        live.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--condition", choices=LATENCY_CONDITIONS, default="default")
    parser.add_argument("--size", default="1280x820", help="WxH, default, or maximized")
    parser.add_argument("--probe", choices=PROBES, default="row")
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
        if args.probe == "import":
            measured = probe_import(origin, size, trials=args.trials)
        else:
            measured = probe(origin, size, trials=args.trials)
    finally:
        stop(process)

    result = {
        "condition": args.condition,
        "size": f"{size[0]}x{size[1]}",
        "requested": list(size),
        "probe": args.probe,
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
