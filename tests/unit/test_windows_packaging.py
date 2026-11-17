"""Windows packaging contracts (ticket 21).

The Windows runner is the only place the build, Inno compiler, and real install
can run. These unit tests keep the Linux job honest about the parts that are
text and pure-Python: the Inno directives, the PyInstaller spec facts, the
PowerShell entry points, the bundle budgets, the vendor and licence rules, and
the SHA-256 manifest.

Expected values come from ticket 10 §6, ticket 21's done-when, and the pinned
values in CONTEXT.md; they are not read back from the scripts under test.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WINDOWS = PROJECT_ROOT / "packaging" / "windows"
ISCC_FILE = WINDOWS / "stemforge.iss"
SPEC_FILE = PROJECT_ROOT / "packaging" / "pyinstaller" / "stemforge_windows.spec"
BUILD_PS1 = WINDOWS / "build.ps1"
PACKAGE_PS1 = WINDOWS / "package.ps1"
SMOKE_PS1 = WINDOWS / "smoke-test.ps1"
VENDOR_PS1 = PROJECT_ROOT / "scripts" / "vendor-fetch.ps1"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "windows-bundle.yml"
WEBVIEW2_LOCK = WINDOWS / "webview2.lock.json"
MIB = 1024 * 1024


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _load("stemforge_verify_bundle", WINDOWS / "verify_bundle.py")
SUMS = _load("stemforge_sha256sums", WINDOWS / "sha256sums.py")
VERSION = _load("stemforge_version_info", WINDOWS / "version_info.py")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, name: str) -> list[str]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().lower() == f"[{name.lower()}]":
            start = index + 1
            break
    if start is None:
        return []
    section: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("[") and line.strip().endswith("]"):
            break
        section.append(line)
    return section


def _directives(lines: list[str]) -> dict[str, str]:
    directives: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        directives[key.strip()] = value.strip()
    return directives


def _has_line(lines: list[str], needle: str) -> bool:
    return any(needle.lower() in line.lower() for line in lines)


# --- Inno Setup -------------------------------------------------------------


def test_inno_installer_has_the_required_per_user_directives() -> None:
    setup = _directives(_section(_text(ISCC_FILE), "Setup"))

    assert setup["MinVersion"] == "10.0.19045"
    assert setup["PrivilegesRequired"] == "lowest"
    assert setup["DefaultDirName"] == r"{localappdata}\Programs\StemForge"
    assert setup["ArchitecturesInstallIn64BitMode"] == "x64compatible"
    assert setup["ArchitecturesAllowed"] == "x64compatible"
    assert setup["SetupIconFile"] == r"..\..\src\stemforge\assets\stemforge.ico"
    assert setup["OutputBaseFilename"] == "StemForge-{#Version}-win64-setup"
    assert setup["UninstallDisplayIcon"] == r"{app}\StemForge.exe"
    assert setup["Compression"] == "lzma2/max"
    assert setup["SolidCompression"] == "yes"
    assert setup["CloseApplications"] == "yes"
    assert setup["AppId"] == "{{7E2F6C2A-6D1B-4A57-9B6D-2B0E4A2F6C21}"


def test_inno_installer_has_no_legacy_web_engine_and_asserts_edge_chromium() -> None:
    text = _text(ISCC_FILE).lower()

    assert "edgechromium" in text
    assert "mshtml" not in text


def test_inno_webview2_bootstrapper_is_embedded_dontcopy_and_conditional() -> None:
    files = _section(_text(ISCC_FILE), "Files")
    code = _section(_text(ISCC_FILE), "Code")

    assert any("{#WebView2Installer}" in line for line in files)
    assert any("dontcopy" in line for line in files)
    assert any("{tmp}" in line for line in files)
    assert any("MicrosoftEdgeWebview2Setup.exe" in line for line in code)
    assert any("ExtractTemporaryFile" in line for line in code)
    assert any("/silent /install" in line for line in code)
    assert any("WebView2Version" in line for line in code)
    assert any("ssPostInstall" in line for line in code)
    assert any("F3017226-FE2A-4295-8BDF-00C3A9A7E4C5" in line for line in code)


def test_uninstaller_never_targets_the_user_data_directory() -> None:
    text = _text(ISCC_FILE)
    uninstall_delete = _section(text, "UninstallDelete")
    code = _section(text, "Code")

    if uninstall_delete:
        assert not _has_line(uninstall_delete, r"{localappdata}\StemForge")
    assert r"{localappdata}\StemForge" in text
    assert any("usPostUninstall" in line for line in code)
    assert any("DataDir" in line for line in code)


def test_installer_has_the_usual_shortcuts_and_launch_option() -> None:
    icons = _section(_text(ISCC_FILE), "Icons")
    run = _section(_text(ISCC_FILE), "Run")
    tasks = _section(_text(ISCC_FILE), "Tasks")

    assert any("{group}" in line for line in icons)
    assert any("desktopicon" in line for line in icons)
    assert any("desktopicon" in line and "unchecked" in line for line in tasks)
    assert any("postinstall" in line for line in run)


# --- PyInstaller spec -------------------------------------------------------


def test_windows_spec_is_windowed_with_icon_and_version_resource() -> None:
    text = _text(SPEC_FILE)

    assert "console=False" in text
    assert 'icon=str(SRC / "assets" / "stemforge.ico")' in text
    assert "version=str(_version_file)" in text
    assert "STEMFORGE_VERSION_FILE" in text
    assert "version_info.py" in text


def test_windows_spec_excludes_the_legacy_engine_and_keeps_edge_chromium() -> None:
    text = _text(SPEC_FILE)
    hidden = text[text.index("hiddenimports = [") : text.index("a = Analysis(")]
    excludes = text[text.index("excludes=[") : text.index("noarchive=False")]

    assert '"webview.platforms.edgechromium"' in hidden
    assert "webview.platforms.mshtml" not in hidden
    assert "webview.platforms.mshtml" in excludes
    assert '"torchaudio"' in excludes


def test_windows_spec_keeps_only_the_required_torch_bin_file() -> None:
    text = _text(SPEC_FILE)

    assert "_TORCH_KEEP_BIN" in text
    assert "torch_shm_manager" in text
    assert '"torch/test"' in text
    assert '"torch/bin"' in text


# --- PowerShell entry points ------------------------------------------------


def test_build_script_fails_closed_on_vendor_and_licence_gates() -> None:
    text = _text(BUILD_PS1)

    assert "packaging/pyinstaller/stemforge_windows.spec" in text.replace("\\", "/")
    assert "PyInstaller" in text
    assert "verify_bundle.py" in text
    assert "vendor\\ffmpeg\\ffmpeg.exe" in text
    assert "vendor\\rubberband\\sndfile.dll" in text
    assert "THIRD_PARTY_NOTICES.md" in text
    assert "SOURCE_OFFER.md" in text
    assert "LICENSES" in text
    assert "BUILDINFO.txt" in text
    assert "-buildconf" in text
    assert "LICENCE-GATE-PENDING.txt" in text
    assert "AllowLicencePending" in text
    assert "version_info.py" in text


def test_package_script_builds_zip_and_setup_from_one_tree_with_hashes() -> None:
    text = _text(PACKAGE_PS1)

    assert "7z.exe" in text
    assert "Compress-Archive" in text
    assert "ISCC.exe" in text
    assert "/DVersion=$Version" in text
    assert "/DWebView2Installer=$bootstrapper" in text
    assert "webview2.lock.json" in text
    assert "sha256sums.py" in text
    assert "SHA256SUMS" in text
    assert "UNSIGNED.txt" in text
    assert "SmartScreen" in text
    assert "LICENCE-GATE-PENDING.txt" in text
    assert "verify_bundle.py" in text
    assert "--zip" in text
    assert "--setup" in text


def test_smoke_script_runs_every_frozen_flag_and_uses_a_temporary_home() -> None:
    text = _text(SMOKE_PS1)

    for flag in ("--self-test", "--smoke-test", "--preflight", "--smoke-separation"):
        assert flag in text
    assert "--install-model" in text
    assert "--accept-model-licence" in text
    assert "STEMFORGE_HOME" in text
    assert "GetTempPath" in text
    assert "wall_seconds" in text
    assert "separation.rss" in text
    assert "--report" in text
    assert "AllowedExitCodes" in text
    assert "Remove-Item -Recurse" in text


def test_vendor_script_builds_rubber_band_and_libsndfile_from_pinned_sources() -> None:
    text = _text(VENDOR_PS1)

    assert "Build-Libsndfile" in text
    assert "Build-RubberBandFromSource" in text
    assert "meson" in text
    assert "cmake" in text
    assert "--vsenv" in text
    assert "sndfile.pc" in text
    assert "pkg-config" in text
    assert "RubberBandPrebuilt" in text
    assert "sndfile.dll" in text


def test_vendor_script_accepts_the_pinned_source_archives() -> None:
    lock = json.loads((PROJECT_ROOT / "packaging" / "vendor.lock.json").read_text(encoding="utf-8"))

    assert lock["components"]["rubberband"]["targets"]["windows-x64"]["build"]["system"] == (
        "meson-msvc"
    )
    assert lock["components"]["libsndfile"]["targets"]["windows-x64"]["build"]["system"] == (
        "cmake-msvc"
    )
    assert (
        lock["components"]["rubberband"]["targets"]["windows-x64"]["source"]["sha256"]
        == "24300f48a8014b7c863b573a9647e61b1b19b37875e2cdd92005e64c6424d266"
    )
    assert (
        lock["components"]["libsndfile"]["targets"]["windows-x64"]["source"]["sha256"]
        == "3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e"
    )


# --- CI and pins ------------------------------------------------------------


def test_windows_workflow_is_manual_35_minutes_and_off_pull_requests() -> None:
    text = _text(WORKFLOW)

    assert "workflow_dispatch:" in text
    assert "pull_request" not in text
    assert "runs-on: windows-2022" in text
    assert "timeout-minutes: 35" in text
    assert "packaging/windows/build.ps1" in text
    assert "packaging/windows/smoke-test.ps1" in text
    assert "packaging/windows/package.ps1" in text
    assert "hashFiles(" in text
    assert "packaging/vendor.lock.json" in text
    assert "actions/upload-artifact@v7" in text


def test_webview2_bootstrapper_pin_is_the_verified_microsoft_download() -> None:
    lock = json.loads(_text(WEBVIEW2_LOCK))

    assert lock["schema"] == 1
    assert lock["url"] == "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    assert lock["filename"] == "MicrosoftEdgeWebview2Setup.exe"
    assert lock["size"] == 1_897_688
    assert lock["sha256"] == ("cf2f2445fef0c5c14e372045f84998771eeb8bb01d59be4afd00692fa160082e")


def test_windows_readme_names_the_data_path_smartscreen_and_webview2() -> None:
    text = _text(WINDOWS / "README.txt")

    assert "%LOCALAPPDATA%\\StemForge" in text
    assert "SmartScreen" in text
    assert "unsigned" in text.lower()
    assert "WebView2" in text
    assert "SHA256SUMS" in text
    assert "STEMFORGE_HOME" in text


# --- Version resource and SHA256SUMS ---------------------------------------


def test_version_info_renders_the_four_part_resource_and_strings() -> None:
    text = VERSION.render("0.1.0")

    assert "filevers=(1, 0, 0, 0)" in text
    assert "prodvers=(1, 0, 0, 0)" in text
    assert "StringStruct('FileVersion', '0.1.0')" in text
    assert "StringStruct('ProductVersion', '0.1.0')" in text
    assert "StringStruct('OriginalFilename', 'StemForge.exe')" in text
    assert "GPL-3.0-only" in text
    assert "StringTable(" in text
    assert "VarStruct('Translation', [1033, 1200])" in text


def test_version_tuple_accepts_semver_and_ignores_prerelease() -> None:
    assert VERSION.version_tuple("1.2.3") == (1, 2, 3, 0)
    assert VERSION.version_tuple("1.2.3-rc1") == (1, 2, 3, 0)
    assert VERSION.version_tuple("1.2.3+build.4") == (1, 2, 3, 0)
    with pytest.raises(ValueError, match="not a numeric semver"):
        VERSION.version_tuple("not-a-version")


def test_version_info_cli_writes_the_file(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "version_info.txt"

    assert VERSION.main(["--version", "2.3.4", "--output", str(output)]) == 0
    text = output.read_text(encoding="utf-8")
    assert "filevers=(2, 3, 4, 0)" in text
    assert "StringStruct('ProductVersion', '2.3.4')" in text


def test_sha256sums_is_sorted_gnu_format_and_verifies(tmp_path: Path) -> None:
    first = tmp_path / "b-portable.zip"
    second = tmp_path / "a-setup.exe"
    first.write_bytes(b"portable")
    second.write_bytes(b"setup")
    manifest = tmp_path / "SHA256SUMS"

    SUMS.write_manifest([first, second], manifest)

    expected = (
        f"{hashlib.sha256(b'setup').hexdigest()}  a-setup.exe\n"
        f"{hashlib.sha256(b'portable').hexdigest()}  b-portable.zip\n"
    )
    assert manifest.read_text(encoding="utf-8") == expected
    assert SUMS.verify_manifest(manifest) == []


def test_sha256sums_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "StemForge-0.1.0-win64-setup.exe"
    artifact.write_bytes(b"original")
    manifest = tmp_path / "SHA256SUMS"
    SUMS.write_manifest([artifact], manifest)
    artifact.write_bytes(b"tampered")

    problems = SUMS.verify_manifest(manifest)

    assert len(problems) == 1
    assert "SHA-256 mismatch" in problems[0]


def test_sha256sums_rejects_duplicate_basenames(tmp_path: Path) -> None:
    one = tmp_path / "one" / "artifact.zip"
    two = tmp_path / "two" / "artifact.zip"
    one.parent.mkdir()
    two.parent.mkdir()
    one.write_bytes(b"1")
    two.write_bytes(b"2")

    with pytest.raises(ValueError, match="duplicate"):
        SUMS.render([one, two])


# --- verify_bundle.py synthetic trees --------------------------------------


def _write(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _fake_pe(*, subsystem: int = 2, version_resource: bool = True) -> bytes:
    data = bytearray(0x400)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    coff = 0x84
    struct.pack_into("<HHIIIHH", data, coff, 0x8664, 1, 0, 0, 0, 0xE0, 0x22)
    optional = coff + 20
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<H", data, optional + 68, subsystem)
    struct.pack_into("<I", data, optional + 108, 16)
    if version_resource:
        struct.pack_into("<II", data, optional + 112 + 16, 0x1000, 0x100)
        marker = "VS_VERSION_INFO".encode("utf-16-le")
        data[0x200 : 0x200 + len(marker)] = marker
    return bytes(data)


def _synthetic_lock(tmp_path: Path, *, ffmpeg_sha: str | None = None) -> Path:
    installs = {
        "ffmpeg": [
            {
                "from": "bin/ffmpeg.exe",
                "to": "ffmpeg/ffmpeg.exe",
                "executable": True,
                "sha256": ffmpeg_sha,
            },
            {
                "from": "bin/ffprobe.exe",
                "to": "ffmpeg/ffprobe.exe",
                "executable": True,
                "sha256": None,
            },
        ],
        "ffprobe": [
            {
                "from": "bin/ffprobe.exe",
                "to": "ffmpeg/ffprobe.exe",
                "executable": True,
                "sha256": None,
            }
        ],
        "deno": [
            {
                "from": "deno.exe",
                "to": "deno/deno.exe",
                "executable": True,
                "sha256": None,
            }
        ],
        "rubberband": [
            {
                "from": "build/rubberband.exe",
                "to": "rubberband/rubberband.exe",
                "executable": True,
                "sha256": None,
            }
        ],
        "libsndfile": [
            {
                "from": "sndfile.dll",
                "to": "rubberband/sndfile.dll",
                "executable": False,
                "sha256": None,
            }
        ],
    }
    projects = {
        "ffmpeg": "FFmpeg",
        "ffprobe": "FFmpeg",
        "deno": "Deno",
        "rubberband": "Rubber Band Library",
        "libsndfile": "libsndfile",
    }
    texts = {
        "ffmpeg": ["LICENSE.txt"],
        "ffprobe": ["LICENSE.txt"],
        "deno": ["LICENSE"],
        "rubberband": ["COPYING"],
        "libsndfile": ["COPYING"],
    }
    components = {
        name: {
            "version": {
                "ffmpeg": "n9.0.2",
                "ffprobe": "n9.0.2",
                "deno": "2.9.8",
                "rubberband": "4.0.0",
                "libsndfile": "1.2.2",
            }[name],
            "license": {
                "ffmpeg": "GPL-3.0-or-later",
                "ffprobe": "GPL-3.0-or-later",
                "deno": "MIT",
                "rubberband": "GPL-2.0-or-later",
                "libsndfile": "LGPL-2.1-or-later",
            }[name],
            "license_texts": texts[name],
            "provenance": {"project": projects[name]},
            "targets": {
                "windows-x64": {
                    "kind": "built" if name in {"rubberband", "libsndfile"} else "archive",
                    "install": installs[name],
                }
            },
        }
        for name in installs
    }
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps({"schema": 1, "components": components}, indent=2), encoding="utf-8")
    return path


def _webview_lock(tmp_path: Path, *, sha: str = "0" * 64) -> Path:
    path = tmp_path / "webview2.lock.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "url": "https://example.invalid/webview2setup.exe",
                "filename": "MicrosoftEdgeWebview2Setup.exe",
                "size": 4,
                "sha256": sha,
            }
        ),
        encoding="utf-8",
    )
    return path


def _native_manifest(tmp_path: Path) -> Path:
    rows = [
        ("ffmpeg", "FFmpeg and ffprobe", ["GPL-3.0-or-later"], True),
        ("rubberband", "Rubber Band Library", ["GPL-2.0-or-later"], True),
        ("libsndfile", "libsndfile", ["LGPL-2.1-or-later"], True),
        ("deno", "Deno", ["MIT"], False),
        (
            "pyinstaller",
            "PyInstaller bootloader",
            ["GPL-2.0-or-later", "Bootloader-exception"],
            False,
        ),
        ("pytorch", "PyTorch", [], False),
        ("python-runtime", "Python runtime", ["PSF-2.0"], False),
        ("webview2", "Microsoft Edge WebView2", [], False),
    ]
    blocks = []
    for component_id, title, texts, buildinfo in rows:
        blocks.append(
            "\n".join(
                [
                    "[[component]]",
                    f'id = "{component_id}"',
                    f'title = "{title}"',
                    'kind = "vendor"',
                    f"texts = {json.dumps(texts)}",
                    f"buildinfo = {str(buildinfo).lower()}",
                ]
            )
        )
    path = tmp_path / "native.toml"
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


def _bundle(tmp_path: Path, *, licence: bool = True) -> Path:
    bundle = tmp_path / "bundle"
    _write(bundle / "StemForge.exe", _fake_pe())
    _write(bundle / "_internal" / "vendor.lock.json", b"{}")
    _write(bundle / "_internal" / "models" / "models.lock.json", b"{}")
    _write(bundle / "_internal" / "torch" / "lib" / "torch_cpu.dll")
    _write(bundle / "_internal" / "torch" / "bin" / "torch_shm_manager.exe")
    _write(bundle / "_internal" / "lameenc.cp313-win_amd64.pyd")
    for relative in (
        "ffmpeg/ffmpeg.exe",
        "ffmpeg/ffprobe.exe",
        "deno/deno.exe",
        "rubberband/rubberband.exe",
        "rubberband/sndfile.dll",
    ):
        _write(bundle / "vendor" / relative)
    if not licence:
        return bundle

    _write(
        bundle / "LICENSE",
        b"GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n",
    )
    _write(
        bundle / "THIRD_PARTY_NOTICES.md",
        b"FFmpeg and ffprobe; Rubber Band Library; libsndfile; Deno; "
        b"PyInstaller bootloader; PyTorch; Python runtime; Microsoft Edge WebView2.\n",
    )
    _write(
        bundle / "SOURCE_OFFER.md",
        b"StemForge offers the corresponding source for three years from the release date. "
        b"See https://github.com/alexmarincu/stemforge/issues.\n",
    )
    _write(bundle / "LICENSES" / "README.md", b"# Licence texts\n")
    texts = {
        "GPL-3.0-or-later",
        "GPL-2.0-or-later",
        "LGPL-2.1-or-later",
        "MIT",
        "Bootloader-exception",
        "PSF-2.0",
    }
    for text in sorted(texts):
        _write(bundle / "LICENSES" / "common" / f"{text}.txt", b"licence text\n")
    _write(
        bundle / "LICENSES" / "python" / "DEPENDENCIES.md",
        b"| `lameenc` | 1.9.0 | `LGPL-3.0-or-later` | runtime |\n",
    )
    components = {
        "ffmpeg": True,
        "rubberband": True,
        "libsndfile": True,
        "deno": False,
        "pyinstaller": False,
        "pytorch": False,
        "python-runtime": False,
        "webview2": False,
    }
    for component, buildinfo in components.items():
        _write(bundle / "LICENSES" / component / "NOTICE.md", b"# Notice\n")
        if buildinfo:
            _write(bundle / "LICENSES" / component / "BUILDINFO.txt", b"build info\n")
    return bundle


def _verify(
    tmp_path: Path,
    bundle: Path,
    *,
    lock: Path | None = None,
    manifest: Path | None = None,
    allow_pending: bool = False,
    zip_path: Path | None = None,
    setup_path: Path | None = None,
    webview2_installer: Path | None = None,
    webview2_lock: Path | None = None,
) -> object:
    return VERIFY.verify(
        bundle,
        lock_path=lock or _synthetic_lock(tmp_path),
        native_manifest=manifest or _native_manifest(tmp_path),
        allow_licence_pending=allow_pending,
        zip_path=zip_path,
        setup_path=setup_path,
        webview2_lock=webview2_lock or _webview_lock(tmp_path),
        webview2_installer=webview2_installer,
    )


def _errors(report: object) -> list[object]:
    return list(report.errors)


def test_a_complete_synthetic_bundle_passes(tmp_path: Path) -> None:
    report = _verify(tmp_path, _bundle(tmp_path))

    assert report.ok, report.render()


def test_weights_anywhere_fail_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / "models" / "955717e8.safetensors", b"weights")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "weights" for item in _errors(report))


def test_torch_test_directory_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / "_internal" / "torch" / "test" / "test_torch.py", b"print(1)")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "torch.test" for item in _errors(report))


def test_torch_bin_allows_only_torch_shm_manager(tmp_path: Path) -> None:
    clean = _verify(tmp_path, _bundle(tmp_path))
    assert clean.ok, clean.render()

    bundle = _bundle(tmp_path / "bad")
    _write(bundle / "_internal" / "torch" / "bin" / "torch_shm_manager.exe")
    _write(bundle / "_internal" / "torch" / "bin" / "extra_tool.exe")

    report = _verify(tmp_path / "bad", bundle)

    assert any(item.check == "torch.bin" for item in _errors(report))


def test_torchaudio_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / "_internal" / "torchaudio" / "lib" / "libtorchaudio.so")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "torchaudio" for item in _errors(report))


def test_missing_vendor_file_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "vendor" / "deno" / "deno.exe").unlink()

    report = _verify(tmp_path, bundle)

    assert any(item.check == "vendor.files" for item in _errors(report))


def test_vendor_hash_mismatch_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    lock = _synthetic_lock(tmp_path, ffmpeg_sha="0" * 64)

    report = _verify(tmp_path, bundle, lock=lock)

    assert any(item.check == "vendor.files" for item in _errors(report))


def test_unlisted_vendor_file_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / "vendor" / "deno" / "extra.dll")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "vendor.extra" for item in _errors(report))


def test_missing_licence_tree_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, licence=False)

    report = _verify(tmp_path, bundle)

    assert any(item.check == "licence.tree" for item in _errors(report))


def test_allow_licence_pending_is_a_warning_not_an_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, licence=False)

    report = _verify(tmp_path, bundle, allow_pending=True)

    assert report.ok
    assert any(item.level == "warn" for item in report.findings)


def test_a_native_file_without_a_notice_or_dependency_row_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _write(bundle / "_internal" / "mystery" / "mystery.dll")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "licence.native" for item in _errors(report))


def test_a_missing_curated_notice_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "LICENSES" / "pytorch" / "NOTICE.md").unlink()

    report = _verify(tmp_path, bundle)

    assert any(item.check == "licence.tree" for item in _errors(report))


def test_a_native_file_without_a_python_dependency_row_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "LICENSES" / "python" / "DEPENDENCIES.md").write_text("# no rows\n", encoding="utf-8")

    report = _verify(tmp_path, bundle)

    assert any(item.check == "licence.native" for item in _errors(report))


def test_console_subsystem_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "StemForge.exe").write_bytes(_fake_pe(subsystem=3))

    report = _verify(tmp_path, bundle)

    assert any(item.check == "exe.pe" for item in _errors(report))


def test_missing_version_resource_fails_the_bundle(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "StemForge.exe").write_bytes(_fake_pe(version_resource=False))

    report = _verify(tmp_path, bundle)

    assert any(item.check == "exe.version" for item in _errors(report))


def test_budget_boundaries_are_450_and_380_mib_with_hard_fails() -> None:
    assert VERIFY.budget_error("zip", 450 * MIB) is None
    assert "over the 450.0 MiB budget" in VERIFY.budget_error("zip", 451 * MIB)
    assert "hard fail" in VERIFY.budget_error("zip", 601 * MIB)
    assert VERIFY.budget_error("setup", 380 * MIB) is None
    assert "over the 380.0 MiB budget" in VERIFY.budget_error("setup", 381 * MIB)
    assert "hard fail" in VERIFY.budget_error("setup", 501 * MIB)
    with pytest.raises(ValueError, match="unknown artifact kind"):
        VERIFY.budget_error("appimage", 1)


def test_artifact_budget_checks_use_the_actual_file_size(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    zip_path = tmp_path / "StemForge-portable.zip"
    with zip_path.open("wb") as handle:
        handle.truncate(451 * MIB)

    report = _verify(tmp_path, bundle, zip_path=zip_path)

    assert any(item.check == "budget.zip" for item in _errors(report))


def test_webview2_bootstrapper_hash_is_checked(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    installer = tmp_path / "MicrosoftEdgeWebview2Setup.exe"
    installer.write_bytes(b"nope")
    lock = _webview_lock(tmp_path, sha="1" * 64)

    report = _verify(
        tmp_path,
        bundle,
        webview2_installer=installer,
        webview2_lock=lock,
    )

    assert any(item.check == "webview2.bootstrapper" for item in _errors(report))


def test_pe_facts_read_the_windowed_resource_fixture(tmp_path: Path) -> None:
    good = tmp_path / "good.exe"
    good.write_bytes(_fake_pe())
    bad = tmp_path / "bad.exe"
    bad.write_bytes(_fake_pe(subsystem=3, version_resource=False))

    facts = VERIFY.pe_facts(good)
    assert facts is not None
    assert facts.machine == 0x8664
    assert facts.subsystem == 2
    assert facts.has_version_resource is True

    facts = VERIFY.pe_facts(bad)
    assert facts is not None
    assert facts.subsystem == 3
    assert facts.has_version_resource is False


def test_verify_bundle_cli_writes_the_json_report(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    report = tmp_path / "verify.json"

    code = VERIFY.main(
        [
            "--bundle",
            str(bundle),
            "--lock",
            str(_synthetic_lock(tmp_path)),
            "--native-manifest",
            str(_native_manifest(tmp_path)),
            "--webview2-lock",
            str(_webview_lock(tmp_path)),
            "--report",
            str(report),
        ]
    )

    assert code == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["bundle"] == str(bundle)
    assert payload["errors"] == 0
