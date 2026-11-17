# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir spec: ``console=False``, the icon, and the WebView2 backends.

Ticket 10 §1–§3 and §6, ticket 13. The launcher is ``StemForge.exe``; the frozen
payload lives in ``_internal\\`` and ``packaging\\windows\\build.ps1`` adds
``vendor\\`` and the licence tree beside it. The Separation worker's torch/Demucs
imports happen inside ``run()``, so they are listed as hidden imports;
``torch/test`` and ``torch/bin`` are trimmed (162 MB measured).
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
    # smoke.py imports these by name so the heavy stack stays off module scope.
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.mshtml",
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
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "pyinstaller" / "runtime-hooks" / "rthook_stemforge_env.py")],
    # mypy is a pydantic hook side effect and is only needed at type-check time.
    excludes=["tkinter", "mypy", "pydantic.mypy", "pydantic.v1.mypy"],
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
    console=False,
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
