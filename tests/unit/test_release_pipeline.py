"""The release pipeline's executable contract (ticket 23).

These tests do not build a bundle. They exercise the pieces the tag workflow
depends on: the workflow graph itself, the asset-name contract, the release
checklist scaffold, the release-notes renderer, ``verify-release.sh`` against
synthetic asset sets, and ``fetch_sources.py`` against a local ``file://``
source. Expected asset names, budgets, and checklist rows come from tickets 10
and 11, not from the implementation.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts import fetch_sources, release_assets, release_notes, vendor_unpack

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
CHECKLIST = REPO_ROOT / "docs" / "qa" / "release-checklist.md"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify-release.sh"

VERSION = "9.9.9"
TAG = f"v{VERSION}"

# Ticket 10 section 8. Names, not a computed pattern: a mismatch must fail.
EXPECTED_ASSETS = {
    f"StemForge-{VERSION}-linux-x86_64.tar.gz",
    f"StemForge-{VERSION}-linux-x86_64.tar.zst",
    f"StemForge-{VERSION}-linux-x86_64.AppImage",
    f"StemForge-{VERSION}-win64-portable.zip",
    f"StemForge-{VERSION}-win64-setup.exe",
    f"StemForge-{VERSION}-src.tar.gz",
    f"StemForge-{VERSION}-third-party-sources.tar.zst",
    "SHA256SUMS",
}

RELEASE_TOML = """\
schema = 1

[project]
name = "StemForge"
license = "GPL-3.0-only"
copyright = "Copyright (C) 2026 the StemForge contributors"
version_file = "src/stemforge/__init__.py"

[repo]
url = "https://github.com/alexmarincu/stemforge"
releases_url = "https://github.com/alexmarincu/stemforge/releases"
source_offer_contact = "https://github.com/alexmarincu/stemforge/issues"

[source_offer]
years = 3
start_year = 2026
end_year = 2029

[assets]
source_archive = "StemForge-{version}-src.tar.gz"
third_party_sources_archive = "StemForge-{version}-third-party-sources.tar.zst"

[paths]
license = "LICENSE"
notices = "THIRD_PARTY_NOTICES.md"
source_offer = "SOURCE_OFFER.md"
licenses_dir = "LICENSES"
uv_lock = "uv.lock"
venv = ".venv"
vendor_lock = "packaging/vendor.lock.json"
vendor_sources = "packaging/vendor/SOURCES.md"
native_manifest = "packaging/licenses/native.toml"
python_manifest = "packaging/licenses/python.toml"
license_texts = "packaging/licenses/texts"
"""

SOURCES_MD = """\
# Vendor sources

## Components

| Component | Version | Licence | Built from |
|---|---|---|---|
| `alpha` | `0.1.0` | `GPL-3.0-or-later` | source archive |
| `beta` | `2.0.0` | `MIT` | source archive |

