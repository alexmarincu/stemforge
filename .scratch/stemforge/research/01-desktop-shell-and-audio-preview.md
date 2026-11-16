# Desktop shell & audio preview — research report

**Ticket:** research 01 (desktop shell + audio preview)
**Status:** complete, decision-ready
**Date:** 2026-11-14
**Scope:** NiceGUI 4.0 native desktop mode, pywebview 6.2.x backends on Linux and Windows, native open/save dialogs, HTML5 audio playback inside the embedded webview, and packaging implications. Decisions referenced as "the map" live in `.scratch/stemforge/map.md`.

**Evidence labels used throughout**

- **[S]** verified by reading the shipped source of the exact version (NiceGUI 4.0.0 wheel, pywebview 6.2.1 wheel, WebKit source).
- **[D]** vendor/project documentation.
- **[M]** measured on this research host (Arch-based, Python 3.14, NiceGUI 4.0.0 in a scratch venv). Commands in Appendix B.
- **[R]** third-party project issue report (credible, but not vendor documentation).

**Limits of this research.** This host has no WebKitGTK and no Windows. Nothing was executed against a real GTK/WebView2 window. All runtime claims about *rendering, dialogs, and codecs* are from source and documentation; the HTTP/media claims are measured. Runtime verification on both target OSes is listed under Open Questions.

---

## TL;DR — recommendation

1. **Use NiceGUI's built-in native mode** (`ui.run(native=True)`), i.e. pywebview. Do **not** build a pywebview shell by hand and do **not** ship a browser-based "app mode" window. NiceGUI's implementation already solves the process, port, lifecycle and shutdown problems that a hand-rolled shell gets wrong. See §2.
2. **Linux backend: GTK/WebKitGTK (the default). Windows backend: EdgeChromium/WebView2 only** — and *assert* it at startup, because pywebview 6.2.1 silently falls back to MSHTML/IE11 when WebView2 is missing, which produces a broken NiceGUI window (§6.1, Risk R1).
3. **Native file dialogs work on both platforms** via `await app.native.main_window.create_file_dialog(...)`, but there is **no NiceGUI wrapper and no NiceGUI documentation** for it — StemForge must own a small wrapper that (a) builds pywebview's very strict filter strings, (b) appends the save extension itself on Linux, and (c) tolerates the known Windows cross-thread deadlock (§4, Risk R3).
4. **Audio preview: serve media over NiceGUI's local HTTP server with `app.add_media_files()` and play it with an `<audio>` element.** Range requests and seeking work [M]. Do **not** use `file://` (§5.4). MP3 preview (as already decided in the map) is the right call: MP3 is the least fragile codec in WebKitGTK *and* WebView2, **provided `gst-plugins-good` is present on Linux** (§5.2).
5. **Linux distribution is the biggest packaging risk, not Windows.** Bundling WebKitGTK into an AppImage is a known-fragile path (webkit helper subprocess paths, GStreamer plugin-path poisoning, EGL/GTK ABI skew) [R]. The low-risk v1 shape is: **tar.gz + a launcher that declares/checks host `webkit2gtk-4.1` + GStreamer**, with AppImage as an opt-in extra that bundles *no* webkit. See §6.2 and Risk R5.

---

## 1. Versions verified

| Component | Version | Date | How verified |
|---|---|---|---|
| NiceGUI | **4.0.0** (latest) | 2026-11-04 | PyPI JSON + wheel contents [S] |
| NiceGUI Python support | `>=3.11,<4` (3.11–3.15) | — | PyPI metadata [S] |
| NiceGUI `native` extra | `pywebview>=5.0.1,<7` | — | `nicegui-4.0.0` wheel `METADATA` [S] |
| pywebview | **6.2.1** (latest) | 2026-04-15 | PyPI JSON + wheel contents [S] |
| pywebview 6.2 | 6.2 | 2026-04-13 | changelog [D] |
| WebKitGTK (Arch `webkit2gtk-4.1`) | **2.54.2** | 2026-10-23 | Arch package API [S] |
| WebKitGTK (Debian 13 trixie) | 2.52.6 | current | sources.debian.org [S] |
| WebKitGTK (Ubuntu 24.04 / 22.04) | 2.52.6 / 2.50.4 | current | packages.ubuntu.com [S] |
| GStreamer (host) | 1.28.9 | 2026-11-08 | `pacman -Si gstreamer` [M] |
| WebKitGTK GStreamer floor | **1.20.0** at build time | — | `Source/cmake/GStreamerChecks.cmake` [S] |
| WebView2 SDK vendored by pywebview | `Microsoft.Web.WebView2` 1.0.3856.49 | — | pywebview 6.2 changelog [D] |

Notes:

