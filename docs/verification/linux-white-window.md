# Linux window rendering: the forced WebKitGTK shared-memory renderer

This is the in-tree verification record for the Linux window workaround in
`configure_window`. It answers four questions with measurements from one host:

1. How much CPU, GPU, and VRAM does the native window use at rest and under
   interaction?
2. How long does an interaction take to show, and does that grow with the window?
3. Where does the large-window time go?
4. Does WebKitGTK still paint a blank white view when the workaround is removed?

The procedures are manual: they need a live X11 session, the real window, and an
unlocked desktop. The unit suite covers the harness's parsing and classification
only.

## Host

| Item | Value |
| --- | --- |
| System | Archcraft rolling, Linux x86_64 |
| Session | X11, bspwm, picom |
| Screen | 3640x1920 across two monitors |
| GPU | NVIDIA Quadro K610M (GK208, 1 GB) |
| Driver | 470.256.02 |
| WebKitGTK | 2.54.3 |
| pywebview | 6.2.1 |
| Python | 3.13.18 |

## Procedure

### Cost matrix

One cell is one launch at one size under one renderer condition:

```sh
uv run python scripts/measure_window_rendering.py \
  --size 1280x820 --condition default
```

The full matrix, with artifacts and the raw JSON:

```sh
uv run python scripts/measure_window_rendering.py \
  --tracks 8 --seconds 6 --scroll-events 300 \
  --json /tmp/window.json --artifacts /tmp/window-shots
```

The harness keeps its `STEMFORGE_HOME` under the system temp directory and drives
the real import dialog to put eight one-second tones into the session Workspace,
because the Workspace is session-only and a clean launch is always empty. The
default launch behaviour is unchanged; the harness supplies only the environment,
the size, and the input.

Sizes are the 1280x820 default, 1920x1080, and the whole 3640x1920 root window
("maximized"). Conditions:

| Name | Environment | What it tests |
| --- | --- | --- |
| `default` | none | The shipped workaround (`WEBKIT_DMABUF_RENDERER_FORCE_SHM=1`). |
| `shm-0` | `WEBKIT_DMABUF_RENDERER_FORCE_SHM=0` | The workaround removed. Zero replaces the default, so the variable is read by value. |
| `disable-dmabuf` | `WEBKIT_DISABLE_DMABUF_RENDERER=1` | The renderer disabled through the documented user override; the workaround is still set by the app. |
| `disable-dmabuf-only` | `WEBKIT_DMABUF_RENDERER_FORCE_SHM=0`, `WEBKIT_DISABLE_DMABUF_RENDERER=1` | What a default that stops forcing shared memory would leave behind: the renderer off and no forced value. |
| `browser` | `STEMFORGE_BROWSER=1`, `BROWSER=/bin/true` | The same page in a Chromium app window, no WebKitGTK. The no-op browser command stops the app opening a tab in the maintainer's own session. |

Each cell samples six seconds at idle, 300 alternating wheel events over the
track rail, and 24 window resizes back and forth by 160x120 with 120 ms between
steps. CPU is percent of one core, so 100 is one full core. The white check
captures the window and counts pixels whose three channels are all at or above
240: the page is dark, so a rendered window stays far below the floor while a
blank WebKit view is pure white. A cell is white when at least 95% of its pixels
clear the floor.

### Interaction latency

CPU is not the felt cost. Live resize stretches the last frame instead of
repainting the window, so the resize burst never pays a full repaint. Hovering or
clicking does. The latency probe times the pixels:

```sh
uv run python -m scripts.measure_window_latency \
  --condition default --size 1280x820
```

It pins the window's top-left corner to the root origin, so a requested size is
the window's real size and the same rail coordinates are on-screen at every
size. It hovers the first two track rows and clicks them in alternation,
reporting the time from the pointer warp or click until the row background, the
row icon, or the detail title changes. The click figure therefore includes the
local server round-trip and the detail-pane rebuild, not only paint, which is
what the user waits for. Polling is every 4 ms, so the resolution is about one
poll.

## Results

### Cost

`Renderer` is `WebKitWebProcess` in native cells and the whole Chromium process
tree in the `browser` arm. The `disable-dmabuf-only` rows come from separate
one-cell runs with the same method, because that condition is the decision check
rather than part of the shipped matrix.

