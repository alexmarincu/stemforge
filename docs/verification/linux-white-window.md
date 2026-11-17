# Linux native window starts white

Fixed on `fix/linux-white-window`, based on `integration/v1` `12f7f83`
(2026-11-17). The report was a white `StemForge` window while the GTK frame and
the local NiceGUI server were healthy.

## Environment

| Item | Value |
|---|---|
| Kernel | Linux 7.3.4-arch1-1 |
| Session | X11 |
| GPU | NVIDIA Quadro K610M (GK208GLM) |
| Driver | NVIDIA 470.256.02 |
| WebKitGTK | 2.54.2 |
| pywebview | 6.2.1 |
| NiceGUI | 4.0.0 |

## Symptom

`uv run stemforge` opened a blank white window. The server was fine (`GET /`
returned 200 and the full HTML), so this was not a page or route failure. stderr
carried the renderer failure:

```text
KMS: DRM_IOCTL_MODE_CREATE_DUMB failed: Permission denied
Failed to create GBM buffer of size 1280x820: Permission denied
```

## Reproduction

The bug is in WebKitGTK's GBM buffer path, not in the StemForge page. A minimal
pywebview window loading a solid-red local HTML file reproduces it:

```sh
uv run python /tmp/opencode/webview-test.py file:///tmp/opencode/red.html SF-RED
xwd -id "$(xdotool search --onlyvisible --name '^SF-RED$' | head -1)" -out /tmp/red.xwd
magick /tmp/red.xwd -colorspace Gray -format '%[fx:mean]\n' info:
```

| Run | `WEBKIT_DMABUF_RENDERER_FORCE_SHM` | Grey mean | Result |
|---|---|---:|---|
| 1 | unset | 0.996 | white |
| 2 | unset | 0.994 | white |
| 3 | unset | 0.996 | white |
| 4 | `1` | 0.215 | red page rendered |
| 5 | `1` | 0.213 | red page rendered |
| 6 | `1` | 0.377 | red page rendered |

Loading the real `http://127.0.0.1:8000/` page in the same probe goes from
`0.994` (white) unset to `0.076` (workspace rendered) with the variable set. The
full app was then launched from the branch and captured at `0.080`; the
workspace, sidebar, empty-state model card, and cache meter were all visible.

## Root cause

WebKitGTK 2.52+ uses the Skia/DMA-BUF renderer. On this NVIDIA/X11 host it
cannot create a GBM buffer (`Permission denied`), so the web view paints white
even though the GTK window and the HTTP server are healthy. Forcing the
DMA-BUF renderer onto shared memory (`WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`)
avoids the GBM allocation and renders normally.

`WEBKIT_DISABLE_DMABUF_RENDERER=1` alone still rendered white;
`WEBKIT_DISABLE_COMPOSITING_MODE=1` alone still rendered white. The two together
also worked, but disabling compositing as well is a larger change than needed.
`WEBKIT_DMABUF_RENDERER_DISABLE_GBM=1` worked too, but is less specific than the
shared-memory switch. The final fix uses `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`.

## Fix

`src/stemforge/integration/window.py` sets the variable with `setdefault`
before NiceGUI spawns the window process:

```python
if sys.platform.startswith("linux"):
    os.environ.setdefault(WEBKIT_SHM_ENV, "1")
```

The child webview process inherits it. Verified on the running app: the
`python -m stemforge` parent's initial `/proc/<pid>/environ` lacks the variable,
while every spawned webview child carries
`WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`. An explicit user value wins because of
`setdefault`; `packaging/linux/README.md` documents the remaining CPU-rendering
override for hosts where shared memory is still not enough.

## Tests

| Check | Result |
|---|---|
| `pytest tests/unit/test_run_ui.py` | 9 passed |
| `ruff format --check` / `ruff check` on the changed files | clean |
| `mypy src/stemforge/integration/window.py` | clean |

The unit test pins the Linux default and the explicit-override behavior. There
is no automated rendering test: the failure needs WebKitGTK, a display, and the
host GPU driver, which the unit and CI layers do not have. The manual probe
above is the red-capable check for this bug.

## Deviation

Ticket 10 §5 says preflight should print the 2.52+ Skia/NVIDIA workarounds. The
app now applies the least intrusive one before the window opens, so the window
works out of the box; the README carries the CPU-rendering fallback for hosts
where shared memory is insufficient. Printing the workaround on every launch
would be noise once it is the default.