Written offer: **three years**.
No weights.
"""


# ---------------------------------------------------------------------------
# Workflow graph
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    payload = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_release_triggers_on_version_tags_and_dispatch(workflow: dict[str, Any]) -> None:
    # PyYAML parses the bare ``on`` key as boolean True under YAML 1.1.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    assert triggers["push"]["tags"] == ["v*"]
    assert "workflow_dispatch" in triggers


def test_release_can_write_release_assets(workflow: dict[str, Any]) -> None:
    assert workflow["permissions"]["contents"] == "write"


def test_release_job_graph_and_timeouts_match_ticket_10(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    assert {"prepare", "linux", "windows", "sources", "publish"} <= set(jobs)
    assert jobs["prepare"]["timeout-minutes"] == 10
    assert jobs["linux"]["timeout-minutes"] == 45
    assert jobs["windows"]["timeout-minutes"] == 60
    assert jobs["sources"]["timeout-minutes"] == 30
    assert jobs["publish"]["timeout-minutes"] == 15
    assert jobs["linux"]["runs-on"] == "ubuntu-22.04"
    assert jobs["windows"]["runs-on"] == "windows-2022"
    for job in ("linux", "windows", "sources", "publish"):
        assert "prepare" in jobs[job]["needs"], job
    assert set(jobs["publish"]["needs"]) == {"prepare", "linux", "windows", "sources"}


def test_publish_waits_in_the_protected_release_environment(workflow: dict[str, Any]) -> None:
    publish = workflow["jobs"]["publish"]
    assert publish["environment"] == "release"
    step_text = yaml.safe_dump(publish)
    assert "--draft=false" in step_text


def test_prepare_asserts_the_tag_and_runs_the_notice_gates(workflow: dict[str, Any]) -> None:
    prepare = workflow["jobs"]["prepare"]
    checkout = next(
        step
        for step in prepare["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout["with"]["fetch-depth"] == 0
    text = yaml.safe_dump(prepare)
    assert "GITHUB_REF_NAME" in text
    assert "stemforge.__version__" in text
    assert "generate_notices.py --check" in text
    assert "--check-release" in text
    assert "gh release create" in text
    assert "--draft" in text
    assert "release-notes.md" in text


def test_separation_jobs_cache_the_model_lock(workflow: dict[str, Any]) -> None:
    model_lock = "src/stemforge/models/models.lock.json"
    for job in ("linux", "windows"):
        text = yaml.safe_dump(workflow["jobs"][job])
        assert "actions/cache" in text
        assert "packaging/vendor.lock.json" in text
        assert model_lock in text


def test_windows_steps_cannot_hang_the_whole_job(workflow: dict[str, Any]) -> None:
    steps = {step.get("name"): step for step in workflow["jobs"]["windows"]["steps"]}
    for name in (
        "Fetch and build the pinned vendor set",
        "Build the frozen bundle",
        "Frozen smoke and separation in the temporary STEMFORGE_HOME",
        "Package the portable ZIP and Inno setup",
    ):
        assert steps[name]["timeout-minutes"] <= 25, name


def test_next_image_jobs_are_non_blocking(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    for name in ("linux-next", "windows-next"):
        assert jobs[name]["continue-on-error"] is True
        assert "--self-test" in yaml.safe_dump(jobs[name])


# ---------------------------------------------------------------------------
# Asset names and verify-release.sh
# ---------------------------------------------------------------------------


def test_asset_names_are_the_ticket_10_set() -> None:
    assert set(release_assets.asset_names(VERSION)) == EXPECTED_ASSETS


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tar_bytes(prefix: str, files: dict[str, bytes], *, mtime: int = 0) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            info.mtime = mtime
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if name.endswith((".sh", "StemForge", "deno", "ffmpeg")) else 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return buffer.getvalue()


def _fake_appimage(payload: dict[str, bytes]) -> bytes:
    """A shell script that provides ``--appimage-extract`` like a real AppImage."""
    root = "usr/lib/stemforge"
    declarations = "\n".join(
        f"mkdir -p \"$(dirname 'squashfs-root/{root}/{name}')\" && "
        f"printf '%s' '{base64.b64encode(data).decode()}' | base64 -d "
        f"> 'squashfs-root/{root}/{name}'"
        for name, data in sorted(payload.items())
    )
    script = f"""#!/bin/sh
set -eu
if [ "${{1:-}}" = "--appimage-extract" ]; then
  rm -rf squashfs-root
  {declarations}
  exit 0
