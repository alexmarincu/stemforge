# StemForge on Linux

This Bundle is the portable Linux build: the frozen StemForge application, its
vendored tools, and the notices. Extract it anywhere; nothing is installed
system-wide and no root access is needed.

```sh
tar -xzf StemForge-1.0.0-linux-x86_64.tar.gz
cd StemForge-1.0.0-linux-x86_64
./run.sh
```

The `.tar.zst` holds the same tree; extract it with
`tar --zstd -xf StemForge-1.0.0-linux-x86_64.tar.zst` if `zstd` is available.
The `.tar.gz` is the canonical artifact.

## What the Bundle contains

```text
StemForge                  the frozen launcher
_internal/                 CPython, torch, Demucs, NiceGUI, yt-dlp, assets
vendor/ffmpeg/             ffmpeg and ffprobe
vendor/deno/               Deno for yt-dlp's JavaScript runtime
vendor/rubberband/         Rubber Band CLI and its co-located libsndfile
LICENSES/                  third-party licence texts
LICENSE
THIRD_PARTY_NOTICES.md
SOURCE_OFFER.md
README.md                  this file
run.sh                     the launcher
```

`vendor/` and the licence tree sit beside `_internal/`, not inside it, so the
vendored tools and their notices are visible and replaceable. The Bundle root is
the directory that contains `run.sh`.

## Requirements

StemForge runs on 64-bit Linux with glibc 2.35 or newer (Ubuntu 22.04, Debian 12,
Fedora current, Arch current). The **native window** needs the host GTK 3,
WebKitGTK 4.1 (2.44 or newer), Soup 3, and GStreamer libraries. StemForge does
not bundle them; the AppImage is deliberately a thin wrapper.

Debian / Ubuntu:

```sh
sudo apt install libgtk-3-0 libwebkit2gtk-4.1-0 gir1.2-webkit2-4.1 \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

Fedora:

```sh
sudo dnf install gtk3 webkit2gtk4.1 gstreamer1-plugins-base gstreamer1-plugins-good
```

Arch:

```sh
sudo pacman -S gtk3 webkit2gtk-4.1 gst-plugins-base gst-plugins-good
```

`run.sh` runs `--preflight` first. If GTK, WebKitGTK, or GStreamer is missing,
it starts the same application in **browser mode** and prints a clear message;
open the URL it prints (normally `http://127.0.0.1:8080`). To force browser mode
on a fully provisioned host, set `STEMFORGE_BROWSER=1`:

```sh
STEMFORGE_BROWSER=1 ./run.sh
```

Without the GStreamer preview stack the app can still separate and export, but
in-window playback is unavailable. Missing GTK/WebKitGTK means no native window
at all, so browser mode is the fallback.

On WebKitGTK 2.52 and newer with an NVIDIA driver, the upstream workarounds can
avoid a blank or flickering window:

```sh
WEBKIT_SKIA_ENABLE_CPU_RENDERING=1 WEBKIT_DMABUF_RENDERER_FORCE_SHM=1 ./run.sh
```

Wayland stays native; do not set `GDK_BACKEND=x11` unless you are troubleshooting
an X11-only driver.

## AppImage

```sh
chmod +x StemForge-1.0.0-linux-x86_64.AppImage
./StemForge-1.0.0-linux-x86_64.AppImage
```

The AppImage is a thin zstd wrapper around the same onedir Bundle. It does not
carry GTK, WebKitGTK, or GStreamer, so the native-window requirements above still
apply and `run.sh` still falls back to browser mode when they are absent.

AppImages need FUSE 2 on the host. If FUSE is unavailable (containers, some
locked-down distributions), extract and run without mounting:

```sh
./StemForge-1.0.0-linux-x86_64.AppImage --appimage-extract-and-run
```

The tarball is the primary artifact and never needs FUSE.

## Data locations

StemForge follows the XDG base-directory conventions:

| What | Default path |
| --- | --- |
| Data (recent files, workspace, webview storage, models) | `~/.local/share/stemforge` |
| Models | `~/.local/share/stemforge/models` |
| Settings and the single-instance lock | `~/.config/stemforge` |
| Cache | `~/.cache/stemforge` |
| Logs | `~/.local/state/stemforge/log` |

Model weights are **not** in this Bundle. StemForge downloads the pinned
`htdemucs` weights on first Separation, after an explicit consent screen, and
verifies the SHA-256. The download is the only required network use; local
import, pitch, separation with an installed model, and export work offline.

Set `STEMFORGE_HOME` to keep everything in one portable directory:

```sh
STEMFORGE_HOME=/media/usb/stemforge ./run.sh
# data/  config/  cache/  logs/  under that directory
```

`STEMFORGE_HOME` is intended for portable installs, testing, and CI. It
overrides every XDG path above; the models live in `$STEMFORGE_HOME/data/models`
and the webview storage in `$STEMFORGE_HOME/data/webview`.

## Diagnostics and smoke tests

Run these from the extracted Bundle:

```sh
./run.sh --preflight             # human-readable dependency report; --json too
./run.sh --self-test             # imports the runtime and executes the vendor tools
./run.sh --smoke-test            # boots the local server and polls it
./run.sh --install-model --accept-model-licence
./run.sh --smoke-separation      # one-second synthetic separation; needs the model
```

Exit codes for `--preflight`: `0` ok, `3` native window unavailable, `4` audio
preview unavailable, `6` storage not writable, `7` unsupported platform, `8`
bundle damaged. `run.sh` falls back to browser mode for `3` and `4`; the other
codes stop the launch.

From the source checkout, the same checks plus archive verification are:

```sh
bash packaging/linux/smoke-test.sh --bundle dist/StemForge-1.0.0-linux-x86_64 \
    --with-separation --appimage dist/StemForge-1.0.0-linux-x86_64.AppImage
python3 packaging/linux/verify_bundle.py --bundle dist/StemForge-1.0.0-linux-x86_64 \
    --archive dist/StemForge-1.0.0-linux-x86_64.tar.gz \
    --archive dist/StemForge-1.0.0-linux-x86_64.tar.zst \
    --archive dist/StemForge-1.0.0-linux-x86_64.AppImage
```

## Unsigned builds and hashes

v1 release artifacts are **unsigned**: there is no code-signing certificate for
StemForge 1.0.0. Download from the official release page, not a mirror, and
compare each file with the published `SHA256SUMS`:

```sh
sha256sum -c SHA256SUMS
```

A mismatched hash means the file is corrupt or not ours; do not run it.

## Licence and source

StemForge is GPL-3.0-only. Third-party components and their licences are listed
in `THIRD_PARTY_NOTICES.md`; the full texts are in `LICENSES/`. `SOURCE_OFFER.md`
explains where the corresponding source for the GPL components is available.
The `LICENSES/` tree is the single copy for this Bundle, and the About dialog
points here.