| Condition | Size | Got | Idle app % | Idle renderer % | Idle net % | Scroll app % | Scroll renderer % | Scroll net % | Resize renderer % | White |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| default | default | 1280x820 | 0.3 | 0.2 | 0.0 | 0.4 | 56.9 | 0.0 | 180.2 | no |
| default | 1920x1080 | 1920x1080 | 0.3 | 0.0 | 0.0 | 0.4 | 29.2 | 0.0 | 185.3 | no |
| default | maximized | 3640x1920 | 0.3 | 0.0 | 0.0 | 0.4 | 30.5 | 0.0 | 174.5 | no |
| shm-0 | default | 1280x820 | 0.3 | 0.0 | 0.0 | 0.4 | 2.4 | 0.0 | 2.8 | yes |
| shm-0 | 1920x1080 | 1920x1080 | 0.5 | 0.2 | 0.0 | 0.0 | 3.3 | 0.0 | 1.7 | yes |
| shm-0 | maximized | 3640x1920 | 0.2 | 0.0 | 0.2 | 0.4 | 3.2 | 0.0 | 2.1 | yes |
| disable-dmabuf | default | 1280x820 | 0.3 | 0.0 | 0.0 | 0.4 | 22.7 | 0.0 | 75.2 | no |
| disable-dmabuf | 1920x1080 | 1920x1080 | 0.5 | 0.0 | 0.0 | 0.4 | 20.3 | 0.0 | 67.6 | no |
| disable-dmabuf | maximized | 3640x1920 | 0.3 | 0.2 | 0.0 | 0.4 | 5.1 | 0.0 | 26.3 | no |
| browser | default | 1280x820 | 0.2 | 0.5 | 0.0 | 0.4 | 6.1 | 0.0 | 28.4 | no |
| browser | 1920x1080 | 1920x1080 | 0.5 | 0.2 | 0.0 | 0.4 | 6.5 | 0.0 | 49.9 | no |
| browser | maximized | 3640x1920 | 0.3 | 0.5 | 0.0 | 0.4 | 5.3 | 0.0 | 44.7 | no |
| disable-dmabuf-only | 1280x820 | 1280x820 | 0.5 | 0.0 | 0.0 | 0.8 | 21.2 | 0.0 | 71.6 | no |
| disable-dmabuf-only | maximized | 3640x1920 | 0.7 | 0.0 | 0.0 | 0.4 | 5.1 | 0.0 | 28.7 | no |

`WebKitNetworkProcess` is zero in every cell (one 0.2 reading), so the column is
included but flat. The `shm-0` cells are a blank window: their idle, scroll and
resize rows measure the blank view, not the page.

| Condition | Size | Idle GPU % | Idle VRAM MiB | Scroll GPU % | Scroll VRAM MiB |
| --- | --- | --- | --- | --- | --- |
| default | default | 35.0 | 429 | 56.2 | 429 |
| default | 1920x1080 | 47.0 | 442 | 49.8 | 442 |
| default | maximized | 49.0 | 480 | 71.3 | 542 |
| shm-0 | default | 55.0 | 424 | 58.3 | 424 |
| shm-0 | 1920x1080 | 55.8 | 433 | 58.9 | 433 |
| shm-0 | maximized | 50.6 | 480 | 56.0 | 480 |
| disable-dmabuf | default | 34.5 | 453 | 61.3 | 453 |
| disable-dmabuf | 1920x1080 | 35.5 | 453 | 53.3 | 453 |
| disable-dmabuf | maximized | 50.1 | 480 | 82.7 | 553 |
| browser | default | 55.3 | 424 | 54.9 | 424 |
| browser | 1920x1080 | 55.6 | 424 | 55.6 | 424 |
| browser | maximized | 49.9 | 453 | 39.0 | 453 |
| disable-dmabuf-only | 1280x820 | 51.6 | 429 | 74.2 | 429 |
| disable-dmabuf-only | maximized | 49.7 | 480 | 83.6 | 553 |

GPU baseline with the desktop idle: 52.3%, 429 MiB. The whole-device counters
include the rest of the desktop and are noisy: they do not separate the
renderer conditions. The one repeatable movement is VRAM during the maximised
scroll in the three rendered native cells, +62 to +73 MiB over their own idle,
which the blank and browser cells do not show.

### Interaction latency

| Condition | Size | Hover median | Click median |
| --- | --- | --- | --- |
| default | 1280x820 | 61.3 ms | 218.4 ms |
| default | 3640x1920 | 652.3 ms | 566.3 ms |
| disable-dmabuf-only | 1280x820 | 22.8 ms | 149.2 ms |
| disable-dmabuf-only | 3640x1920 | 485.3 ms | 1034.7 ms |

Medians of the trials that detected a change: four of eight for hover and seven
or eight of eight for click. The hover figure is the paint signal. The click
figure includes the server round-trip and the detail-pane rebuild, and at
3640x1920 it is too noisy to rank the renderer paths: individual
`disable-dmabuf-only` trials ranged from 614 to 1932 ms, so its higher median
does not mean the renderer made clicks slower. A missed trial means the row was
already in the target state or no change appeared within five seconds.