fi
echo "fake appimage" >&2
exit 1
"""
    return script.encode()


def _bundle_files(
    lock: dict[str, Any],
    target: str,
    *,
    licence_tree: bool = True,
    weights: bool = False,
    extra_binary: bool = False,
    bad_vendor: bool = False,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        "StemForge": b"launcher",
        "_internal/stemforge.txt": b"payload",
        "run.sh": b"#!/bin/sh\n",
        "README.txt": b"readme",
    }
    if licence_tree:
        files.update(
            {
                "LICENSE": b"GPL-3.0-only\n",
                "THIRD_PARTY_NOTICES.md": b"notices\n",
                "SOURCE_OFFER.md": b"offer\n",
                "LICENSES/README.md": b"licences\n",
            }
        )
    if weights:
        files["_internal/models/htdemucs/955717e8.safetensors"] = b"weights"
    if extra_binary:
        files["vendor/extra/tool"] = b"extra"
    provenance: list[str] = []
    for component, record in lock["components"].items():
        entry = record["targets"][target]
        for install in entry["install"]:
            data = f"{component}:{install['to']}".encode()
            if bad_vendor and install.get("sha256"):
                data = b"tampered"
            files[f"vendor/{install['to']}"] = data
            if install.get("sha256"):
                # The synthetic lock's hash is replaced with the actual hash by
                # the caller after this function returns.
                pass
            provenance.append(f"  installed: vendor/{install['to']} sha256={_sha256(data)}")
    files["vendor/PROVENANCE.txt"] = (
        "StemForge vendor set\n" + "\n".join(provenance) + "\n"
    ).encode()
    return files


def _write_asset(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _make_repo_root(tmp_path: Path, *, placeholder: bool = False) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "packaging").mkdir()
    (root / "src/stemforge").mkdir(parents=True)
    for name in ("generate_notices.py", "release_assets.py"):
        shutil.copy2(REPO_ROOT / "scripts" / name, root / "scripts" / name)
    text = RELEASE_TOML
    if placeholder:
        text = text.replace(
            "https://github.com/alexmarincu/stemforge/releases", "https://example.com/releases"
        )
    (root / "packaging" / "release.toml").write_text(text, encoding="utf-8")
    (root / "src/stemforge/__init__.py").write_text(
        f'__version__ = "{VERSION}"\n', encoding="utf-8"
    )
    return root


def _synthetic_lock(tmp_path: Path, targets: dict[str, dict[str, bytes]]) -> dict[str, Any]:
    lock: dict[str, Any] = {
        "schema": 1,
        "components": {
            "alpha": {
                "version": "0.1.0",
                "license": "GPL-3.0-or-later",
                "targets": {},
            }
        },
        "source_offer": {"sources": []},
    }
    component = lock["components"]["alpha"]
    for target, target_files in targets.items():
        installs = []
        for name, data in target_files.items():
            if not name.startswith("vendor/") or name == "vendor/PROVENANCE.txt":
                continue
            installs.append(
                {
                    "from": name,
                    "to": name.removeprefix("vendor/"),
                    "executable": False,
                    "sha256": _sha256(data),
                }
            )
        component["targets"][target] = {"kind": "archive", "install": installs}
    lock_path = tmp_path / "vendor.lock.json"
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True), encoding="utf-8")
    return lock


def _make_assets(
    tmp_path: Path,
    *,
    licence_tree: bool = True,
    weights: bool = False,
    extra_binary: bool = False,
    bad_vendor: bool = False,
    missing_sums: bool = False,
    bad_checksum: bool = False,
    extra_asset: bool = False,
) -> tuple[Path, Path]:
    assets = tmp_path / "assets"
    assets.mkdir()
    bundle_name = f"StemForge-{VERSION}-linux-x86_64"
    empty_lock: dict[str, Any] = {"components": {}, "source_offer": {"sources": []}}
    linux_files = _bundle_files(
        empty_lock,
        "linux-x86_64",
        licence_tree=licence_tree,
        weights=weights,
        extra_binary=extra_binary,
    )
    # Add one synthetic vendor file with a pinned hash.
    linux_files["vendor/ffmpeg/ffmpeg"] = b"ffmpeg-binary"
    windows_lock_files = {"vendor/ffmpeg/ffmpeg.exe": b"ffmpeg-binary"}
    _synthetic_lock(
        tmp_path,
        {"linux-x86_64": linux_files, "windows-x64": windows_lock_files},
    )
    if bad_vendor:
        linux_files["vendor/ffmpeg/ffmpeg"] = b"tampered"
    zipped = _tar_bytes(bundle_name, linux_files)
    (assets / f"{bundle_name}.tar.gz").write_bytes(gzip.compress(zipped, mtime=0))
    (assets / f"{bundle_name}.tar.zst").write_bytes(
        subprocess.run(
            ["zstd", "-q", "-19", "-T0", "-c"],
            input=zipped,
            check=True,
            capture_output=True,
        ).stdout
    )
    (assets / f"{bundle_name}.AppImage").write_bytes(_fake_appimage(linux_files))

    windows_root = f"StemForge-{VERSION}-win64"
    windows_files = {
        f"{windows_root}/{name}": data
        for name, data in linux_files.items()
        if name.startswith(("LICENSE", "THIRD_PARTY", "SOURCE_", "LICENSES/"))
    }
    windows_files[f"{windows_root}/StemForge.exe"] = b"MZ" + b"\0" * 200
    windows_files[f"{windows_root}/vendor/ffmpeg/ffmpeg.exe"] = b"ffmpeg-binary"
    (assets / f"StemForge-{VERSION}-win64-portable.zip").write_bytes(_zip_bytes(windows_files))
    setup_names = [
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "SOURCE_OFFER.md",
        "LICENSES\\",
        "StemForge.exe",
        "vendor\\ffmpeg\\ffmpeg.exe",
        "vendor\\rubberband\\rubberband.exe",
        "vendor\\rubberband\\sndfile.dll",
    ]
    setup = b"MZ" + b"\0" * 100 + "\n".join(setup_names).encode("utf-16-le")
    (assets / f"StemForge-{VERSION}-win64-setup.exe").write_bytes(setup)

    src_prefix = f"StemForge-{VERSION}"
    src_files = {
        "LICENSE": b"GPL",
        "packaging/release.toml": RELEASE_TOML.encode(),
        "packaging/vendor.lock.json": (tmp_path / "vendor.lock.json").read_bytes(),
        "scripts/vendor-fetch.sh": b"#!/bin/sh\n",
        "scripts/vendor-fetch.ps1": b"# ps1\n",
        "src/stemforge/__init__.py": f'__version__ = "{VERSION}"\n'.encode(),
        "HANDOFF.md": b"handoff",
    }
    (assets / f"{src_prefix}-src.tar.gz").write_bytes(
        gzip.compress(_tar_bytes(src_prefix, src_files), mtime=0)
    )

    sources_files = {
        "SOURCES.md": SOURCES_MD.encode(),
        "MANIFEST.json": b'{"schema": 1}\n',
        "build/vendor-fetch.sh": b"#!/bin/sh\n",
        "build/vendor-fetch.ps1": b"# ps1\n",
    }
    (assets / f"{src_prefix}-third-party-sources.tar.zst").write_bytes(
        subprocess.run(
            ["zstd", "-q", "-19", "-T0", "-c"],
            input=_tar_bytes("SOURCES", sources_files),
            check=True,
            capture_output=True,
        ).stdout
    )

    others = sorted(path for path in assets.iterdir() if path.name != "SHA256SUMS")
    lines = [f"{_sha256(path.read_bytes())}  {path.name}" for path in others]
    if bad_checksum:
        _first_digest, first_name = lines[0].split("  ", 1)
        lines[0] = f"{'0' * 64}  {first_name}"
    if not missing_sums:
        (assets / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if extra_asset:
        (assets / f"StemForge-{VERSION}-linux-x86_64.deb").write_bytes(b"deb")
        if not missing_sums:
            with (assets / "SHA256SUMS").open("a", encoding="utf-8") as handle:
                handle.write(f"{_sha256(b'deb')}  StemForge-{VERSION}-linux-x86_64.deb\n")
    return assets, tmp_path / "vendor.lock.json"


def _run_verify(
    repo_root: Path, assets: Path, lock: Path, *, tag: str = TAG, extra: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "sh",
            str(VERIFY_SCRIPT),
            "--dir",
            str(assets),
            "--tag",
            tag,
            "--repo-root",
            str(repo_root),
            "--vendor-lock",
            str(lock),
            "--no-innoextract",
            *(extra or []),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return _make_repo_root(tmp_path)


def test_verify_release_accepts_the_exact_asset_set(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS  assets.exact" in result.stdout
    assert "PASS  sums.coverage" in result.stdout


def test_verify_release_rejects_a_missing_licence_tree(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, licence_tree=False)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  licences." in result.stdout


def test_verify_release_rejects_an_extra_binary(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, extra_asset=True)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  assets.exact" in result.stdout


def test_verify_release_rejects_model_weights(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, weights=True)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  weights." in result.stdout


def test_verify_release_rejects_a_bad_checksum(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, bad_checksum=True)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  sums.match" in result.stdout


def test_verify_release_rejects_a_missing_sha256sums(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, missing_sums=True)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  assets.exact" in result.stdout or "FAIL  sums.present" in result.stdout


def test_verify_release_rejects_a_placeholder_url(tmp_path: Path) -> None:
    repo = _make_repo_root(tmp_path, placeholder=True)
    assets, lock = _make_assets(tmp_path)
    result = _run_verify(repo, assets, lock)
    assert result.returncode == 1
    assert "placeholder" in (result.stdout + result.stderr).lower()


def test_verify_release_rejects_a_tag_mismatch(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path)
    result = _run_verify(repo_root, assets, lock, tag="v9.9.8")
    assert result.returncode == 1
    assert "FAIL  version.tag" in result.stdout


def test_verify_release_rejects_a_vendor_hash_mismatch(tmp_path: Path, repo_root: Path) -> None:
    assets, lock = _make_assets(tmp_path, bad_vendor=True)
    result = _run_verify(repo_root, assets, lock)
    assert result.returncode == 1
    assert "FAIL  vendor." in result.stdout


def test_setup_extraction_checks_the_same_licence_and_vendor_tree(tmp_path: Path) -> None:
    assets, lock_path = _make_assets(tmp_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    app = tmp_path / "app"
    (app / "LICENSES").mkdir(parents=True)
    (app / "LICENSE").write_text("GPL", encoding="utf-8")
    (app / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
    (app / "SOURCE_OFFER.md").write_text("offer", encoding="utf-8")
    (app / "LICENSES" / "README.md").write_text("licences", encoding="utf-8")
    (app / "vendor" / "ffmpeg").mkdir(parents=True)
    (app / "vendor" / "ffmpeg" / "ffmpeg.exe").write_bytes(b"ffmpeg-binary")

    findings = release_assets._setup_findings(assets, VERSION, lock, extracted_root=app)

    by_name = {finding.check: finding for finding in findings}
    assert by_name["setup.licences"].status == "pass"
    assert by_name["setup.vendor"].status == "pass"
    assert by_name["setup.tree"].status == "pass"


# ---------------------------------------------------------------------------
# fetch_sources.py
# ---------------------------------------------------------------------------


def _source_archive(tmp_path: Path, name: str, data: bytes) -> tuple[str, str]:
    path = tmp_path / name
    path.write_bytes(data)
    return path.as_uri(), _sha256(data)


def _sources_lock(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    lock = {
        "components": {
            "alpha": {
                "version": "0.1.0",
                "license": "GPL-3.0-or-later",
                "targets": {
                    "linux-x86_64": {
                        "kind": "built",
                        "source": {
                            "url": entries[0]["url"],
                            "filename": Path(urllib.parse.urlparse(entries[0]["url"]).path).name,
                            "size": entries[0]["size"],
                            "sha256": entries[0]["sha256"],
                        },
                        "install": [],
                    }
                },
            },
            "beta": {
                "version": "2.0.0",
                "license": "MIT",
                "targets": {
                    "linux-x86_64": {
                        "kind": "built",
                        "source": {
                            "url": entries[1]["url"],
                            "filename": Path(urllib.parse.urlparse(entries[1]["url"]).path).name,
                            "size": entries[1]["size"],
                            "sha256": entries[1]["sha256"],
                        },
                        "install": [],
                    }
                },
            },
        },
        "source_offer": {"sources": entries},
    }
    path = tmp_path / "vendor.lock.json"
    path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
    return path


def _sources_args(tmp_path: Path, lock: Path, output: Path, *, verify: bool = False) -> list[str]:
    root = tmp_path / "root"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "packaging").mkdir(parents=True, exist_ok=True)
    copy = root / "scripts"
    for name in ("vendor-fetch.sh", "vendor-fetch.ps1"):
        shutil.copy2(REPO_ROOT / "scripts" / name, copy / name)
    sources_md = root / "packaging" / "vendor" / "SOURCES.md"
    sources_md.parent.mkdir(parents=True, exist_ok=True)
    sources_md.write_text(SOURCES_MD, encoding="utf-8")
    args = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "fetch_sources.py"),
        "--lock",
        str(lock),
        "--sources-md",
        str(sources_md),
        "--root",
        str(root),
        "--version",
        VERSION,
        "--cache",
        str(tmp_path / "cache"),
        "--output",
        str(output),
    ]
    if verify:
        args.append("--verify")
    return args


def _source_entries(tmp_path: Path) -> list[dict[str, Any]]:
    first_url, first_sha = _source_archive(tmp_path, "alpha-0.1.0.tar.gz", b"alpha source")
    second_url, second_sha = _source_archive(tmp_path, "beta-2.0.0.tar.gz", b"beta source")
    return [
        {
            "component": "alpha",
            "version": "0.1.0",
            "license": "GPL-3.0-or-later",
            "targets": ["linux-x86_64"],
            "url": first_url,
            "sha256": first_sha,
            "size": len(b"alpha source"),
            "source": "alpha upstream",
        },
        {
            "component": "beta",
            "version": "2.0.0",
            "license": "MIT",
            "targets": ["linux-x86_64"],
            "url": second_url,
            "sha256": second_sha,
            "size": len(b"beta source"),
            "source": "beta upstream",
        },
    ]


def test_source_archive_name_prefers_the_lock_recorded_filename(tmp_path: Path) -> None:
    """The Rubber Band tag URL ends in ``v4.0.0.tar.gz``; the lock names it."""
    archive = tmp_path / "download.bin"
    archive.write_bytes(b"source")
    sha = _sha256(b"source")
    lock = {
        "components": {
            "alpha": {
                "version": "0.1.0",
                "license": "GPL-3.0-or-later",
                "targets": {
                    "linux-x86_64": {
                        "kind": "built",
                        "source": {
                            "url": archive.as_uri(),
                            "filename": "alpha-0.1.0.tar.gz",
                            "size": 6,
                            "sha256": sha,
                        },
                        "install": [],
                    }
                },
            }
        },
        "source_offer": {
            "sources": [
                {
                    "component": "alpha",
                    "version": "0.1.0",
                    "license": "GPL-3.0-or-later",
                    "targets": ["linux-x86_64"],
                    "url": archive.as_uri(),
                    "sha256": sha,
                }
            ]
        },
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    sources_md = tmp_path / "SOURCES.md"
    sources_md.write_text(SOURCES_MD, encoding="utf-8")

    sources = fetch_sources.load_sources(lock_path, sources_md)

    assert sources[0].filename == "alpha-0.1.0.tar.gz"
    assert release_assets._source_offer_archives(lock)[0]["filename"] == "alpha-0.1.0.tar.gz"


def test_fetch_sources_builds_a_deterministic_verified_bundle(tmp_path: Path) -> None:
    entries = _source_entries(tmp_path)
    lock = _sources_lock(tmp_path, entries)
    first = tmp_path / f"StemForge-{VERSION}-third-party-sources.tar.gz"
    second = tmp_path / "second.tar.gz"
    result = subprocess.run(_sources_args(tmp_path, lock, first), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(_sources_args(tmp_path, lock, second), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert first.read_bytes() == second.read_bytes()

    verify = subprocess.run(
        _sources_args(tmp_path, lock, first, verify=True), capture_output=True, text=True
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    with tarfile.open(first, "r:gz") as archive:
        names = set(archive.getnames())
    assert "SOURCES/SOURCES.md" in names
    assert "SOURCES/MANIFEST.json" in names
    assert "SOURCES/build/vendor-fetch.sh" in names
    assert "SOURCES/alpha/alpha-0.1.0.tar.gz" in names
    assert "SOURCES/beta/beta-2.0.0.tar.gz" in names


def test_fetch_sources_zstd_output_is_deterministic(tmp_path: Path) -> None:
    if shutil.which("zstd") is None:
        pytest.skip("zstd is not installed")
    entries = _source_entries(tmp_path)
    lock = _sources_lock(tmp_path, entries)
    first = tmp_path / "first.tar.zst"
    second = tmp_path / "second.tar.zst"
    assert subprocess.run(_sources_args(tmp_path, lock, first)).returncode == 0
    assert subprocess.run(_sources_args(tmp_path, lock, second)).returncode == 0
    assert first.read_bytes() == second.read_bytes()
    verify = subprocess.run(
        _sources_args(tmp_path, lock, first, verify=True), capture_output=True, text=True
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_fetch_sources_fails_closed_on_a_hash_mismatch(tmp_path: Path) -> None:
    entries = _source_entries(tmp_path)
    entries[0]["sha256"] = "0" * 64
    lock = _sources_lock(tmp_path, entries)
    output = tmp_path / "sources.tar.gz"
    result = subprocess.run(_sources_args(tmp_path, lock, output), capture_output=True, text=True)
    assert result.returncode == 1
    assert "SHA-256 mismatch" in result.stdout + result.stderr
    assert not output.exists()


def test_fetch_sources_rejects_a_path_escape(tmp_path: Path) -> None:
    entries = _source_entries(tmp_path)
    entries[0]["url"] = (tmp_path / ".." / "escape.tar.gz").as_uri()
    lock = _sources_lock(tmp_path, entries)
    output = tmp_path / "sources.tar.gz"
    result = subprocess.run(_sources_args(tmp_path, lock, output), capture_output=True, text=True)
    assert result.returncode == 1
    assert "unsafe" in (result.stdout + result.stderr).lower()


def test_fetch_sources_verify_rejects_a_tampered_archive(tmp_path: Path) -> None:
    entries = _source_entries(tmp_path)
    lock = _sources_lock(tmp_path, entries)
    output = tmp_path / "sources.tar.gz"
    assert subprocess.run(_sources_args(tmp_path, lock, output)).returncode == 0
    with tarfile.open(output, "r:gz") as archive:
        members = [
            (info, archive.extractfile(info).read())  # type: ignore[union-attr]
            for info in archive.getmembers()
        ]
    with tarfile.open(output, "w:gz") as archive:
        for info, data in members:
            if info.name.endswith("alpha-0.1.0.tar.gz"):
                data = b"tampered" + data[8:]
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    verify = subprocess.run(
        _sources_args(tmp_path, lock, output, verify=True), capture_output=True, text=True
    )
    assert verify.returncode == 1
    assert "SHA-256 mismatch" in verify.stdout


# ---------------------------------------------------------------------------
# Vendor unpack (the Windows runner's tarfile replacement)
# ---------------------------------------------------------------------------


def _tar_xz(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:xz") as archive:
        for name, data in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
    return path


def test_vendor_unpack_strips_the_archive_root(tmp_path: Path) -> None:
    archive = _tar_xz(
        tmp_path / "source.tar.xz",
        {"libsndfile-1.2.2/CMakeLists.txt": b"cmake", "libsndfile-1.2.2/src/a.c": b"a"},
    )
    destination = tmp_path / "out"

    count = vendor_unpack.unpack(archive, destination, strip=1)

    assert count == 2
    assert (destination / "CMakeLists.txt").read_bytes() == b"cmake"
    assert (destination / "src" / "a.c").read_bytes() == b"a"


def test_vendor_unpack_rejects_a_path_escape(tmp_path: Path) -> None:
    archive = _tar_xz(tmp_path / "source.tar.xz", {"../evil": b"x"})

    with pytest.raises(ValueError, match="unsafe archive path"):
        vendor_unpack.unpack(archive, tmp_path / "out")


# ---------------------------------------------------------------------------
# Release notes and checklist
# ---------------------------------------------------------------------------


def test_release_notes_name_the_shipped_yt_dlp_and_unsigned_caveat() -> None:
    text = release_notes.render(REPO_ROOT)
    assert "StemForge 0.1.0" in text
    assert "yt-dlp 2026.10.26" in text
    assert "unsigned" in text.lower()
    assert "SmartScreen" in text
    assert "SHA256SUMS" in text
    assert "docs/qa/release-checklist.md" in text
    assert "SOURCE_OFFER.md" in text
    assert "Windows figures are unmeasured" in text


# Ticket 11 section 7. Every row must survive in the checklist scaffold.
CHECKLIST_ROWS = (
    "`run.sh` opens the native window",
    "`--preflight --json` is clean on a fully provisioned host",
    "Drag-and-drop WAV, MP3, FLAC, 48 kHz, mono, and a multichannel file",
    "the downmix notice appears",
    "YouTube import: one public, non-age-gated video",
    "consent modal once",
    "disclaimer line",
    "playlist/channel/search URL fails as a field error",
    "Clear YouTube cache",
    "Preview: play, seek, and switch Tracks",
    "only one player sounds at a time",
    "Pitch: +12 and \u221212",
    "A/B between Original and the newest render",
    "version tabs",
    "`master`/`stems` suffixes",
    "Separation: first-run model download and consent",
    "cancel mid-Job",
    "model-missing gate and resubmit",
    "per-stem playback",
    "Instrumental matches the non-vocal mix",
    "Export: WAV 24, FLAC 24, and MP3 320",
    "correct extension handling and filters",
    "tags and cover art are present",
    "Remove Track deletes cached audio after confirmation",
    "Clear cached audio leaves the live Workspace intact",
    "recent files behave as 07 defines",
    "Logs exist under the 07 paths",
    "About shows licence, notices, source offer, and the licences folder",
    "AppImage with and without FUSE",
    "browser fallback when WebKitGTK or GStreamer is missing",
    "the 2.52+ NVIDIA/Skia workaround note",
    "Offline: launch, local import, and Separation with the model already installed",
    "model missing + offline shows the actionable error",
    "Inno installer: per-user, no UAC",
    "Start Menu shortcut (desktop optional)",
    "`MinVersion`",
    "WebView2 present and absent (bootstrapper runs silently)",
    "optional launch",
    "Portable ZIP: WebView2 absent shows the message box with the download link",
    "SmartScreen wording: unsigned build",
    '"Unknown publisher"',
    "`SHA256SUMS` is the integrity check",
    "Native dialogs: open/save filters, extension normalization, overwrite confirmation",
    "no console window",
    "Playback, pitch, separation, export, remove, clear cache, logs, About",
    "Title bar, taskbar, Start Menu, and Alt-Tab icon",
    "Uninstall keeps `%LOCALAPPDATA%\\StemForge` and shows the data path",
    "reinstall reuses it",
    "`--preflight --json` on a clean VM",
    "WebView2/.NET failure exits 5 with the actionable message",
    "close the window during a running Job",
    "no prompt, no veto, partials removed, settings saved",
    "corrupt-file and unsupported-file errors",
    "disk-space warning",
    "job log path from the error banner",
)


def test_release_checklist_contains_every_ticket_11_row() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    missing = [row for row in CHECKLIST_ROWS if row not in text]
    assert missing == []


def test_release_checklist_has_the_budget_and_publish_gate_tables() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    for asset in (
        "Linux onedir",
        "Linux `tar.gz`",
        "Linux `tar.zst`",
        "Linux AppImage",
        "Windows ZIP",
        "Windows setup",
        "Linux build job",
        "Windows build job",
        "Frozen startup",
        "`--smoke-separation`",
    ):
        assert asset in text, asset
    for gate in (
        "The tagged commit is on `main` with `ci.yml` green",
        "`release.yml`'s `prepare`, `linux`, `windows`, and `sources` are green",
        "`verify-release.sh` is green",
        "Both frozen `--smoke-separation` runs pass",
        "The 10 \u00a79 bundle budgets pass",
        "`docs/qa/release-checklist.md` is complete",
        "Version `0.1.0` also has counsel sign-off",
    ):
        assert gate in text, gate
    for column in ("Pass/fail", "OS build", "Date", "Tester", "Artifact SHA-256"):
        assert column in text, column
