"""The Linux Bundle verifier and the packaging scripts it guards.

``packaging/linux/verify_bundle.py`` is the release gate from ticket 10 §2 and
§9: the onedir budget, the licence tree, no model weights, the torch trim, and
every vendor file matching ``packaging/vendor.lock.json``. The shell scripts are
asserted here only for parseability and the flags that tie them to the pinned
AppImage tool and the bundle verifier; the real build runs in ticket 20's
verification pass, not in the unit layer.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

import stemforge

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = PROJECT_ROOT / "packaging" / "linux" / "verify_bundle.py"
BUNDLE_NAME = f"StemForge-{stemforge.__version__}-linux-x86_64"


def _load_verifier() -> Any:
    spec = importlib.util.spec_from_file_location("linux_verify_bundle", VERIFIER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _load_verifier()


def _write(path: Path, data: bytes = b"x", *, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        path.chmod(0o755)
    return path


def _vendor_lock(files: dict[str, Path]) -> dict[str, Any]:
    install = []
    for relative, path in files.items():
        install.append(
            {
                "from": f"bin/{relative}",
                "to": relative,
                "executable": True,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return {
        "schema": 1,
        "targets": {"linux-x86_64": {"system": "linux", "arch": "x86_64"}},
        "components": {
            "ffmpeg": {"version": "test", "targets": {"linux-x86_64": {"install": install}}}
        },
    }


def _make_bundle(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / BUNDLE_NAME
    _write(root / "StemForge", b"launcher", executable=True)
    _write(root / "run.sh", b"#!/bin/sh\n", executable=True)
    _write(root / "README.md", b"# StemForge\n")
    _write(root / "_internal" / "models" / "models.lock.json", b"{}")
    _write(root / "_internal" / "torch" / "bin" / "torch_shm_manager", b"shm")
    _write(root / "_internal" / "torch" / "lib" / "libtorch_cpu.so", b"torch")
    _write(root / "LICENSE", b"GPL-3.0-only\n")
    _write(root / "THIRD_PARTY_NOTICES.md", b"notices\n")
    _write(root / "SOURCE_OFFER.md", b"offer\n")
    _write(root / "LICENSES" / "ffmpeg" / "LICENSE.txt", b"GPL\n")
    ffmpeg = _write(root / "vendor" / "ffmpeg" / "ffmpeg", b"ffmpeg", executable=True)
    lock = tmp_path / "vendor.lock.json"
    lock.write_text(json.dumps(_vendor_lock({"ffmpeg/ffmpeg": ffmpeg})), encoding="utf-8")
    return root, lock


def _verify(root: Path, lock: Path, **kwargs: Any) -> Any:
    return VERIFY.verify_bundle(root, lock_path=lock, **kwargs)


def _checks(result: Any) -> dict[str, str]:
    return {finding.check: finding.status for finding in result.findings}


def test_a_complete_bundle_passes(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)

    result = _verify(root, lock)

    assert result.ok, [finding.detail for finding in result.findings if finding.status == "fail"]
    assert result.sizes["bundle"] == VERIFY.tree_size(root)
    assert _checks(result)["bundle.torch"] == "pass"
    assert _checks(result)["bundle.vendor"] == "pass"


def test_a_missing_licence_tree_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    (root / "THIRD_PARTY_NOTICES.md").unlink()

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.licences"] == "fail"


def test_the_licence_tree_can_be_skipped_only_for_debug_builds(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    shutil.rmtree(root / "LICENSES")

    assert not _verify(root, lock).ok
    result = _verify(root, lock, allow_missing_licences=True)

    assert result.ok
    assert _checks(result)["bundle.licences"] == "skip"


def test_shipped_model_weights_fail(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "_internal" / "models" / "955717e8.safetensors", b"weights")

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.weights"] == "fail"


def test_a_weights_file_anywhere_in_the_tree_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "vendor" / "ffmpeg" / "surprise.pth", b"weights")

    assert not _verify(root, lock).ok


def test_torch_test_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "_internal" / "torch" / "test" / "test_torch.py", b"")

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.torch"] == "fail"


def test_torch_bin_allows_only_the_shm_manager(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "_internal" / "torch" / "bin" / "torchrun", b"")

    result = _verify(root, lock)

    assert not result.ok
    assert "torch/bin/torchrun" in " ".join(
        finding.detail for finding in result.findings if finding.check == "bundle.torch"
    )


def test_torchaudio_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "_internal" / "torchaudio" / "__init__.py", b"")

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.torch"] == "fail"


def test_vendor_hash_mismatch_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    (root / "vendor" / "ffmpeg" / "ffmpeg").write_bytes(b"changed")

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.vendor"] == "fail"


def test_missing_vendor_file_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    (root / "vendor" / "ffmpeg" / "ffmpeg").unlink()

    assert not _verify(root, lock).ok


def test_an_undeclared_vendor_file_fails(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "vendor" / "surprise" / "tool", b"", executable=True)

    result = _verify(root, lock)

    assert not result.ok
    assert "surprise/tool" in " ".join(
        finding.detail for finding in result.findings if finding.check == "bundle.vendor"
    )


def test_provenance_metadata_is_allowed_in_vendor(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    _write(root / "vendor" / "PROVENANCE.txt", b"pins\n")

    assert _verify(root, lock).ok


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Windows has no executable bit; POSIX packaging is checked on Linux",
)
def test_the_launcher_and_run_script_must_be_executable(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    (root / "run.sh").chmod(0o644)

    result = _verify(root, lock)

    assert not result.ok
    assert _checks(result)["bundle.layout"] == "fail"


def test_bundle_budget_is_enforced(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    size = VERIFY.tree_size(root)

    budgets = {**VERIFY.BUDGET_BYTES, "bundle": size}
    assert _verify(root, lock, budgets=budgets).ok

    budgets["bundle"] = size - 1
    result = _verify(root, lock, budgets=budgets)
    assert not result.ok
    assert _checks(result)["budget.bundle"] == "fail"


def _write_tar_gz(path: Path, root: Path) -> None:
    members = {
        "StemForge": b"launcher",
        "run.sh": b"#!/bin/sh\n",
        "LICENSE": b"GPL\n",
        "THIRD_PARTY_NOTICES.md": b"notices\n",
        "SOURCE_OFFER.md": b"offer\n",
        "LICENSES/ffmpeg/LICENSE.txt": b"GPL\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for relative, data in members.items():
            info = tarfile.TarInfo(f"{root.name}/{relative}")
            info.size = len(data)
            info.mode = 0o755 if relative in {"StemForge", "run.sh"} else 0o644
            archive.addfile(info, io.BytesIO(data))


def test_archive_budgets_are_enforced_per_archive_kind(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    tarball = tmp_path / f"{root.name}.tar.gz"
    _write_tar_gz(tarball, root)
    zstd = tmp_path / f"{root.name}.tar.zst"
    _write(zstd, b"zstd")
    appimage = tmp_path / f"{root.name}.AppImage"
    _write(appimage, b"appimage")

    passing = {**VERIFY.BUDGET_BYTES, "tar.gz": tarball.stat().st_size}
    result = _verify(root, lock, archives=[tarball, zstd, appimage], budgets=passing)
    assert result.ok, [finding.detail for finding in result.findings if finding.status == "fail"]

    failing = {**passing, "tar.gz": tarball.stat().st_size - 1}
    result = _verify(root, lock, archives=[tarball], budgets=failing)
    assert not result.ok
    assert _checks(result)["budget.tar.gz"] == "fail"


def test_tar_content_is_checked_for_weights_and_the_licence_tree(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    tarball = tmp_path / f"{root.name}.tar.gz"
    _write_tar_gz(tarball, root)
    _write(root / "_internal" / "models" / "weights.safetensors", b"weights")

    result = _verify(root, lock, archives=[tarball])

    assert not result.ok

    with tarfile.open(tarball, "w:gz") as archive:
        info = tarfile.TarInfo(f"{root.name}/_internal/models/weights.safetensors")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"w"))
    result = _verify(root, lock, archives=[tarball])
    assert not result.ok
    assert any(
        finding.check.startswith("archive") and finding.status == "fail"
        for finding in result.findings
    )


def test_archive_licence_gate_can_be_skipped_only_for_debug_builds(tmp_path: Path) -> None:
    root, lock = _make_bundle(tmp_path)
    tarball = tmp_path / f"{root.name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        for name, data in (("StemForge", b"launcher"), ("run.sh", b"#!/bin/sh\n")):
            info = tarfile.TarInfo(f"{root.name}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    assert not _verify(root, lock, archives=[tarball]).ok
    result = _verify(root, lock, archives=[tarball], allow_missing_licences=True)
    assert result.ok
    assert _checks(result)["archive.tar.gz.contents"] == "pass"


def test_the_cli_prints_json_and_uses_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, lock = _make_bundle(tmp_path)

    code = VERIFY.main(["--bundle", str(root), "--lock", str(lock), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["sizes"]["bundle"] == VERIFY.tree_size(root)

    (root / "LICENSE").unlink()
    code = VERIFY.main(["--bundle", str(root), "--lock", str(lock), "--json"])

    assert code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_the_self_check_exercises_both_outcomes() -> None:
    assert VERIFIER_PATH.is_file()
    assert VERIFY.main(["--self-check"]) == 0


def test_the_appimage_tool_pin_is_complete() -> None:
    lock = json.loads(
        (PROJECT_ROOT / "packaging" / "linux" / "appimage.lock.json").read_text("utf-8")
    )

    assert lock["version"] == "1.9.1"
    assert lock["url"].startswith("https://github.com/AppImage/appimagetool/releases/download/")
    assert lock["url"].endswith("/appimagetool-x86_64.AppImage")
    assert re.fullmatch(r"[0-9a-f]{64}", lock["sha256"])
    assert lock["size"] > 0
    assert lock["asset"] == "appimagetool-x86_64.AppImage"
    runtime = lock["runtime"]
    assert runtime["version"] == "20251108"
    assert runtime["url"].startswith("https://github.com/AppImage/type2-runtime/releases/download/")
    assert runtime["url"].endswith("/runtime-x86_64")
    assert re.fullmatch(r"[0-9a-f]{64}", runtime["sha256"])
    assert runtime["size"] > 0


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="the Linux scripts are parsed and shellchecked on POSIX hosts",
)
@pytest.mark.parametrize(
    ("script", "shell"),
    [
        ("packaging/linux/build.sh", "bash"),
        ("packaging/linux/run.sh", "sh"),
        ("packaging/linux/smoke-test.sh", "bash"),
    ],
)
def test_the_packaging_scripts_parse(script: str, shell: str) -> None:
    completed = subprocess.run(
        [shell, "-n", str(PROJECT_ROOT / script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_the_build_script_wires_the_release_gates() -> None:
    script = (PROJECT_ROOT / "packaging" / "linux" / "build.sh").read_text("utf-8")

    assert "verify_bundle.py" in script
    assert "vendor-fetch.sh --verify-only" in script
    assert "--exclude=.cache" in script
    assert "--sort=name" in script
    assert "SOURCE_DATE_EPOCH" in script
    assert "appimage.lock.json" in script
    assert "--runtime-file" in script
    assert "--mksquashfs-opt=-Xcompression-level" in script
    assert "--allow-missing-licences" in script
    assert "StemForge-${version}-linux-x86_64" in script


def test_run_sh_falls_back_to_browser_mode() -> None:
    script = (PROJECT_ROOT / "packaging" / "linux" / "run.sh").read_text("utf-8")

    assert "--preflight --quiet" in script
    assert "STEMFORGE_BROWSER=1" in script
    assert 'exec "$here/StemForge" "$@"' in script
    assert "vendor/rubberband" in script


def test_the_smoke_script_covers_the_appimage() -> None:
    script = (PROJECT_ROOT / "packaging" / "linux" / "smoke-test.sh").read_text("utf-8")

    assert "--with-separation" in script
    assert "--appimage-extract-and-run" in script
    assert "--self-test" in script
    assert "--smoke-test" in script


def test_the_bundle_readme_documents_the_host_contract() -> None:
    readme = (PROJECT_ROOT / "packaging" / "linux" / "README.md").read_text("utf-8")

    for needle in (
        "sudo apt install",
        "sudo dnf install",
        "sudo pacman -S",
        "~/.local/share/stemforge",
        "~/.config/stemforge",
        "~/.cache/stemforge",
        "~/.local/state/stemforge/log",
        "STEMFORGE_HOME",
        "STEMFORGE_BROWSER",
        "--appimage-extract-and-run",
        "--self-test",
        "--smoke-test",
        "--with-separation",
        "unsigned",
    ):
        assert needle in readme, needle
