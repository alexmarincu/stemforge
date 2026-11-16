# -*- mode: python ; coding: utf-8 -*-
"""Linux onedir spec: NiceGUI as data, the Bundle root beside ``_internal``.

Ticket 10 §1–§3. PyInstaller 6.22.3 places the frozen payload in
``dist/StemForge/_internal``; ``packaging/linux/build.sh`` adds ``vendor/``,
``run.sh``, and the licence tree beside the launcher. torch and Demucs are not
dependencies yet: ticket 13 adds their hidden imports and the 168 MB trim.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
SRC = ROOT / "src" / "stemforge"

datas = [
    (str(SRC / "assets"), "assets"),
    (str(SRC / "models" / "models.lock.json"), "models"),
    *collect_data_files("nicegui"),
]

hiddenimports = [
    # smoke.py imports these by name so the heavy stack stays off module scope.
    "webview",
    "webview.platforms.gtk",
    "stemforge.ui.page",
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
