"""The licence-tree generator's contract (ticket 22).

The generator reads the committed lock and manifests plus the installed
``.venv`` metadata. Every assertion here is behaviour a release builds on: the
committed tree is reproducible, every locked package is covered, the curated
native notices are present, weights and ``torchaudio`` never appear, and the
source offer is derived from ``packaging/release.toml`` and
``packaging/vendor.lock.json``.
"""

from __future__ import annotations

import shutil
import sys
import tomllib
from pathlib import Path

import pytest
from scripts.generate_notices import (
    GeneratedTree,
    ReleaseConfigError,
    check,
    compare_trees,
    generate,
    load_release,
    release_problems,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_FILES = ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE_OFFER.md")


def _tree(root: Path) -> dict[str, bytes]:
    """Every generated file's relative path and bytes, for equality checks."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _lock_packages() -> list[dict[str, object]]:
    with (REPO_ROOT / "uv.lock").open("rb") as handle:
        return list(tomllib.load(handle)["package"])


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> GeneratedTree:
    """One full generation shared by the read-only assertions."""
    destination = tmp_path_factory.mktemp("notices") / "out"
    return generate(REPO_ROOT, destination)


@pytest.mark.unit
def test_two_generations_are_byte_identical(tmp_path: Path) -> None:
    first = generate(REPO_ROOT, tmp_path / "first")
    second = generate(REPO_ROOT, tmp_path / "second")

    assert _tree(first.root) == _tree(second.root)


@pytest.mark.unit
def test_every_lock_package_has_a_coverage_row(generated: GeneratedTree) -> None:
    rows = {}
    for line in (generated.root / "LICENSES/python/DEPENDENCIES.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells[1]

    missing = [package["name"] for package in _lock_packages() if package["name"] not in rows]
    assert missing == []
    for package in _lock_packages():
        assert rows[package["name"]] == package["version"]


@pytest.mark.unit
def test_every_package_row_declares_an_spdx_id_or_curated_expression(
    generated: GeneratedTree,
) -> None:
    lines = (generated.root / "LICENSES/python/DEPENDENCIES.md").read_text().splitlines()
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 5 and cells[0].startswith("`"):
            assert cells[2], line
            assert cells[2] != "NOASSERTION", line


@pytest.mark.unit
@pytest.mark.parametrize(
    "needle",
    [
        "FFmpeg",
        "Rubber Band",
        "libsndfile",
        "Deno",
        "PyTorch",
        "NumPy",
        "NiceGUI",
        "Quasar",
        "Vue",
        "WebView2",
        "GCC Runtime Library Exception",
        "yt-dlp",
        "yt-dlp-ejs",
    ],
)
def test_curated_entries_are_present(generated: GeneratedTree, needle: str) -> None:
    notices = (generated.root / "THIRD_PARTY_NOTICES.md").read_text()
    assert needle in notices


@pytest.mark.unit
def test_pooled_texts_are_lf_and_named_by_their_content_hash(generated: GeneratedTree) -> None:
    """A CRLF upstream file must not drift through git's autocrlf conversion."""
    import hashlib

    pool = generated.root / "LICENSES/texts"
    files = [path for path in pool.iterdir() if path.is_file()]
    assert files
    for path in files:
        data = path.read_bytes()
        assert b"\r" not in data, path.name
        digest = hashlib.sha256(data).hexdigest()
        assert path.name.endswith(f"-{digest[:12]}.txt") or path.name.endswith(f"-{digest}.txt"), (
            path.name
        )


@pytest.mark.unit
def test_torchaudio_is_absent_everywhere(generated: GeneratedTree) -> None:
    assert "torchaudio" not in (REPO_ROOT / "uv.lock").read_text()
    for path in generated.root.rglob("*"):
        assert "torchaudio" not in path.as_posix()
        if path.is_file():
            assert "torchaudio" not in path.read_bytes().decode("utf-8", errors="ignore")


@pytest.mark.unit
def test_no_model_weights_or_vendor_binaries_are_committed(generated: GeneratedTree) -> None:
    forbidden = (".safetensors", ".pt", ".pth", ".onnx", ".ckpt", ".bin", ".exe", ".dll", ".so")
    for path in generated.root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        assert not name.endswith(forbidden), path


@pytest.mark.unit
def test_source_offer_uses_release_toml_and_lists_every_archive(
    generated: GeneratedTree,
) -> None:
    release = load_release(REPO_ROOT / "packaging/release.toml")
    offer = (generated.root / "SOURCE_OFFER.md").read_text()

    assert release.repo_url in offer
    assert release.releases_url in offer
    assert release.source_offer_contact in offer
    assert "three years" in offer
    assert "2026" in offer
    assert "2029" in offer
    assert "GPL-3.0-only section 6" in offer
    assert "packaging/vendor/SOURCES.md" in offer

    import json

    with (REPO_ROOT / "packaging/vendor.lock.json").open("rb") as handle:
        sources = json.load(handle)["source_offer"]["sources"]
    for entry in sources:
        assert entry["url"] in offer, entry["component"]
        assert entry["sha256"] in offer, entry["component"]


@pytest.mark.unit
def test_notices_reference_the_repository_and_project_licence(generated: GeneratedTree) -> None:
    release = load_release(REPO_ROOT / "packaging/release.toml")
    notices = (generated.root / "THIRD_PARTY_NOTICES.md").read_text()

    assert release.repo_url in notices
    assert release.releases_url in notices
    assert "GPL-3.0-only" in notices
    assert "Corresponding Source" in notices


@pytest.mark.unit
def test_placeholder_values_are_rejected() -> None:
    good = load_release(REPO_ROOT / "packaging/release.toml")
    assert release_problems(good) == ()

    for field, value in (
        ("repo_url", "https://CHANGEME.invalid/stemforge"),
        ("releases_url", "https://example.com/stemforge/releases"),
        ("source_offer_contact", ""),
    ):
        broken = load_release(
            REPO_ROOT / "packaging/release.toml",
            overrides={field: value},
        )
        problems = release_problems(broken)
        assert problems, field
        with pytest.raises(ReleaseConfigError):
            broken.require_valid()


@pytest.mark.unit
def test_check_is_green_on_the_committed_tree(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip(
            "the committed tree is generated on the reference Linux host; "
            "the licensing CI job is ubuntu-24.04"
        )
    result = check(REPO_ROOT, scratch=tmp_path / "scratch")

    assert result.ok is True
    assert result.first_difference is None
    assert result.checked_files > 0


@pytest.mark.unit
def test_check_is_red_on_drift_and_names_the_first_file(tmp_path: Path) -> None:
    generated = generate(REPO_ROOT, tmp_path / "generated")
    committed = tmp_path / "committed"
    shutil.copytree(generated.root, committed)
    drifted = committed / "THIRD_PARTY_NOTICES.md"
    drifted.write_text(
        drifted.read_text() + "\nA hand edit that must be detected.\n",
        encoding="utf-8",
    )

    result = compare_trees(committed=committed, generated=generated.root)

    assert result.ok is False
    assert result.first_difference == "THIRD_PARTY_NOTICES.md"
    assert "THIRD_PARTY_NOTICES.md" in result.summary
    assert "A hand edit" in result.detail


@pytest.mark.unit
def test_check_accepts_a_separate_committed_root(tmp_path: Path) -> None:
    generated = generate(REPO_ROOT, tmp_path / "generated")
    committed = tmp_path / "committed"
    shutil.copytree(generated.root, committed)
    (committed / "SOURCE_OFFER.md").unlink()

    result = check(
        REPO_ROOT,
        committed_root=committed,
        scratch=tmp_path / "scratch",
    )

    assert result.ok is False
    assert result.first_difference == "SOURCE_OFFER.md"


@pytest.mark.unit
def test_generated_paths_are_the_shipped_tree(generated: GeneratedTree) -> None:
    for name in GENERATED_FILES:
        assert (generated.root / name).is_file(), name
    assert (generated.root / "LICENSES/README.md").is_file()
    assert (generated.root / "LICENSES/common/GPL-3.0-only.txt").is_file()
    assert (generated.root / "LICENSES/ffmpeg/BUILDINFO.txt").is_file()
    assert (generated.root / "LICENSES/python/DEPENDENCIES.md").is_file()
    assert (generated.root / "LICENSES/nicegui/frontend/quasar.txt").is_file()
