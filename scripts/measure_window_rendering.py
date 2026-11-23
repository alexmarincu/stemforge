#!/usr/bin/env python3
"""Measure native-window rendering cost on a live X11/WebKitGTK session.

This is a manual procedure, not a test: it opens the real window, drives it with
X11 input, and samples the processes and the GPU.

One cell is one launch at one size under one renderer condition. The numbers
are percent of one CPU core, so a value above 100 means more than one core.

Example::

    uv run python scripts/measure_window_rendering.py \\
        --size 1280x820 --condition default

The full matrix is the default. Screenshots are classified by the fraction of
pixels at or above a near-white floor; the page background is dark, so a
rendered window stays far below the floor while a blank WebKit view is pure
white.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from stemforge.integration.window import DEFAULT_WINDOW_SIZE, WEBKIT_SHM_ENV
from stemforge.ui.formatting import APP_NAME

TITLE: Final = APP_NAME
DEFAULT_SIZES: Final = ("default", "1920x1080", "maximized")
CONDITIONS: Final[dict[str, dict[str, str]]] = {
    # configure_window sets the workaround itself when the variable is absent.
    "default": {},
    # An explicit value replaces the default through setdefault; zero switches
    # the renderer back to the GBM path.
    "shm-0": {WEBKIT_SHM_ENV: "0"},
    "disable-dmabuf": {"WEBKIT_DISABLE_DMABUF_RENDERER": "1"},
    # What a default that stops forcing shared memory would leave behind: the
    # DMABUF renderer off and no FORCE_SHM value of its own.
    "disable-dmabuf-only": {
        WEBKIT_SHM_ENV: "0",
        "WEBKIT_DISABLE_DMABUF_RENDERER": "1",
    },
    # BROWSER points webbrowser at a no-op so the control window is the one the
    # harness sizes, not a tab in the maintainer's session.
    "browser": {"STEMFORGE_BROWSER": "1", "BROWSER": "/bin/true"},
}
CONDITION_ORDER: Final = (
    "default",
    "shm-0",
    "disable-dmabuf",
    "disable-dmabuf-only",
    "browser",
)
# A blank view cannot show the import button, and browser mode has no native
# dialog, so only the native conditions that render can be populated.
SEEDED_CONDITIONS: Final = frozenset({"default", "disable-dmabuf", "disable-dmabuf-only"})
BROWSER_BINARIES: Final = (
    "chromium",
    "chromium-browser",
    "brave",
    "google-chrome-stable",
    "google-chrome",
    "microsoft-edge-stable",
)
BROWSER_URL: Final = "http://127.0.0.1:8080"
LOCKERS: Final = ("i3lock", "swaylock", "xsecurelock", "xfce4-screensaver")
WHITE_FLOOR: Final = 240
WHITE_FRACTION: Final = 0.95
CLK_TCK: Final = os.sysconf("SC_CLK_TCK")
SEED_SECONDS: Final = 1.0
SEED_RATE: Final = 44100


class MeasurementError(RuntimeError):
    """The host cannot produce a trustworthy cell."""


@dataclass(frozen=True)
class Gpu:
    util_percent: float | None
    used_mib: float | None
    samples: int


@dataclass(frozen=True)
class Capture:
    width: int
    height: int
    white_fraction: float
    white: bool


@dataclass(frozen=True)
class Cell:
    condition: str
    size: str
    requested: tuple[int, int]
    achieved: tuple[int, int] | None
    idle_cpu: dict[str, float]
    scroll_cpu: dict[str, float]
    resize_cpu: dict[str, float]
    gpu_idle: Gpu
    gpu_scroll: Gpu
    capture: Capture | None
    screenshot: str | None
    note: str


@dataclass(frozen=True)
class Host:
    platform: str
    screen: tuple[int, int]
    window_manager: str
    compositor: str
    webkitgtk: str
    gpu: str
    driver: str
    python: str


# -- pure helpers -----------------------------------------------------------


def parse_size(text: str, *, default: tuple[int, int], screen: tuple[int, int]) -> tuple[int, int]:
    """Resolve a size keyword or ``WxH`` against the live screen."""
    if text == "default":
        return default
    if text == "maximized":
        return screen
    match = re.fullmatch(r"(\d+)x(\d+)", text)
    if match is None:
        raise MeasurementError(f"size must be WxH, default, or maximized: {text!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise MeasurementError(f"size must be positive: {text!r}")
    return width, height


def parse_geometry(text: str) -> dict[str, int]:
    """Read the ``--shell`` output of ``xdotool getwindowgeometry``."""
    geometry: dict[str, int] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in {"WINDOW", "X", "Y", "WIDTH", "HEIGHT", "SCREEN"} and re.fullmatch(
            r"-?\d+", value.strip()
        ):
            geometry[key] = int(value.strip())
    if "WIDTH" not in geometry or "HEIGHT" not in geometry:
        raise MeasurementError(f"no window geometry in {text!r}")
    return geometry


def parse_proc_stat(text: str) -> tuple[int, int]:
    """Return the user and system tick counts from ``/proc/<pid>/stat``.

    The command name sits in parentheses and may itself contain spaces and
    parentheses, so the fixed-width prefix cannot be used.
    """
    end = text.rindex(")")
    fields = text[end + 1 :].split()
    return int(fields[11]), int(fields[12])


def cpu_percent(before: tuple[int, int], after: tuple[int, int], seconds: float) -> float:
    """Convert a tick delta to percent of one core.

    A process that restarts mid-window resets its counters; clamping keeps that
    from turning into a negative cost.
    """
    if seconds <= 0:
        raise MeasurementError("cpu window must be positive")
    delta = sum(after) - sum(before)
    return max(delta, 0) / CLK_TCK / seconds * 100.0


def parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    """Parse a binary P6 PPM at 8 bits per channel."""
    fields: list[bytes] = []
    offset = 0
    while len(fields) < 4:
        while offset < len(data) and data[offset : offset + 1].isspace():
            offset += 1
        if offset < len(data) and data[offset : offset + 1] == b"#":
            offset = data.index(b"\n", offset) + 1
            continue
        start = offset
        while offset < len(data) and not data[offset : offset + 1].isspace():
            offset += 1
        fields.append(data[start:offset])
    if fields[0] != b"P6":
        raise MeasurementError("screenshot is not a binary PPM")
    if fields[3] != b"255":
        raise MeasurementError("screenshot is not 8 bits per channel")
    if not data[offset : offset + 1].isspace():
        raise MeasurementError("screenshot header has no raster separator")
    offset += 1
    width, height = int(fields[1]), int(fields[2])
    pixels = data[offset:]
    if len(pixels) < width * height * 3:
        raise MeasurementError("screenshot is truncated")
    return width, height, pixels[: width * height * 3]


def white_fraction(rgb: bytes, *, floor: int = WHITE_FLOOR) -> float:
    """The share of pixels whose three channels all clear the white floor."""
    if len(rgb) % 3:
        raise MeasurementError("pixel buffer is not RGB")
    total = len(rgb) // 3
    if total == 0:
        raise MeasurementError("pixel buffer is empty")
    white = 0
    for index in range(0, len(rgb), 3):
        if rgb[index] >= floor and rgb[index + 1] >= floor and rgb[index + 2] >= floor:
            white += 1
    return white / total


def classify_ppm(data: bytes) -> Capture:
    """Classify a screenshot as the blank web view or a rendered page."""
    width, height, rgb = parse_ppm(data)
    fraction = white_fraction(rgb)
    return Capture(
        width=width,
        height=height,
        white_fraction=fraction,
        white=fraction >= WHITE_FRACTION,
    )


def parse_nvidia_smi(text: str) -> list[tuple[float, float]]:
    """Read ``utilization.gpu,memory.used`` rows in percent and MiB."""
    samples: list[tuple[float, float]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            samples.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return samples


def summarize_gpu(samples: Sequence[tuple[float, float]]) -> Gpu:
    if not samples:
        return Gpu(util_percent=None, used_mib=None, samples=0)
    return Gpu(
        util_percent=statistics.fmean(util for util, _ in samples),
        used_mib=statistics.fmean(used for _, used in samples),
        samples=len(samples),
    )


def format_cell_row(cell: Cell) -> str:
    """One matrix row; the columns stay stable so the cells are comparable."""
    asked = f"{cell.requested[0]}x{cell.requested[1]}"
    got = "n/a" if cell.achieved is None else f"{cell.achieved[0]}x{cell.achieved[1]}"
    white = "n/a" if cell.capture is None else ("yes" if cell.capture.white else "no")
    return (
        "| "
        + " | ".join(
            [
                cell.condition,
                cell.size,
                asked,
                got,
                _percent(cell.idle_cpu.get("stemforge")),
                _renderer_percent(cell.idle_cpu),
                _percent(cell.scroll_cpu.get("stemforge")),
                _renderer_percent(cell.scroll_cpu),
                _renderer_percent(cell.resize_cpu),
                _percent(cell.gpu_idle.util_percent),
                _mib(cell.gpu_idle.used_mib),
                white,
            ]
        )
        + " |"
    )


def matrix_header() -> str:
    return (
        "| Condition | Size | Asked | Got | Idle app % | Idle renderer % | "
        "Scroll app % | Scroll renderer % | Resize renderer % | GPU idle % | "
        "VRAM idle MiB | White |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def _mib(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def _renderer_percent(values: dict[str, float]) -> str:
    """WebKitWebProcess natively; the browser tree in the control arm."""
    for name in ("webkit", "browser"):
        if name in values:
            return _percent(values[name])
    return "n/a"


# -- process and X11 plumbing ----------------------------------------------


def run(argv: Sequence[str], *, timeout: float = 30.0) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise MeasurementError(f"{argv[0]} failed: {detail}")
    return result.stdout


def try_run(argv: Sequence[str], *, timeout: float = 30.0) -> str | None:
    try:
        return run(argv, timeout=timeout)
    except (MeasurementError, OSError, subprocess.TimeoutExpired):
        return None


def children_map() -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = re.search(r"^PPid:\s+(\d+)", status, re.MULTILINE)
        if match is not None:
            children.setdefault(int(match.group(1)), []).append(int(entry.name))
    return children


def descendants(root: int) -> set[int]:
    children = children_map()
    found: set[int] = set()
    stack = [root]
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def proc_ticks(pid: int) -> tuple[int, int]:
    try:
        return parse_proc_stat(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8"))
    except (OSError, IndexError, ValueError):
        return (0, 0)


def pids_by_marker(root: int, marker: str) -> list[int]:
    matches: list[int] = []
    for pid in descendants(root):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() in cmdline:
            matches.append(pid)
    return matches


def session_locked() -> bool:
    """A screen locker covers the window and receives synthetic input itself."""
    return any(try_run(["pgrep", "-x", locker]) is not None for locker in LOCKERS)


def screen_size() -> tuple[int, int]:
    """The root window covers every monitor, which is what "maximized" means here."""
    output = try_run(["xwininfo", "-root", "-stats"])
    if output is not None:
        width = re.search(r"^\s*Width:\s*(\d+)", output, re.MULTILINE)
        height = re.search(r"^\s*Height:\s*(\d+)", output, re.MULTILINE)
        if width is not None and height is not None:
            return int(width.group(1)), int(height.group(1))
    fallback = try_run(["xdotool", "getdisplaygeometry"])
    if fallback is None:
        raise MeasurementError("cannot read the X screen size")
    width_text, height_text = fallback.split()
    return int(width_text), int(height_text)


def window_manager() -> str:
    output = try_run(["wmctrl", "-m"])
    if output is None:
        return "unknown"
    for line in output.splitlines():
        if line.lower().startswith("name:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def compositor() -> str:
    for name in ("picom", "compton", "xcompmgr"):
        if shutil.which(name) and try_run(["pgrep", "-x", name]) is not None:
            return name
    return "none"


def webkitgtk_version() -> str:
    code = (
        "import gi;"
        "gi.require_version('WebKit2','4.1');"
        "from gi.repository import WebKit2;"
        "print(f'{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}')"
    )
    output = try_run([sys.executable, "-c", code], timeout=30)
    return "unknown" if output is None else output.strip()


def gpu_identity() -> tuple[str, str]:
    output = try_run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
    )
    if output is None or not output.strip():
        return ("unknown", "unknown")
    name, _, driver = output.strip().partition(",")
    return (name.strip(), driver.strip())


def wait_for_window(root: int, *, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = try_run(["xdotool", "search", "--name", f"^{re.escape(TITLE)}$"])
        if output:
            live = descendants(root) | {root}
            for candidate in output.split():
                pid_text = try_run(["xdotool", "getwindowpid", candidate])
                if pid_text is not None and int(pid_text.strip()) in live:
                    return int(candidate)
        time.sleep(0.25)
    raise MeasurementError(f"no {TITLE} window appeared within {timeout:.0f}s")


def float_window(window: int) -> None:
    """Leave bspwm's tiling before resizing; a tiled window ignores the size."""
    if shutil.which("bspc") and try_run(["bspc", "query", "-T", "-n", str(window)]):
        # A window can map on a desktop that is no longer focused while the app
        # starts, and an unmapped window receives no input.
        try_run(["bspc", "node", str(window), "-d", "focused"])
        try_run(["bspc", "node", str(window), "-t", "floating"])


