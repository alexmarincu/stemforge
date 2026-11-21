# -*- mode: python ; coding: utf-8 -*-
"""Linux onedir spec: NiceGUI as data, the Bundle root beside ``_internal``.

Ticket 10 §1–§3 and ticket 13. PyInstaller 6.22.3 places the frozen payload in
``dist/StemForge/_internal``; ``packaging/linux/build.sh`` adds ``vendor/``,
``run.sh``, and the licence tree beside the launcher. The Separation worker's
torch/Demucs imports happen inside ``run()``, so they are listed as hidden
imports; ``torch/test`` and ``torch/bin`` are trimmed (162 MB measured).

PyGObject is part of the payload because the frozen CPython cannot borrow the
host's ``gi`` (it is built for the host Python, not 3.13). The GTK/WebKit
typelibs and shared libraries stay host-provided, so ``libstdc++.so.6`` and
``libgcc_s.so.1`` are deliberately not bundled: host WebKitGTK/ICU must resolve
their C++ ABI against the same runtime as the rest of the desktop stack.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src" / "stemforge"

datas = [
    (str(SRC / "assets"), "assets"),
    (str(SRC / "models" / "models.lock.json"), "models"),
    # The frozen preflight verifies vendor/ against this lock (ticket 19).
    (str(ROOT / "packaging" / "vendor.lock.json"), "."),
    *collect_data_files("nicegui"),
    # demucs/remote/files.txt is read by the legacy repo fallback; the HF path
    # used by the worker needs no network data but the package is tiny.
    *collect_data_files("demucs"),
]

hiddenimports = [
    # The GTK bridge for pywebview. PyGObject is built for the frozen CPython
    # and shipped in _internal/; the typelibs themselves stay on the host.
    "gi",
    "gi.repository",
    *(name for name in collect_submodules("gi") if name not in {"gi", "gi.repository"}),
    # smoke.py imports these by name so the heavy stack stays off module scope.
    "webview",
    "webview.platforms.gtk",
    "stemforge.ui.page",
    # The worker imports torch/Demucs inside run(); a call-time import is
    # invisible to static analysis and research 04 measured a 150 MB bundle
    # when these were omitted (ticket 13).
    "torch",
    "demucs.api",
    "demucs.apply",
    "demucs.audio",
    "demucs.hdemucs",
    "demucs.hf",
    "demucs.htdemucs",
    "demucs.pretrained",
    "demucs.repo",
    "demucs.spec",
    "demucs.states",
    "demucs.transformer",
    "demucs.utils",
    "demucs.wiener",
    "stemforge.workers.separation",
    # The pytest/User-simulation helpers pull pytest and mypy into the
    # payload; the shipped app never imports them.
    *(name for name in collect_submodules("nicegui") if not name.startswith("nicegui.testing")),
]

a = Analysis(
    [str(SRC / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    # The no-op gi.repository hooks keep GTK/WebKit/GStreamer typelibs and
    # shared libraries on the host (ADR 0011); only PyGObject itself ships.
    hookspath=[str(ROOT / "packaging" / "pyinstaller" / "hooks")],
    hooksconfig={"gi": {"icons": [], "themes": [], "languages": []}},
    runtime_hooks=[str(ROOT / "packaging" / "pyinstaller" / "runtime-hooks" / "rthook_stemforge_env.py")],
    # mypy is a pydantic hook side effect and is only needed at type-check time.
    # pycairo is a build dependency of PyGObject; StemForge never calls
    # gi.require_foreign("cairo"), so the wheel stays out of the payload.
    excludes=["tkinter", "mypy", "pydantic.mypy", "pydantic.v1.mypy", "cairo"],
    noarchive=False,
    optimize=0,
)

# Ticket 13 / ticket 12: an inference-only bundle does not need torch's test
# suite or its command-line helpers (~162 MB measured). ``import torch`` refuses
# to load unless torch_shm_manager exists, so that one file stays.
_TORCH_KEEP_BIN = {"torch_shm_manager", "torch_shm_manager.exe"}


def _keep(entry):
    destination = entry[0].replace("\\", "/")
    if destination in {"torch/test", "torch/bin"}:
        return False
    if destination.startswith(("torch/test/", "torch/bin/")):
        return destination.rsplit("/", 1)[-1] in _TORCH_KEEP_BIN
    return True


a.datas = [entry for entry in a.datas if _keep(entry)]
a.binaries = [entry for entry in a.binaries if _keep(entry)]

# These two are the host's compiler runtime, not app payload. Bundling them
# makes the frozen process interpose an older libstdc++ on host WebKitGTK/ICU
# (Arch currently needs CXXABI_1.3.15 for ICU 78, which GCC 12's copy lacks).
# The app's own binaries need at most GLIBCXX_3.4.29/CXXABI_1.3.11, and every
# supported host (glibc 2.35+ distributions) provides at least that.
_HOST_RUNTIME_LIBRARIES = {"libstdc++.so.6", "libgcc_s.so.1"}
a.binaries = [
    entry
    for entry in a.binaries
    if entry[0].replace("\\", "/").rsplit("/", 1)[-1] not in _HOST_RUNTIME_LIBRARIES
]
# Everything else under /lib and /usr/lib is the host platform runtime: glibc,
# the GTK/WebKit/GStreamer stack, X11, OpenSSL, expat, and their transitive
# dependencies. Keep only the app payload copied from the build venv and the
# vendored tools. PyInstaller always keeps *python* and lib-dynload binaries.
a.exclude_system_libraries()

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StemForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SRC / "assets" / "stemforge.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StemForge",
)