### Where the large-window time goes

The 3640x1920 cell leaves three candidates: WebKitGTK's paint, picom's
composite, and the populated page's own content. These controls separate them.
Unless marked, each runs at 3640x1920 with picom on the maintainer's xrender
configuration.

| Control | Hover median | What it shows |
| --- | --- | --- |
| app, default, eight tracks | 652.3 ms | the reported case |
| app, default, two tracks | 520.5 ms | the track list adds some cost |
| app, default, empty Workspace | 118.7 ms | the base page is fast |
| minimal WebKitGTK page, 292x59 row | 113.8 ms | WebKitGTK's baseline is fast |
| minimal WebKitGTK page, full-page hover | 129.8 ms | a full-page change is about 130 ms |
| Chrome app window, same page, empty | 41.0 ms | the compositor is not generically slow |
| Chrome app window, software rendering | 13.8 ms | a software path is not generically slow |
| app, default, eight tracks, picom stopped | 189.0 ms | picom adds about 460 ms |
| app, disable-dmabuf-only, eight tracks, picom stopped | 126.6 ms | the renderer term remains without picom |
| app, default, two tracks, picom glx | 547.7 ms | the xrender backend is not the cause |
| app, `WEBKIT_DISABLE_COMPOSITING_MODE=1`, eight tracks | 574-1220 ms | disabling accelerated compositing is not the fix |

The Chrome control started the app in browser mode (`STEMFORGE_BROWSER=1`,
`BROWSER=/bin/true`) and opened `google-chrome-stable --app=http://127.0.0.1:8080
--window-size=3640,1920` with a fresh profile. The minimal page is a frameless
GTK window with one `WebKit2.WebView` loading a 292x59 hover row and a
full-page hover block. The compositor cells stop picom and restart it with the
same configuration; the glx cell adds `--backend glx`. All are manual one-offs.

The empty app page and the minimal WebKitGTK page both hover in about
115-130 ms, and Chrome's empty page in 41 ms. Neither WebKitGTK nor picom is
slow by itself at this size. The populated page is: two tracks bring the app to
520 ms and eight to 652 ms, and any hover in that page is in the same range (the
fixed Import audio button measured 466 ms against 327 ms for a track row in one
two-track run, both with wide spreads). The cost is page-wide, not tied to the
hovered row.

Stopping picom lowers the populated default from 652 to 189 ms, so most of the
gap is the compositor's handling of the populated page. Without picom the
populated page is still slower than the empty page (189 against 119 ms), and the
renderer choice still shows (default 189 against 127 ms for
`disable-dmabuf-only`), so WebKit's repaint is a second, smaller term. The
two-track result against the empty result puts the detail pane or track-row
content, not the base page, at the centre. A hidden `<audio>` element with a
loaded source did not reproduce the cost in the minimal page (115.7 ms), so the
media element is ruled out.

The exact layer or paint inside the populated page is not isolated here. The
candidates left are the detail pane's composited content and the row list's
layer structure; both need a WebKit layer/paint profile, which is outside this
measurement's scope.

### Why the extra conditions and workloads

The four conditions and the idle/scroll samples are the required matrix. The
resize burst, the fifth condition, and the latency probe were added to answer the
workaround decision: resize is the controlled full-window CPU workload,
`disable-dmabuf-only` is the configuration a changed default would produce, and
latency is the quantity the report is actually about. The compositor stop, the
Chrome app window, and the minimal WebKitGTK page are diagnostic controls, not
shipped conditions: each removes one candidate so the remaining large-window cost
can be attributed. None of them changes product behaviour.

## White-screen check

| Condition | Sizes measured | Screenshot | Outcome |
| --- | --- | --- | --- |
| `default` | all three | rendered | pass |
| `shm-0` | all three | pure white | fail, the blank view reproduces |
| `disable-dmabuf` | all three | rendered | pass |
| `disable-dmabuf-only` | 1280x820, maximized | rendered | pass |
| `browser` | all three | rendered | not applicable |

With `WEBKIT_DMABUF_RENDERER_FORCE_SHM=0` the window is pure white at all three
sizes: 100% of captured pixels at or above the floor. The shipped default and
the DMABUF-disabled configurations render the page in every cell measured.

That also settles the spelling question. If WebKit read the variable by
presence rather than by value, `=0` would have kept the forced shared-memory
path and rendered exactly like `default`. It did not; it took the GBM path and
blanked, so zero really does switch the renderer off.