def window_origin(window: int) -> tuple[int, int]:
    """A maximised window can sit at a negative origin from the WM border."""
    geometry = try_run(["xdotool", "getwindowgeometry", "--shell", str(window)])
    if geometry is None:
        return (0, 0)
    parsed = parse_geometry(geometry)
    return (parsed.get("X", 0), parsed.get("Y", 0))


def place_window(window: int, size: tuple[int, int], *, settle: float) -> tuple[int, int] | None:
    float_window(window)
    try_run(["xdotool", "windowactivate", "--sync", str(window)])
    try_run(["xdotool", "windowraise", str(window)])
    # Pin the top-left corner so every requested size starts at the same root
    # coordinates; the rail probe then reads the same place at every size.
    try_run(["xdotool", "windowmove", str(window), "0", "0"])
    if try_run(["xdotool", "windowsize", str(window), str(size[0]), str(size[1])]) is None:
        return None
    time.sleep(settle)
    geometry = try_run(["xdotool", "getwindowgeometry", "--shell", str(window)])
    if geometry is None:
        return None
    parsed = parse_geometry(geometry)
    return (parsed["WIDTH"], parsed["HEIGHT"])


# -- workloads --------------------------------------------------------------


class GpuSampler(threading.Thread):
    """Sample the whole-GPU counters while a workload runs.

    ``nvidia-smi`` reports the device, not the process, so a busy desktop adds a
    constant offset; the same offset applies to every cell.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[tuple[float, float]] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            output = try_run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                timeout=10,
            )
            if output:
                self.samples.extend(parse_nvidia_smi(output))
            self._stop_event.wait(0.25)

    def stop(self) -> Gpu:
        self._stop_event.set()
        self.join(timeout=10)
        return summarize_gpu(self.samples)


def cpu_over(pids: dict[str, list[int]], action: Callable[[], None]) -> dict[str, float]:
    before = {name: [proc_ticks(pid) for pid in ids] for name, ids in pids.items()}
    started = time.monotonic()
    action()
    seconds = time.monotonic() - started
    return {
        name: round(
            sum(
                cpu_percent(before[name][index], proc_ticks(pid), seconds)
                for index, pid in enumerate(ids)
            ),
            1,
        )
        if ids
        else 0.0
        for name, ids in pids.items()
    }


def sample(pids: dict[str, list[int]], action: Callable[[], None]) -> tuple[dict[str, float], Gpu]:
    """Measure process CPU and whole-GPU counters over one workload."""
    sampler = GpuSampler()
    sampler.start()
    try:
        cpu = cpu_over(pids, action)
    finally:
        gpu = sampler.stop()
    return cpu, gpu


def baseline_gpu(seconds: float = 3.0) -> Gpu:
    """The desktop's own GPU load, so a cell's numbers can be read as a delta."""
    sampler = GpuSampler()
    sampler.start()
    try:
        time.sleep(seconds)
    finally:
        baseline = sampler.stop()
    return baseline


def scroll_burst(window: int, width: int, height: int, events: int) -> None:
    """Alternate wheel down and up over the rail so the list keeps moving."""
    x = 140 if width >= 1150 else 100
    y = int(height * 0.55)
    origin = window_origin(window)
    try_run(["xdotool", "mousemove", str(origin[0] + x), str(origin[1] + y)])
    per_direction = max(1, events // 10)
    command: list[str] = ["xdotool"]
    for _ in range(5):
        command += ["click", "--repeat", str(per_direction), "--delay", "8", "5"]
        command += ["click", "--repeat", str(per_direction), "--delay", "8", "4"]
    try_run(command, timeout=120)


def resize_burst(window: int, width: int, height: int, steps: int = 24) -> None:
    command: list[str] = ["xdotool"]
    for index in range(steps):
        shrink = (160, 120) if index % 2 else (0, 0)
        command += [
            "windowsize",
            str(window),
            str(width - shrink[0]),
            str(height - shrink[1]),
            "sleep",
            "0.12",
        ]
    try_run(command, timeout=120)


def capture(window: int, path: Path) -> Capture:
    try_run(["import", "-window", str(window), "-depth", "8", str(path)], timeout=120)
    if not path.is_file():
        raise MeasurementError("screenshot capture produced no file")
    return classify_ppm(path.read_bytes())


# -- seeding and launching --------------------------------------------------


def seed_session(window: int, home: Path, count: int) -> tuple[int, str]:
    """Import tones through the app's own dialog.

    The Workspace is session-only, so a clean launch is always empty and the
    only way to give the rail rows is to drive the import the user would. The
    window title changes to the selected Track name, which is the signal that an
    import registered.
    """
    sounds = home / "measure-sounds"
    sounds.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        path = sounds / f"tone-{index:02d}.wav"
        if not path.is_file():
            _write_tone(path, 220 + index * 7)
        expected = f"tone-{index:02d}"
        if expected in (try_run(["xdotool", "getwindowname", str(window)]) or ""):
            continue
        dialog = None
        for _ in range(4):
            try_run(["xdotool", "windowactivate", str(window)])
            time.sleep(0.3)
            origin = window_origin(window)
            try_run(["xdotool", "mousemove", str(origin[0] + 146), str(origin[1] + 86)])
            try_run(["xdotool", "click", "1"])
            dialog = wait_for_dialog(timeout=5)
            if dialog is not None:
                break
        if dialog is None:
            return index, "the open dialog did not appear"
        try_run(["xdotool", "windowactivate", str(dialog)])
        time.sleep(0.4)
        try_run(["xdotool", "key", "--window", str(dialog), "ctrl+l"])
        time.sleep(0.4)
        try_run(["xdotool", "key", "--window", str(dialog), "ctrl+a"])
        time.sleep(0.2)
        try_run(["xdotool", "type", "--window", str(dialog), "--delay", "20", str(path)])
        time.sleep(0.5)
        try_run(["xdotool", "key", "--window", str(dialog), "Return"])
        if not wait_for_title(window, expected, timeout=30):
            try_run(["xdotool", "key", "--window", str(dialog), "Escape"])
            return index, f"the import of {expected} did not register"
    return count, ""


def wait_for_dialog(*, timeout: float) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = try_run(["xdotool", "search", "--name", "^Open files?$"])
        if output:
            return int(output.split()[-1])
        time.sleep(0.25)
    return None


def wait_for_title(window: int, expected: str, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        name = try_run(["xdotool", "getwindowname", str(window)])
        if name is not None and expected in name:
            return True
        time.sleep(0.25)
    return False


def _write_tone(path: Path, frequency: int) -> None:
    import math
    import struct

    frames = bytearray()
    for sample in range(int(SEED_RATE * SEED_SECONDS)):
        value = int(12000 * math.sin(2 * math.pi * frequency * sample / SEED_RATE))
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SEED_RATE)
        handle.writeframes(bytes(frames))


def launch(home: Path, condition: str, log: Path) -> subprocess.Popen[bytes]:
    environment = os.environ | {"STEMFORGE_HOME": str(home)} | CONDITIONS[condition]
    handle = log.open("wb")
    return subprocess.Popen(
        [sys.executable, "-m", "stemforge"],
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait(timeout=5)


def browser_binary() -> str | None:
    for name in BROWSER_BINARIES:
        found = shutil.which(name)
        if found is not None:
            return found
    return None


def wait_for_server(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BROWSER_URL, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    raise MeasurementError("the browser-mode server did not answer")


def launch_browser(*, width: int, height: int, profile: Path) -> subprocess.Popen[bytes]:
    binary = browser_binary()
    if binary is None:
        raise MeasurementError("no Chromium-family browser found for the control arm")
    return subprocess.Popen(
        [
            binary,
            f"--app={BROWSER_URL}",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


# -- one cell ---------------------------------------------------------------


def run_cell(
    *,
    home: Path,
    condition: str,
    size_label: str,
    size: tuple[int, int],
    tracks: int,
    idle_seconds: float,
    scroll_events: int,
    artifacts: Path | None,
    launch_timeout: float,
    keep_open: bool,
) -> Cell:
    log = home / f"launch-{condition}-{size_label}.log"
    process = launch(home, condition, log)
    browser: subprocess.Popen[bytes] | None = None
    note = ""
    try:
        if condition == "browser":
            wait_for_server(launch_timeout)
            browser = launch_browser(
                width=size[0], height=size[1], profile=home / "browser-profile"
            )
            window = wait_for_window(browser.pid, timeout=launch_timeout)
        else:
            window = wait_for_window(process.pid, timeout=launch_timeout)
        achieved = place_window(window, size, settle=2.5)
        if achieved is None:
            note = "the window manager refused the resize; measured at its own size"
        if tracks and condition in SEEDED_CONDITIONS:
            imported, problem = seed_session(window, home, tracks)
            seeded = f"seeded {imported}/{tracks} tracks"
            if problem:
                seeded = f"{seeded}: {problem}"
            note = "; ".join(part for part in (note, seeded) if part)
            # The import Jobs run one at a time; the last one keeps the view
            # busy for several seconds after the title changes.
            time.sleep(12)
        pids = {
            "stemforge": [process.pid],
            "webkit": pids_by_marker(process.pid, "WebKitWebProcess"),
            "network": pids_by_marker(process.pid, "WebKitNetworkProcess"),
        }
        if browser is not None:
            pids = {
                "stemforge": [process.pid],
                "browser": sorted(descendants(browser.pid)),
                "network": [],
            }
        idle_cpu, idle_gpu = sample(pids, lambda: time.sleep(idle_seconds))

        screenshot: str | None = None
        web_capture: Capture | None = None
        with tempfile.TemporaryDirectory(prefix="stemforge-shot-") as scratch:
            path = Path(scratch) / "window.ppm"
            try:
                web_capture = capture(window, path)
                if artifacts is not None:
                    artifacts.mkdir(parents=True, exist_ok=True)
                    target = artifacts / f"{condition}-{size_label}.png"
                    if try_run(["convert", str(path), str(target)]) is not None:
                        screenshot = str(target)
            except MeasurementError as error:
                note = "; ".join(part for part in (note, f"screenshot: {error}") if part)

        measured = achieved or size
        scroll_cpu, scroll_gpu = sample(
            pids, lambda: scroll_burst(window, measured[0], measured[1], scroll_events)
        )
        resize_cpu = cpu_over(pids, lambda: resize_burst(window, size[0], size[1]))
        return Cell(
            condition=condition,
            size=size_label,
            requested=size,
            achieved=achieved,
            idle_cpu=idle_cpu,
            scroll_cpu=scroll_cpu,
            resize_cpu=resize_cpu,
            gpu_idle=idle_gpu,
            gpu_scroll=scroll_gpu,
            capture=web_capture,
            screenshot=screenshot,
            note=note,
        )
    finally:
        if not keep_open:
            if browser is not None:
                stop(browser)
            stop(process)


# -- entry point ------------------------------------------------------------


def collect_host() -> Host:
    gpu, driver = gpu_identity()
    return Host(
        platform=f"{sys.platform} {os.uname().machine}",
        screen=screen_size(),
        window_manager=window_manager(),
        compositor=compositor(),
        webkitgtk=webkitgtk_version(),
        gpu=gpu,
        driver=driver,
        python=sys.version.split()[0],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--size", action="append", help="repeatable: WxH, default, or maximized")
    parser.add_argument("--condition", action="append", choices=CONDITION_ORDER, help="repeatable")
    parser.add_argument(
        "--home", type=Path, default=Path(tempfile.gettempdir()) / "stemforge-window-measurement"
    )
    parser.add_argument(
        "--tracks",
        type=int,
        default=8,
        help="tracks to import through the app before measuring (0 disables)",
    )
    parser.add_argument("--seconds", type=float, default=8.0, help="idle sample window")
    parser.add_argument("--scroll-events", type=int, default=400)
    parser.add_argument("--launch-timeout", type=float, default=120.0)
    parser.add_argument("--json", type=Path, help="write the raw cells as JSON")
    parser.add_argument("--artifacts", type=Path, help="write one PNG per cell")
    parser.add_argument(
        "--keep-open", action="store_true", help="leave the last window open for inspection"
    )
    args = parser.parse_args(argv)

    if session_locked():
        raise MeasurementError("the session is locked; unlock before measuring")
    home: Path = args.home
    home.mkdir(parents=True, exist_ok=True)
    screens = screen_size()
    sizes = args.size or list(DEFAULT_SIZES)
    conditions = args.condition or list(CONDITION_ORDER)
    host = collect_host()
    print(f"host: {host}", file=sys.stderr)
    baseline = baseline_gpu()
    print(
        f"gpu baseline (desktop idle): {_percent(baseline.util_percent)}% "
        f"{_mib(baseline.used_mib)} MiB",
        file=sys.stderr,
    )

    cells: list[Cell] = []
    for condition in conditions:
        for size_label in sizes:
            size = parse_size(size_label, default=DEFAULT_WINDOW_SIZE, screen=screens)
            print(f"measuring {condition} at {size_label} ({size[0]}x{size[1]})", file=sys.stderr)
            cell = run_cell(
                home=home,
                condition=condition,
                size_label=size_label,
                size=size,
                tracks=args.tracks,
                idle_seconds=args.seconds,
                scroll_events=args.scroll_events,
                artifacts=args.artifacts,
                launch_timeout=args.launch_timeout,
                keep_open=args.keep_open
                and condition == conditions[-1]
                and size_label == sizes[-1],
            )
            cells.append(cell)
            print(format_cell_row(cell), file=sys.stderr)
            if cell.note:
                print(f"  note: {cell.note}", file=sys.stderr)

    print()
    print(matrix_header())
    for cell in cells:
        print(format_cell_row(cell))
    print(
        f"\nGPU baseline (desktop idle): {_percent(baseline.util_percent)}%, "
        f"{_mib(baseline.used_mib)} MiB; whole-device counters, not per process."
    )

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "host": asdict(host),
                    "gpu_baseline": asdict(baseline),
                    "cells": [asdict(cell) for cell in cells],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\njson: {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MeasurementError as error:
        print(f"measurement failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