- NiceGUI 4.0 is a cleanup release; its release notes contain **no native-mode breaking changes**. The native fixes that matter landed in 3.14–3.19 (#6111, #6131, #6132, #6381, #6392) and are all present in 4.0.0 [S].
- NiceGUI 4.0 **removed** the deprecated APIs from 3.x and now warns/errors differently, but `app.native.*` is unchanged.
- A proposal to add a non-pywebview "borrow the user's Chromium" fallback (#6264) is **not** in 4.0.0 — `nicegui/native/` contains only the pywebview implementation [S].

---

## 2. How NiceGUI native mode actually works

This is the part that decides most of the design, and it is not obvious from the docs.

### 2.1 Process model

- `ui.run(native=True)` starts the **uvicorn server in the main process** and spawns **one extra process** for the window (`native.SPAWN_CONTEXT.Process(target=_open_window, daemon=True)`). The spawn context is forced ("the window subprocess must not fork", #1841) [S].
- The window process waits for the port to accept connections, then builds the pywebview window and calls `webview.start()` [S].
- The window therefore loads `http://127.0.0.1:<port>/` — **real HTTP**, not `load_html`, not `file://`. Port is auto-selected from 8000–8999 unless passed (`native.find_open_port()`, default in native mode) [S][D].
- Killing/closing the window ends the app: the server process polls the window process and calls `core.stop_and_exit()` (hard `os._exit`), which also handles the "windowed build has no stdout" crash fixed in #6392 [S].

**Consequences for StemForge**

- Anything configured in the *parent* process must cross a `pickle` boundary into the window process. NiceGUI passes `window_args`, `settings`, `start_args` as picklables, re-imports the app module in the child, and **warns about keys it could not transfer** (#6082) [S]. So: keep `app.native.*` values plain data; a lambda or live object silently degrades to "ignored" with a warning.
- `app.native.*` assignments must be at **module import time, outside `if __name__ == '__main__':`** — the docs call this out explicitly, because the child re-imports the module [D].
- In frozen builds, `freeze_support()` must be the **first statement in the main guard**, and `app.native` config must stay outside it [D].

### 2.2 Window API surface

`app.native.main_window` is not the pywebview `Window`; it is `nicegui.native.native.WindowProxy`, an **async proxy** that marshals every call over a `multiprocessing.Queue` to the window process and (for getters/dialogs) waits on a response queue [S].

Proxy methods present in 4.0.0 (all `async` where they return a value) [S]:

`get_always_on_top`, `set_always_on_top`, `get_size`, `get_position`, `load_url`, `load_html`, `load_css`, `set_title`, `get_cookies`, `get_current_url`, `destroy`, `show`, `hide`, `set_window_size`, `resize`, `maximize`, `minimize`, `restore`, `toggle_fullscreen`, `move`, `evaluate_js`, `create_confirmation_dialog`, `create_file_dialog`, `signal_server_shutdown`.

Not available: `expose()` raises `NotImplementedError`, and the real `Window` object (`window.native`, `window.dom`, `window.events`) is not reachable from the server process [S].

### 2.3 Lifecycle and threading rules

- `webview.start()` **must run on the main thread of its process** — pywebview raises `WebViewException('pywebview must be run on a main thread.')` otherwise [S]. NiceGUI satisfies this by using a subprocess.
- Inside the window process, NiceGUI runs a **dedicated executor thread** that pulls method calls off the queue and invokes them on the real window. Dialog calls are dispatched onto *further* threads (one per call) so a modal dialog does not block other window calls [S].
- Window events are bridged one-way into the server process: `shown`, `loaded`, `minimized`, `maximized`, `restored`, `resized`, `moved`, `closed`, `drop`, exposed as `app.native.on(event, handler)` with sync or async handlers [S][D].
- **`closing` is not bridged.** The source says: *"'closing' is not bridged yet — it requires a synchronous round-trip to support vetoing the close"* [S]. **There is no supported way to veto a window close from NiceGUI** (§8, Risk R4).
- Closing the window while a file dialog is open logs `shutdown is possibly blocked by opened dialogs like a file picker` and waits for the dialog thread [S]. Design for autosave, not for "prompt on close".

### 2.4 What the window looks like

- Default 800×600, `window_size=(w,h)`, `fullscreen=True`, `frameless=True` all force native mode [S].
- `app.native.window_args` is forwarded verbatim to `webview.create_window(...)`, `app.native.start_args` to `webview.start(...)`, `app.native.settings` to `webview.settings` [S][D].
- Windows-only nicety: a local `.ico` `favicon` is applied as window/taskbar icon via `WM_SETICON` + an `AppUserModelID` (`nicegui.<title>`) [S]. On Linux, set `app.native.start_args['icon'] = '/path/icon.png'` (passed to `webview.start(icon=...)`) [S][D].
- Windows quirk already handled: uvicorn's console-wide Ctrl+C would close a pywebview ≥6.2 window, so NiceGUI installs `SetConsoleCtrlHandler(None, TRUE)` on Windows to ignore it (#6381) [S]. This is why `reload=True` no longer kills the window on Windows — but still use `reload=False` in a shipped build.

---

## 3. Supported vs unsupported window behaviour

| Capability | Linux (GTK/WebKitGTK) | Windows (EdgeChromium) | Notes |
|---|---|---|---|
| `ui.run(native=True)` | ✅ | ✅ | requires the deps in §6 |
| resize / move / minimize / maximize / fullscreen | ✅ | ✅ | via proxy, async |
| always-on-top | ✅ (idle_add) | ⚠️ known deadlock | pywebview #1823, unfixed in 6.2.1 [R] |
| set title from page | ✅ | ✅ | `ui.page_title()` routes to the native window [S] |
| `evaluate_js` from server | ✅ | ✅ | round-trip through the queue |
| drag & drop files with full paths | ✅ (bridged) | ✅ | `app.native.on('drop')`; `pywebviewFullPath` [S][D] |
| veto window close | ❌ | ❌ | `closing` not bridged [S] |
| multiple native windows | ❌ | ❌ | NiceGUI owns exactly one window |
| access real `Window`/`native` handle | ❌ | ❌ | proxy only |
| custom menu bar | ❌ (GTK unsupported anyway) | ⚠️ not exposed | pywebview `menu=` exists but is not plumbed through NiceGUI |
| devtools | `app.native.start_args['debug']=True` | same | opens inspector on GTK, DevTools on Edge [S] |

---

## 4. Native open/save dialogs

### 4.1 API

Only route: `app.native.main_window.create_file_dialog(...)`, awaited [S]:

```python
from nicegui import app
import webview  # optional dependency, comes with the `native` extra

paths = await app.native.main_window.create_file_dialog(
    dialog_type=webview.FileDialog.OPEN,   # OPEN=10, FOLDER=20, SAVE=30
    directory='/home/me/Music',            # ignored if it does not exist
    allow_multiple=True,
    save_filename='',                      # SAVE only
    file_types=('Audio (*.wav;*.mp3;*.flac)',),
)
# -> tuple[str, ...] | None   (None == cancelled)
```

- `webview.FileDialog` is the 6.x enum; the deprecated ints 10/20/30 still work and the proxy falls back to them if the enum is missing [S].
- `Window.create_file_dialog` first **validates every filter string** and raises `ValueError` on a bad one, then discards a non-existent `directory` [S].
- Return shape: tuple of paths, or `None` on cancel. `FOLDER` returns a 1-tuple. `SAVE` returns a 1-tuple [S].
- The call blocks a NiceGUI thread-pool worker (`run.io_bound`) for as long as the dialog is open [S].

### 4.2 File filters are stricter than they look

The accepted grammar (pywebview `util.parse_file_type`) is [S]:

```
^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$
```

Measured accept/reject [M]:

| Filter string | Result |
|---|---|
| `Audio (*.wav;*.mp3;*.flac)` | ✅ `('Audio ', '*.wav;*.mp3;*.flac')` |
| `Audio files (*.wav)` | ✅ |
| `Audio (*)` | ✅ |
| `Stems (*.stem;*.stems)` | ✅ |
| `Audio, video (*.wav;*.mp4)` | ❌ comma not allowed in description |
| `Audio (*.wav; *.mp3)` | ❌ no space after `;` |
| `(*.wav)` | ❌ description required |
| `Audio (*.aif*)` | ❌ only `\w` extensions, or bare `*` |

So: description must be `[\w ]+` (letters/digits/underscore/space only), patterns are `*.ext` with word-character extensions, separated by `;` with no spaces. Build these from a constant, and unit-test the exact strings — an invalid one raises at call time, i.e. when the user clicks "Open".

### 4.3 Behaviour per platform

| | Linux (GTK3) | Windows (WinForms) |
|---|---|---|
| Implementation | `gtk.FileChooserDialog` with `OPEN`/`SAVE`/`SELECT_FOLDER`, `set_select_multiple`, `add_filter` + `add_pattern` [S] | `System.Windows.Forms.OpenFileDialog` / `SaveFileDialog` / custom `OpenFolderDialog` [S] |
| Runs on | GTK main loop via `glib.idle_add`, caller blocks on a semaphore [S] | the **calling thread** — `dialog.ShowDialog(form)` with no `Invoke` marshalling [S] |
| Multi-select | ✅ `get_filenames()` | ✅ `Multiselect = allow_multiple` |
| Filter syntax | GTK glob patterns, one filter object per description [S] | WinForms filter string: `Description (*.ext)` + `&#124;` + `*.ext`, entries joined with `&#124;` [S] |
| Save: default name | `set_current_name(save_filename)` [S] | `FileName = save_filename` |
| Save: extension appended | **No.** GTK returns what the user typed [S] | **Yes**, by default: `AddExtension=true`, extension taken from the selected filter because pywebview never sets `DefaultExt` [D][S] |
| Overwrite prompt | GTK native | WinForms `OverwritePrompt` default true |
| Known defect | none found | cross-thread `ShowDialog` can deadlock the GUI thread under activation load — pywebview #1823, closed as stale, **not fixed in 6.2.1** [R] |

**Implication for StemForge:** normalise the save path in Python — if `Path(p).suffix == ''`, append the chosen format's extension yourself. Do not rely on GTK (it won't) or on Windows (it will, but only when the user keeps a filter selected).

---

## 5. Audio playback inside the native webview

### 5.1 Serving media from NiceGUI — measured

`ui.audio(Path)` does **not** stream from disk; `SourceElement` calls `app.add_media_file(local_file=...)` and rewrites `src` to an HTTP route [S]. That route is NiceGUI's own range responder (`nicegui/app/range_response.py`), not Starlette's `FileResponse` [S].

Measured against NiceGUI 4.0.0 + Starlette 1.10.0 [M], 100 000-byte file:

| Request | Result |
|---|---|
| `GET /media/x.wav` | `200`, `Accept-Ranges: bytes`, `Content-Length: 100000`, `Content-Type: audio/x-wav`, plus a `Content-Range` header (harmless spec deviation) |
| `GET` + `Range: bytes=1000-` | `206`, `Content-Range: bytes 1000-99999/100000`, `Content-Length: 99000` |
| `GET` + `Range: bytes=0-1` | `206`, length 2 |
| `GET` + `Range: bytes=99999-` | `206`, length 1 |
| `GET` + `Range: bytes=-500` (suffix form) | **`416`** — suffix ranges are not implemented |
| `GET` + `Range: bytes=0-1,3-4` (multi) | **`416`** |
| `HEAD /media/x.wav` | **`405`** with a JSON body |

- Seeking works: browsers seek by issuing `Range: bytes=N-`, which is exactly what is implemented. Suffix and multi-range requests are rejected with `416`; if a media stack ever probes with `bytes=-1` it will fail. Worth a real-device check (Open Question Q1).
- `HEAD` is not routed: both `add_media_file` and `add_media_files` register GET-only routes and this Starlette version answers `405` with `Allow: GET` (measured on both) [M]. `<audio>` normally does not HEAD, but a `preload`/probe path might; if it does, add an explicit HEAD route that reuses `get_range_response` headers.
- Chunk size is a query parameter (`nicegui_chunk_size`, default 8192, clamped to 1–8 KiB) [S].

**Route strategy.** `add_media_file` registers **one route per file** and never removes it until the element is deleted [S]. A track list that creates `ui.audio(track.path)` per row leaks routes for the session. Instead mount the library once:

```python
app.add_media_files('/library', library_root)   # one route, {filename:path}
ui.audio('/library/<track_id>/preview.mp3')
```

`add_media_files` resolves and confines paths to the directory (`is_relative_to`) and returns 404 otherwise [S]. Note that it serves *any* file under that root — point it at a dedicated cache dir, not the user's music folder, if you do not want to expose the whole library.

**MIME types.** The route uses `mimetypes.guess_type` [S]. On Linux this is sane: `.mp3→audio/mpeg`, `.wav→audio/x-wav`, `.flac→audio/x-flac`, `.m4a→audio/mp4`, `.opus→audio/ogg` [M]. On Windows `mimetypes` is seeded from the registry, so results vary by machine and unknown extensions become `application/octet-stream`. If a preview ever needs `.opus`/`.flac`, register `mimetypes.add_type(...)` at startup, or simply keep the decided "preview = MP3" rule.

### 5.2 Linux: WebKitGTK + GStreamer

WebKitGTK delegates *all* media decoding to GStreamer. The media player builds a `playbin3`/`uridecodebin` pipeline and lets the plugin registry auto-plug decoders — so "which codecs work" is exactly "which GStreamer plugins are installed" [S: `MediaPlayerPrivateGStreamer.cpp`, `GStreamerChecks.cmake`].

pywebview enables the relevant WebKit settings explicitly: `enable_media_stream`, `enable_mediasource`, `enable_webaudio`, `enable_webgl`, clipboard access, and `allow_file_access_from_file_urls` from `ALLOW_FILE_URLS` [S].

**Who installs the plugins**

- **Debian** (`libwebkit2gtk-4.1-0`) *Depends*: `gstreamer1.0-plugins-base`, `gstreamer1.0-plugins-good`; *Recommends*: `gstreamer1.0-libav`, `gstreamer1.0-plugins-bad` (via `${gst:Recommends}`) [S: debian/control + debian/rules].
- **Ubuntu** moves `libav` and `-bad` from Recommends to **Suggests** (not in `main`) [S: debian/rules]. A plain Ubuntu install therefore may lack AAC.
- **Arch** (`webkit2gtk-4.1`) *Depends* only `gstreamer`, `gst-plugins-base-libs`, `gst-plugins-bad-libs`; *OptDepends* `gst-plugins-good`, `gst-plugins-bad`, `gst-libav` [S]. A minimal Arch box can have **no usable audio decoder at all**.

**Element → package mapping, measured on GStreamer 1.28.9** [M] (`pacman -Qo` of each element's `.so`):

| Format | Decoder element | Package |
|---|---|---|
| WAV | `wavparse` | `gst-plugins-good` |
| MP3 | `mpg123audiodec` + `mpegaudioparse` | `gst-plugins-good` |
| FLAC | `flacdec` + `flacparse` | `gst-plugins-good` |
| Opus/Ogg | `opusdec` + `oggdemux` | **`gst-plugins-base`** (the *plugins* package, not `-base-libs`) |
| AAC | `faad` (bad) or `avdec_aac` (libav) | `gst-plugins-bad` / `gst-libav` |
| sink | `autoaudiosink`, `pulsesink` | `gst-plugins-good`; `pipewiresink` is a separate `gst-plugin-pipewire` |
| convert/resample | `audioconvert`, `audioresample` | `gst-plugins-base-libs` (pulled by webkit) |

Reading of this table:

- **`gst-plugins-good` is the single mandatory plugin set** for any preview at all (WAV *and* MP3). It is a hard dependency on Debian/Ubuntu but only an optdepend on Arch → **runtime check needed**, not just a doc note.
- **MP3 is the safest choice**: decoder is in `-good`, no `-ugly` needed, LGPL mpg123. This validates the map's "preview uses a cached webview-safe MP3" decision.
- **AAC is the fragile one** (patent-encumbered; `-bad`/`libav`, Ubuntu only Suggests). Do not preview from `.m4a`/`.aac`; transcode.
- **Opus needs `gst-plugins-base`**, which Arch does *not* pull in via webkit2gtk — counter-intuitive and easy to miss.
- FLAC is fine on both platforms (Chromium has decoded it for years, `-good` covers WebKitGTK) but keep it out of the preview path anyway, to keep the Linux plugin matrix to one package.

**Autoplay.** WebKit's `RequiresUserGestureForMediaPlayback` default is *false* on WebKitGTK (the settings doc says "This is off by default, so media playback could start automatically"), and pywebview does not change it [S]. So Linux is permissive; Windows is not (§5.3). Do not rely on `autoplay=True` for a consistent product behaviour.

### 5.3 Windows: WebView2

- pywebview 6.2.1 on Windows has exactly one backend without third-party extras: **WinForms + EdgeChromium/WebView2** (CEF exists only via the `cef` extra / `cefpython3`). If the runtime or .NET is missing it **silently loads MSHTML (IE11)** and logs a warning [S]. NiceGUI will then report `Vue failed to load … requires Chrome 89+` after page load — a confusing failure that reaches the user as a blank/broken window [S].
- Runtime detection is a registry check of `Microsoft\EdgeUpdate\Clients\{F3017226-…}` plus .NET `Release >= 394802` (4.6.2); pywebview also supports coreclr fallback since 6.2 (#1791) [S][D].
- pywebview passes `--disable-features=ElasticOverscroll` and, when `ALLOW_FILE_URLS` is on, `--allow-file-access-from-files` as `AdditionalBrowserArguments`; it sets nothing about media or autoplay [S].
- Codec support is the Edge/Chromium stack: WebView2 ships the Edge engine, and Microsoft's own distribution docs describe the Runtime as providing "the same web platform capabilities … as the Stable channel of the Microsoft Edge browser". Unlike vanilla Chromium/CEF builds, the Edge-branded engine carries the proprietary codecs (AAC/H.264), which is why projects that cannot ship CEF with proprietary codecs migrate to WebView2 [D][R]. WebView2 also exposes an autoplay **permission** (`CoreWebView2PermissionKind.Autoplay = 0x9`) and documents an `autoplay-policy` browser flag [D]. **Inference (not played through on a Windows host):** MP3/AAC/WAV/FLAC/Opus all decode; autoplay with sound is gated unless a user gesture or the `autoplay-policy` flag is used.
- pywebview does **not** handle `PermissionRequested`, so the default Chromium autoplay policy applies (`document.play()` from a click works; autoplay on load may not) [S][D].

### 5.4 `file://` URLs

- `webview.settings['ALLOW_FILE_URLS']` is **`True` by default in the 6.2.1 source**, although the published API page still says "Disabled by default" [S][D]. On GTK it maps to `allow_file_access_from_file_urls`; on Windows to `--allow-file-access-from-files` [S].
- That setting governs *file→file* access. A page served from `http://127.0.0.1` still may not load `file:///…`; pywebview's maintainer's answer to exactly this `<audio src="file:///…">` question is *"file:// urls may be blocked by your renderer … the solution is to use relative paths, so that built-in http server is used instead"* (#1219, closed) [R].
- NiceGUI never has a reason to use `file://`: `ui.audio(Path)` already rewrites to an HTTP route [S].

**Verdict: no `file://` anywhere in StemForge. Always serve through NiceGUI's HTTP routes.**

### 5.5 What `<audio>` gives you (and does not)

`ui.audio` renders exactly `<audio controls autoplay muted src>` — no `preload`, no `type`, no volume/rate API. Methods: `seek(seconds)`, `play()`, `pause()` [S].

Implications:

- Fine for **single-track preview** (the v1 scope).
- Not sufficient for **synchronised multi-stem playback** (per-stem `<audio>` elements drift; there is no shared clock) and not sufficient for a waveform/scrub UI. The map already defers waveform/stem mixer, so this is a v1 non-issue — but the UI ticket should not assume `ui.audio` scales to the post-v1 mixer.
- For anything beyond single-file preview, the supported escape hatch is a **custom Vue component** (`ui.element` + `esm=`), which NiceGUI documents and bundles [D]. A custom component can use `<audio>` + `AudioContext`/`MediaElementSource`, or `decodeAudioData` + `AudioBufferSourceNode` for sample-accurate simultaneous starts.
- `preload` can be set today with `.props('preload=none')` if load-on-open is undesirable.

---

## 6. Packaging implications

### 6.1 Windows

- **WebView2 runtime is a hard dependency** and its absence is *silent* (MSHTML fallback). Two supported distribution modes [D]:
  - *Evergreen*: tiny bootstrapper (`MicrosoftEdgeWebview2Setup.exe /silent /install`) or the standalone offline installer; runtime auto-updates; win11 ships it, most win10 have it.
  - *Fixed Version*: download and ship a pinned runtime (>250 MB), point pywebview at it with `webview.settings['WEBVIEW2_RUNTIME_PATH']` (added in pywebview 6.1) [D][S].
- `WebView2Loader.dll` (and the two `Microsoft.Web.WebView2.*.dll`) must ship beside the app. pywebview bundles them under `webview/lib/` and its PyInstaller hook collects `webview/lib` + `webview/js` [S]. NiceGUI documents the classic `Unable to load DLL 'WebView2Loader.dll'` failure and the "move the DLL up one directory" workaround [D].
- **pywebview's PyInstaller hook only handles Windows data files and JS assets** — it bundles nothing for Linux [S]. `nicegui-pack` adds only NiceGUI's own data dir; it does **not** add hidden imports for pywebview [S].
- Recommended shape (matches the map): **portable ZIP (onedir) + Inno Setup installer**, bootstrapper fallback in the installer, and an explicit runtime check in the app (§8 R1). Avoid `--onefile` for start-up time (NiceGUI docs say the same) [D].

### 6.2 Linux — the real risk

Facts:

- pywebview's Linux dependency is **PyGObject + GTK3 + WebKit2 4.1**, and it is *not* installed by `pip install nicegui[native]` (the extra is bare `pywebview`). pywebview's own `gtk` extra pins `PyGObject==3.50.0`, which in practice means "use the distro package" [S][D].
- pywebview's GTK backend hard-requires `Gtk 3.0` and `WebKit2 4.1` (falling back to 4.0) — i.e. **webkit2gtk-4.1**, the GTK3 API [S].
- PyInstaller/Nuitka cannot meaningfully bundle WebKitGTK: no hook exists for it, and WebKit spawns helper processes (`WebKitWebProcess`, `WebKitNetworkProcess`, `WebKitGPUProcess`) at paths fixed at build time [S][R].
- AppImage bundling of WebKitGTK is a documented minefield [R]:
  - `linuxdeploy-plugin-gtk` cannot deploy WebKit2 because the helper executables must live at fixed locations and relocation breaks them (issue #42).
  - Upstream 2026 reports show the two classic failure modes: a partially bundled webkit + GStreamer **ABI/EGL skew** against the host Mesa/GLib, and `AppRun` exporting `GST_PLUGIN_SYSTEM_PATH_1_0` to a directory that was never populated, which **disables GStreamer's default plugin search** and makes WebKit abort on the first audio element. Tauri's own docs warn that the `ugly` plugin set is licence-hostile to redistribute.
  - The AppImage/AppImageKit wiki's own GTK3 recipe requires hand-setting `GI_TYPELIB_PATH`, `GDK_PIXBUF_MODULE_FILE`, `GSETTINGS_SCHEMA_DIR`, `GST_PLUGIN_SCANNER`, `GST_PLUGIN_PATH`, `GST_REGISTRY`, and `chdir` into `APPDIR`.

Reading for StemForge's stated plan ("Linux AppImage + tar.gz, glibc 2.35+"):

| Strategy | Verdict |
|---|---|
| AppImage that **bundles** webkit2gtk + GStreamer | ❌ high risk of "window never appears" / "no audio" on hosts newer than the build image |
| AppImage that **bundles nothing** and requires host `webkit2gtk-4.1` + `gst-plugins-good` | ⚠️ workable, but then the AppImage's only advantage over tar.gz is the single file — and you must be careful **not** to let the bundler set `GST_PLUGIN_SYSTEM_PATH*` |
| **tar.gz + launcher/install script that declares and checks deps** | ✅ lowest risk, honest about the platform |
| Flatpak / deb / rpm | out of scope per the map (would actually be the robust answer) |

Given glibc 2.35+ (Ubuntu 22.04+), every supported host has webkit2gtk-4.1 ≥ 2.50 (measured: jammy 2.50.4, noble 2.52.6, trixie 2.52.6) — so a host-provided webkit is a defensible v1 contract. Recommend making **tar.gz the primary Linux artifact** and AppImage a thin wrapper over the same bundle, with a preflight check that verifies the typelib and the GStreamer elements before showing the window.

### 6.3 Nuitka

- NiceGUI documents Nuitka with two required flags: `--include-package=nicegui --include-package-data=nicegui` (PEP 562 lazy imports defeat Nuitka's static analysis); native mode "works the same as with PyInstaller" [D].
- Same caveat: no Linux webkit bundling. `--linux-onefile-icon` is irrelevant to the webkit problem.
- Best practice per pywebview: `--nofollow-import-to` the GUI toolkits you do not use (e.g. `PyQt5`, `PySide6`) so a Qt installed on the build machine does not bloat or shadow the bundle [D].

---

## 7. Supported vs unsupported paths (consolidated)

**Supported, verified**

1. `ui.run(native=True)` on Linux with GTK3 + webkit2gtk-4.1 + PyGObject + `gst-plugins-good`.
2. `ui.run(native=True)` on Windows 10/11 x64 with WebView2 Runtime ≥ 86 and .NET Framework ≥ 4.6.2 (or coreclr).
3. `app.native.main_window.create_file_dialog` for OPEN / FOLDER / SAVE, including multi-select and filters.
4. Local media over `http://127.0.0.1` with `Range: bytes=N-` seeking (measured 206 responses).
5. Window events `shown/loaded/minimized/maximized/restored/resized/moved/closed/drop`.
6. Drag-and-drop of files with absolute paths (`app.native.on('drop')`).
7. PyInstaller / Nuitka frozen builds, with `freeze_support()` and `reload=False`.

**Unsupported or unsafe**

1. `file://` media URLs from an HTTP-served page. Use NiceGUI's routes.
2. Vetoing window close (`closing` not bridged).
3. Multiple native windows; access to the real pywebview `Window`/`native` handle.
4. Qt/WebEngine as the shipping Linux backend (extra deps, `--no-sandbox` hacks on Arch/Manjaro/NixOS inside pywebview, unverified codec build flags) — fallback only, not a plan.
5. Bundling WebKitGTK/GStreamer into a Linux AppImage.
6. The non-pywebview fallback backend (#6264) — not in 4.0.0.
7. macOS — pywebview supports it, but it is out of scope for this map.

---

## 8. Recommended approach for StemForge

### 8.1 Shell

```python
from multiprocessing import freeze_support
from nicegui import app, ui

app.native.window_args.update(resizable=True, min_size=(960, 640))
app.native.start_args['private_mode'] = False      # keep cookies/localStorage across runs
app.native.start_args['storage_path'] = str(APP_DIR / 'webview')  # optional, explicit
app.native.settings['ALLOW_DOWNLOADS'] = False

def root() -> None: ...

if __name__ == '__main__':
    freeze_support()                                # first statement
    ui.run(root, native=True, reload=False, title='StemForge',
           window_size=(1280, 800), storage_secret=...)
```

- `private_mode=False` matters: pywebview defaults to private mode and, on GTK, **disables HTML5 local storage** in that mode [S]. NiceGUI's `app.storage.user` is keyed by a signed session cookie [S], so with private mode it resets on every launch. Either turn private mode off or keep preferences in your own SQLite/JSON store.
- Do **not** rely on `app.storage.user` alone for "recent files" if private mode stays on.

### 8.2 Backend assertion (Windows)

Fail fast and loudly instead of showing an IE11 blank window. Preferred check: read the same registry values pywebview reads, *before* importing anything Windows-GUI-related in the server process [S][D]:

```python
# Windows only, before ui.run(native=True)
import winreg
RUNTIME_KEY = r'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
def webview2_installed() -> bool:
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, RUNTIME_KEY),
                      (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')):
        try:
            with winreg.OpenKey(root, sub) as k:
                version, _ = winreg.QueryValueEx(k, 'pv')
                return version not in ('', '0.0.0.0')
        except OSError:
            continue
    return False
```

If you prefer pywebview's own verdict, `webview.platforms.winforms.renderer` is set at import time by its probe — but importing that module pulls in WinForms, so only do it inside the window child. Either way, ship the Evergreen bootstrapper in the installer and keep the check as belt-and-braces.

### 8.3 Dialogs

- One module, `dialogs.py`, with constants for the *exact* filter strings and a `open_audio_files()` / `save_as(default_name, fmt)` pair.
- Always append the extension yourself when saving.
- Catch `ValueError` from filter parsing in tests, not at runtime.
- Never open a dialog from a place that can race window close; disable UI while a dialog is open (it is modal but the app keeps running).

### 8.4 Audio preview

1. **Cache transcodes**: every imported track gets one `preview.mp3` (e.g. 44.1 kHz stereo, 128–192 kbps, loudness-normalised if you like) in the session cache. The map already decides this; the research confirms it is the least fragile option.
2. **Serve the cache directory once** with `app.add_media_files('/preview', cache_dir)` and use `/preview/<id>.mp3` in `ui.audio`. Do not instantiate `ui.audio(Path)` per library row (route leak).
3. **Playback**: `ui.audio` for v1 (single track, `seek()`, `play()`, `pause()`). Add `.props('preload=metadata')` or `none` as needed. Accept that `autoplay` differs by OS; always start playback from a click.
4. **Waveform (post-v1)**: compute peaks in Python from the canonical WAV and render with `ui.echart`/canvas. Do not compute waveforms in JS (`decodeAudioData`) — that reintroduces the exact codec matrix you are trying to avoid, and costs a full float32 buffer per track.
5. **Preflight check on Linux**: at startup, verify `Gst.ElementFactory.find('mpg123audiodec')` (or `wavparse`) and `gi.require_version('WebKit2', '4.1')`; if missing, show an actionable message ("install gst-plugins-good") instead of a silent player. This is a 20-line check that converts the most likely Linux support ticket into a self-service fix.

### 8.5 Distribution

- **Windows**: onedir PyInstaller build inside Inno Setup; Evergreen bootstrapper as a prerequisite; assert EdgeChromium; ship `WebView2Loader.dll` (pywebview hook handles it, verify in CI).
- **Linux**: tar.gz is the primary artifact; document `webkit2gtk-4.1`, `python3-gi`/`gobject-introspection`, `gst-plugins-base`, `gst-plugins-good` (and `gst-libav` if AAC is ever needed). If an AppImage is still wanted, build it as "no webkit, no GStreamer bundled" and make the AppRun check for the host typelib — and make sure nothing sets `GST_PLUGIN_SYSTEM_PATH*`.

---

## 9. Risks

| # | Risk | Evidence | Severity | Mitigation |
|---|---|---|---|---|
| R1 | Windows silently uses MSHTML/IE11 when WebView2 is absent → blank/broken window, cryptic Vue error | pywebview winforms source `_is_chromium` → `mshtml` fallback; NiceGUI `_warn_if_esm_unsupported` [S] | **High** | Installer bootstrapper + registry preflight (§8.2) + clear error dialog |
| R2 | Linux host lacks `gst-plugins-good` → no preview at all (Arch optdepend; Debian hard dep) | Arch `webkit2gtk-4.1` optdepends; Debian `Depends` [S]; measured element→package map [M] | **High** | Startup GStreamer preflight + documented/checked deps |
| R3 | Windows `create_file_dialog` runs on a worker thread with no `Invoke`; can deadlock the GUI under activation load | pywebview #1823 (6.2.1, closed stale, not fixed) [R]; source `dialog.ShowDialog(i)` [S] | Medium | Keep dialogs short-lived; disable UI during dialog; track upstream; consider a `window.native`-based `Invoke` workaround if it appears |
| R4 | No close-veto: user can lose in-progress work (separation/render) by closing the window | `closing` not bridged [S] | Medium | Autosave/checkpoint on every state change; disable destructive close paths; document it |
| R5 | AppImage with bundled webkit/GStreamer fails on newer hosts (EGL/ABI skew, GST path poisoning, helper process paths) | linuxdeploy-plugin-gtk #42; tauri #15665; screenpipe #5529; claudette #539 [R] | **High** | Ship tar.gz as primary; AppImage without webkit; never export `GST_PLUGIN_SYSTEM_PATH*` |
| R6 | Preview codec gap: AAC (`-bad`/`libav`) and Opus (`gst-plugins-base` on Arch) are not guaranteed | Debian/Ubuntu rules, Arch optdepends, measured map [S][M] | Medium | Cache MP3 previews only; never feed `.m4a`/`.opus` to `<audio>` |
| R7 | `HEAD` on media route returns 405; suffix ranges return 416 | measured [M] | Low | Test on real WebKitGTK/WebView2 (Q1); add HEAD route if needed |
| R8 | `private_mode=True` default ⇒ `app.storage.user` resets each launch | pywebview API docs + GTK source disables local storage in private mode [D][S] | Medium | `private_mode=False` or own persistence |
| R9 | Route leak from per-file `ui.audio(Path)` in a track list | `add_media_file` registers a route per call [S] | Low | `add_media_files` once |
| R10 | Packaged windowed build hangs on exit if stdio is `None` | NiceGUI #6392, fixed and present in 4.0.0 (`core.stop_and_exit`) [S] | Resolved | Keep ≥ 4.0.0 |
| R11 | pywebview pinned `PyGObject==3.50.0` in its `gtk` extra means `pip install pywebview[gtk]` is not a viable install path on modern distros | pywebview METADATA [S] | Medium | Depend on distro `python3-gi`; document per-distro install lines |

---

## 10. Open questions

1. **Do real engines issue `HEAD` or suffix ranges for `<audio>`?** Our route answers 405/416 for those. Needs a 30-minute test on GTK and WebView2 with `app.native.start_args['debug']=True` and the network panel / `webkit` inspector.
2. **Does `autoplay=True` with sound work on each OS?** WebKitGTK default says yes, Chromium says no-without-gesture. Test, then codify "playback always starts from a click".
3. **Does `app.native.on('drop')` deliver full paths on GTK** for multi-file drops, and does it work when the window is frameless? Not covered by any test here.
4. **Multi-stem synchronised playback**: single `<audio>` per stem vs. one `AudioContext` with `AudioBufferSourceNode` started at the same `currentTime`. Sample-accurate sync needs the latter; memory needs the former. Decide in the UI prototype ticket, not here.
5. **AppImage decision**: is a host-dependent AppImage acceptable to the maintainer, or does tar.gz-only ship in v1? This changes the release workflow, not the app.
6. **Windows Fixed Version WebView2**: worth 250 MB to remove the evergreen dependency? Probably not for v1; revisit if enterprise deployment appears.
7. **Nuitka vs PyInstaller on Linux with `gi`**: PyInstaller's `gi.repository` hooks collect typelibs from the build machine; Nuitka's story for `gi` is less documented. A 1-hour spike would settle it, and it affects CI build images.
8. **Does `ui.page_title()` + `app.native.main_window.set_title()` interact badly** with page navigation (per-page titles)? Source shows `page_title` pushes to the native window when present [S]; untested.

---

## 11. Citations

**NiceGUI**

1. NiceGUI 4.0.0 on PyPI — https://pypi.org/project/nicegui/4.0.0/
2. NiceGUI v4.0.0 release notes — https://github.com/zauberzeug/nicegui/releases/tag/v4.0.0
3. NiceGUI docs, "Configuration & Deployment" → Native Mode, Native Window Events, Packaging, Packaging with Native Mode — https://nicegui.io/documentation/section_configuration_deployment
4. NiceGUI issue #6392, "Process never exits after closing the native window in a console-less build" — https://github.com/zauberzeug/nicegui/issues/6392
5. NiceGUI issue #6381, "Native mode + reload=True: pywebview 6.x window dies on hot-reload (Windows)" — https://github.com/zauberzeug/nicegui/issues/6381
6. NiceGUI issue #6264, "Add a fallback backend for native mode when pywebview is absent" (closed, not in 4.0.0) — https://github.com/zauberzeug/nicegui/issues/6264
7. NiceGUI source (4.0.0 wheel): `nicegui/native/native_mode.py`, `native.py`, `native_config.py`, `ui_run.py`, `server.py`, `core.py`, `app/app.py`, `app/range_response.py`, `elements/audio.py`, `elements/audio.js`

**pywebview**

8. pywebview 6.2.1 on PyPI — https://pypi.org/project/pywebview/6.2.1/
9. pywebview API reference — https://pywebview.flowrl.com/api/
10. pywebview changelog (6.0 → 6.2.1) — https://pywebview.flowrl.com/changelog
11. pywebview guide, "Web engine" — https://pywebview.flowrl.com/guide/web_engine.html
12. pywebview guide, "Installation" — https://pywebview.flowrl.com/guide/installation.html
13. pywebview guide, "Freezing" — https://pywebview.flowrl.com/guide/freezing.html
14. pywebview issue #1823, cross-thread `on_top`/`create_file_dialog` on EdgeChromium — https://github.com/r0x0r/pywebview/issues/1823
15. pywebview issue #1219, "file:/// Not allowed to load local resource?" — https://github.com/r0x0r/pywebview/issues/1219
16. pywebview source (6.2.1 wheel): `webview/__init__.py`, `window.py`, `util.py`, `guilib.py`, `platforms/gtk.py`, `platforms/qt.py`, `platforms/winforms.py`, `platforms/edgechromium.py`, `__pyinstaller/hook-webview.py`

**WebKitGTK / GStreamer**

17. WebKitGTK 2.54.2 release — https://webkitgtk.org/2026/10/23/webkitgtk2.54.2-released.html
18. WebKit `WebKitSettings.cpp` (`media-playback-requires-user-gesture` docs/default) — https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/API/glib/WebKitSettings.cpp
19. WebKit `MediaPlayerPrivateGStreamer.cpp` (playbin/uridecodebin pipeline) — https://github.com/WebKit/WebKit/blob/main/Source/WebCore/platform/graphics/gstreamer/MediaPlayerPrivateGStreamer.cpp
20. WebKit `GStreamerChecks.cmake` (GStreamer ≥ 1.20) — https://github.com/WebKit/WebKit/blob/main/Source/cmake/GStreamerChecks.cmake
21. Arch `webkit2gtk-4.1` package metadata (depends/optdepends) — https://archlinux.org/packages/extra/x86_64/webkit2gtk-4.1/
22. Debian `webkit2gtk` 2.54.2-2 `debian/control` — https://sources.debian.org/src/webkit2gtk/2.54.2-2/debian/control/
23. Debian `webkit2gtk` `debian/rules` (`${gst:Recommends}` / Ubuntu Suggests) — https://sources.debian.org/src/webkit2gtk/2.54.2-2/debian/rules/
24. Ubuntu noble `libwebkit2gtk-4.1-0` — https://packages.ubuntu.com/noble/libwebkit2gtk-4.1-0
25. Ubuntu jammy `libwebkit2gtk-4.1-0` — https://packages.ubuntu.com/jammy/libwebkit2gtk-4.1-0
26. GStreamer, installing on Linux (plugin sets, licensing) — https://gstreamer.freedesktop.org/documentation/installing/on-linux.html
27. GTK3 `GtkFileChooserDialog` — https://docs.gtk.org/gtk3/class.FileChooserDialog.html

**Windows / WebView2**

28. Distribute your app and the WebView2 Runtime (Evergreen vs Fixed Version, registry detection, bootstrapper, `WebView2Loader.dll`) — https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution
29. WebView2 browser flags (incl. `autoplay-policy`, `allow-file-access-from-files`) — https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/webview-features-flags
30. `CoreWebView2PermissionKind` (incl. `Autoplay = 0x9`) — https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/winrt/microsoft_web_webview2_core/corewebview2permissionkind
31. `FileDialog.AddExtension` (default `true`; extension from selected filter + `DefaultExt`) — https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.filedialog.addextension
32. `FileDialog.DefaultExt` (default empty) — https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.filedialog.defaultext

**Linux desktop packaging**

33. AppImage/AppImageKit wiki, "Bundling GTK3 apps" — https://github.com/AppImage/AppImageKit/wiki/Bundling-GTK3-apps
34. linuxdeploy-plugin-gtk issue #42, "Can't deploy any app that uses Webkit2" — https://github.com/linuxdeploy/linuxdeploy-plugin-gtk/issues/42
35. Tauri issue #15665, AppImages over-bundling libwayland/glib/gstreamer, stale `GST_PLUGIN_SYSTEM_PATH` in AppRun — https://github.com/tauri-apps/tauri/issues/15665
36. Tauri docs, AppImage / `bundleMediaFramework` — https://v2.tauri.app/distribute/appimage/
37. screenpipe issue #5529, AppRun exports `GST_PLUGIN_SYSTEM_PATH` with no plugins bundled — https://github.com/screenpipe/screenpipe/issues/5529
38. claudette issue #539, internally inconsistent bundled webkit+gstreamer in AppImage — https://github.com/utensils/claudette/issues/539

**Reference on codecs (secondary, for container/codec naming only)**

39. MDN, "Web audio codec guide" — https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Formats/Audio_codecs

---

## Appendix A — claims that were *not* verified

- No WebKitGTK/GTK window was opened on this host (`webkit2gtk-4.1` and `PyGObject` typelibs absent; only `gi` core and PyQt5 without WebEngine are installed). All GTK behaviour above is source/doc-derived.
- No Windows host was available. WebView2 codec behaviour is inferred from "WebView2 = Edge engine" plus Microsoft's distribution/flags/permission docs; it was not played through.
- Dialog behaviour (GTK `FileChooserDialog`, WinForms `ShowDialog`) is source-derived; no dialog was shown.
- `app.native.on('drop')` path fidelity on GTK is documented in pywebview but untested here.

## Appendix B — measured evidence

Environment: Archcraft (Arch-based), kernel 7.2.14, Python 3.14, `nicegui==4.0.0` in `/tmp/opencode/stemforge-research/venv`, FastAPI 0.163.0, Starlette 1.10.0, GStreamer 1.28.9.

**B.1 Media route behaviour** (FastAPI `TestClient` against `nicegui.app`; same results for `add_media_file` and `add_media_files`, both GET-only):

```
GET full                                   200  content-length 100000  accept-ranges bytes  content-type audio/x-wav
GET Range: bytes=1000-                     206  content-range bytes 1000-99999/100000  content-length 99000
GET Range: bytes=-500 (suffix)             416  content-range bytes */100000
GET Range: bytes=0-1                       206  content-range bytes 0-1/100000  content-length 2
GET Range: bytes=999999-                   416  content-range bytes */100000
GET Range: bytes=0-1,3-4 (multi)           416  content-range bytes */100000
HEAD full                                  405  application/json
```

**B.2 MIME guessing (Linux, Python 3.14 `mimetypes`)**

```
.wav -> audio/x-wav    .mp3 -> audio/mpeg    .m4a -> audio/mp4    .aac -> audio/aac
.flac -> audio/x-flac  .ogg -> audio/ogg     .opus -> audio/ogg   .aiff -> audio/x-aiff
```

**B.3 GStreamer element ownership (GStreamer 1.28.9, `pacman -Qo` on each element's shared object)**

```
wavparse       gst-plugins-good       flacdec        gst-plugins-good
mpg123audiodec gst-plugins-good       flacparse      gst-plugins-good
mpegaudioparse gst-plugins-good       aacparse       gst-plugins-good
opusdec        gst-plugins-base       avdec_aac      gst-libav
oggdemux       gst-plugins-base       faad           gst-plugins-bad
audioresample  gst-plugins-base-libs  autoaudiosink  gst-plugins-good
```

**B.4 pywebview file-filter grammar** — regex from `webview/util.py`, exercised against candidate strings (see §4.2 table).