![The native window with the workaround removed: the whole web view is white.](../screenshots/linux-white-screen.png)

![The same build with the shipped default: the window renders, with the eight seeded tracks in the rail.](../screenshots/linux-window-populated.png)

## Conclusion

**The report is reproduced, and it is latency.** Under the shipped default a
hover goes from 61 ms at 1280x820 to 652 ms at 3640x1920, and a click from 218
to 566 ms. The window is not slower because it computes more; it is slower
because each repaint takes longer to show, and the larger window takes longer to
repaint.

**The average CPU columns missed it.** Live resize stretches the last frame
instead of repainting, so the resize burst stays at 174-185% of a core at every
size, and idle is flat at 0.3%. The cost is per repaint, not per second, so a
CPU mean over a fixed window cannot see it. The latency probe is the right
instrument; the earlier reading of the CPU matrix as "no area effect" is
corrected by these numbers.

**The guard is still required on this host.** Removing it paints a blank white
view on WebKitGTK 2.54.3 with the 470 driver on X11, at all three sizes. The
alternative guard renders as well: `disable-dmabuf-only` passes the white check
at the sizes measured. The requirement is therefore keeping WebKit off the
failing DMABUF path, not the `FORCE_SHM` spelling specifically.

**The large-window cost is the populated page, amplified by picom.** The empty
app page and a minimal WebKitGTK page hover in 115-130 ms, and Chrome's empty
page in 41 ms (14 ms with software rendering). The populated eight-track page is
652 ms with picom and 189 ms without it; the two-track page is 520 ms with
picom. So neither WebKitGTK nor picom is slow alone. The populated page's
repaint path is the dominant term, and picom's composite of that page adds about
460 ms of the 652 ms default. The exact layer inside the populated page is not
isolated; the detail pane and the row list are the remaining candidates.

**The forced-SHM renderer is a default-size term, not the large-window fix.**
At 1280x820 the DMABUF-disabled configuration cuts hover from 61 to 23 ms, click
from 218 to 149 ms, and resize-renderer CPU from 180 to 72-75% of a core. At
3640x1920 it lowers the populated hover from 652 to 485 ms with picom and from
189 to 127 ms without, but the result stays in the hundreds of milliseconds and
the click figures are too noisy to rank the paths. Disabling accelerated
compositing (`WEBKIT_DISABLE_COMPOSITING_MODE=1`) did not fix the maximised lag
(574-1220 ms), and neither did picom's glx backend (548 ms). No renderer
environment value tested here removes the maximised cost.

**Recommendation.** Keep a Linux guard. The measured default-size win favors
replacing `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1` with
`WEBKIT_DISABLE_DMABUF_RENDERER=1` under the same `setdefault`: it renders,
keeps WebKit off the white-screen path, and cuts default-size hover and CPU. It
does not fix the maximised lag, so it should not be sold as that fix. The
maximised lag needs a follow-up profile of the populated page's layer/paint and
a host compositor check; the decision to change the default remains the
maintainer's.

## Limits

- One host, one GPU, one driver, one WebKitGTK version. Another driver may not
  need the guard at all.
- Wayland is untested.
- Latency is measured to the first changed pixel, with a 4 ms poll and process
  scheduling noise; the click figure includes the local server round-trip and
  the detail-pane rebuild, and is too noisy at 3640x1920 to rank the renderer
  paths.
- The 3640x1920 window is 7.0 Mpx, but only 5.76 Mpx is on the two monitors: the
  bottom 480 px of the left monitor is outside the root. WebKit still paints the
  full window surface, so the latency includes that off-screen area.
- The probe's row positions are the first two rows of the shipped rail layout.
  A layout change moves them and the probe then reports no detected trials.
- The GPU columns are whole-device and include the rest of the desktop.
- The `browser` arm runs an empty Workspace: browser mode has no native import
  dialog, so it cannot be populated through the page. Its renderer column sums
  the Chromium tree and is not comparable with the native rows.
- The compositor stop, the Chrome app window, the minimal WebKitGTK page, the
  hidden-audio page, and the `WEBKIT_DISABLE_COMPOSITING_MODE` and picom-glx
  cells are one-off diagnostic controls, not part of the shipped procedure. They
  use the maintainer's picom configuration (xrender, no shadows, blur, or
  fading); another compositor configuration can change the absolute numbers.
- The populated page includes the selected track's detail pane and the rail
  rows. The empty state and the minimal page establish the baseline, but the
  exact layer or paint inside the populated page is not isolated.
- The eight-track session is small. A much larger Workspace adds rows and detail
  content, but the populated page already dominates at eight tracks, so the
  renderer comparison does not depend on a larger session.
