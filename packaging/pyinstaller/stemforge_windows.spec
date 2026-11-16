# -*- mode: python ; coding: utf-8 -*-
"""Windows onedir spec: ``console=False``, the icon, and the WebView2 backends.

Ticket 10 §1–§3 and §6. The launcher is ``StemForge.exe``; the frozen payload
lives in ``_internal\\`` and ``packaging\\windows\\build.ps1`` adds ``vendor\\``
and the licence tree beside it. torch and Demucs are not dependencies yet:
ticket 13 adds their hidden imports, the 168 MB trim, and ``version_info.txt``.
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
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.mshtml",
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
