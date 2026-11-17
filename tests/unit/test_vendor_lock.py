"""The vendor lock: pins, targets, provenance, and the runtime contracts.

``packaging/vendor.lock.json`` is the single source of truth for the vendor
set: ticket 10 §4 names it, the fetch scripts read it, ``platform.binaries``
resolves the installed layout, and ``platform.preflight`` compares the
``--version`` output with it. The expected values below come from CONTEXT.md
(pinned 2026-11-16), not from the lock itself.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from stemforge.platform import binaries, preflight

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = PROJECT_ROOT / "packaging" / "vendor.lock.json"
SOURCES_PATH = PROJECT_ROOT / "packaging" / "vendor" / "SOURCES.md"

TARGETS = ("linux-x86_64", "windows-x64")
TOOLS = ("ffmpeg", "ffprobe", "rubberband", "deno")
COMPONENTS = (*TOOLS, "libsndfile")

HEX64 = re.compile(r"^[0-9a-f]{64}$")

PINNED_VERSIONS = {
    "ffmpeg": "n9.0.2-57-gd838f9f9ae",
    "ffprobe": "n9.0.2-57-gd838f9f9ae",
    "rubberband": "4.0.0",
    "deno": "2.9.8",
    "libsndfile": "1.2.2",
}

PINNED_LICENCES = {
    "ffmpeg": "GPL-3.0-or-later",
    "ffprobe": "GPL-3.0-or-later",
    "rubberband": "GPL-2.0-or-later",
    "deno": "MIT",
    "libsndfile": "LGPL-2.1-or-later",
}

# (component, target) -> (filename, size, sha256). The source archive is the
# pinned artifact for a built component; the downloaded archive is the pin for
# a prebuilt one.
PINNED_ARCHIVES = {
    ("ffmpeg", "linux-x86_64"): (
        "ffmpeg-n9.0.2-57-gd838f9f9ae-linux64-gpl-9.0.tar.xz",
        152_700_372,
        "72836132863e0d40e2af74a8ef59c32b10771aed0c8540b634fb77db2e6e3ae2",
    ),
    ("ffmpeg", "windows-x64"): (
        "ffmpeg-n9.0.2-57-gd838f9f9ae-win64-gpl-9.0.zip",
        194_990_151,
        "11b387a88ad24ee6d78bae7d24a2417834737e6ac00f0a36ca67fead1e924eff",
    ),
    ("ffprobe", "linux-x86_64"): (
        "ffmpeg-n9.0.2-57-gd838f9f9ae-linux64-gpl-9.0.tar.xz",
        152_700_372,
        "72836132863e0d40e2af74a8ef59c32b10771aed0c8540b634fb77db2e6e3ae2",
    ),
    ("ffprobe", "windows-x64"): (
        "ffmpeg-n9.0.2-57-gd838f9f9ae-win64-gpl-9.0.zip",
        194_990_151,
        "11b387a88ad24ee6d78bae7d24a2417834737e6ac00f0a36ca67fead1e924eff",
    ),
    ("deno", "linux-x86_64"): (
        "deno-x86_64-unknown-linux-gnu.zip",
        41_963_064,
        "e890975bc2549807bbcc0b584ffafda72cc7c93349dd99c80483dd784f6c4609",
    ),
    ("deno", "windows-x64"): (
        "deno-x86_64-pc-windows-msvc.zip",
        42_917_472,
        "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf",
    ),
    ("rubberband", "linux-x86_64"): (
        "rubberband-4.0.0.tar.gz",
        290_305,
        "24300f48a8014b7c863b573a9647e61b1b19b37875e2cdd92005e64c6424d266",
    ),
    ("rubberband", "windows-x64"): (
        "rubberband-4.0.0.tar.gz",
        290_305,
        "24300f48a8014b7c863b573a9647e61b1b19b37875e2cdd92005e64c6424d266",
    ),
    ("libsndfile", "linux-x86_64"): (
        "libsndfile-1.2.2.tar.xz",
        730_760,
        "3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e",
    ),
    ("libsndfile", "windows-x64"): (
        "libsndfile-1.2.2.tar.xz",
        730_760,
        "3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e",
    ),
}

EXPECTED_INSTALLS = {
    "linux-x86_64": {
        "ffmpeg": {"ffmpeg/ffmpeg", "ffmpeg/ffprobe"},
        "ffprobe": {"ffmpeg/ffprobe"},
        "deno": {"deno/deno"},
        "rubberband": {"rubberband/rubberband"},
        "libsndfile": {"rubberband/libsndfile.so.1"},
    },
    "windows-x64": {
        "ffmpeg": {"ffmpeg/ffmpeg.exe", "ffmpeg/ffprobe.exe"},
        "ffprobe": {"ffmpeg/ffprobe.exe"},
        "deno": {"deno/deno.exe"},
        "rubberband": {"rubberband/rubberband.exe"},
        "libsndfile": {"rubberband/sndfile.dll"},
    },
}


@pytest.fixture(scope="module")
def lock() -> dict[str, object]:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _components(lock: dict[str, object]) -> dict[str, dict[str, object]]:
    components = lock["components"]
    assert isinstance(components, dict)
    return components


def _target(record: dict[str, object], target: str) -> dict[str, object]:
    targets = record["targets"]
    assert isinstance(targets, dict)
    entry = targets[target]
    assert isinstance(entry, dict)
    return entry


def _archive_fields(entry: dict[str, object]) -> dict[str, object]:
    if entry["kind"] == "built":
        source = entry["source"]
        assert isinstance(source, dict)
        return source
    return entry


def _installs(entry: dict[str, object]) -> list[dict[str, object]]:
    installs = entry["install"]
    assert isinstance(installs, list)
    return [item for item in installs if isinstance(item, dict)]


def test_lock_has_the_documented_top_level_schema(lock: dict[str, object]) -> None:
    assert lock["schema"] == 1
    assert lock["vendor_root"] == "vendor"
    assert set(lock["targets"]) == set(TARGETS)  # type: ignore[arg-type]
    assert set(_components(lock)) == set(COMPONENTS)


def test_every_target_names_its_os_and_architecture(lock: dict[str, object]) -> None:
    targets = lock["targets"]
    assert isinstance(targets, dict)

    assert targets["linux-x86_64"] == {"system": "linux", "arch": "x86_64"}
    assert targets["windows-x64"] == {"system": "win32", "arch": "amd64"}


def test_every_component_pins_the_expected_version_and_licence(lock: dict[str, object]) -> None:
    for name, record in _components(lock).items():
        assert record["version"] == PINNED_VERSIONS[name], name
        assert record["license"] == PINNED_LICENCES[name], name
        texts = record["license_texts"]
        assert isinstance(texts, list), name
        assert texts, name
        assert all(isinstance(text, str) and text for text in texts), name


def test_every_component_has_provenance_and_both_targets(lock: dict[str, object]) -> None:
    for name, record in _components(lock).items():
        provenance = record["provenance"]
        assert isinstance(provenance, dict), name
        assert isinstance(provenance.get("project"), str), name
        assert provenance["project"], name
        assert str(provenance.get("homepage", "")).startswith("https://"), name
        assert set(record["targets"]) == set(TARGETS), name  # type: ignore[arg-type]


def test_tool_version_flags_match_the_preflight_parsers(lock: dict[str, object]) -> None:
    for name in ("ffmpeg", "ffprobe"):
        assert _components(lock)[name]["version_flag"] == "-version"
    for name in ("rubberband", "deno"):
        assert _components(lock)[name]["version_flag"] == "--version"
    assert _components(lock)["libsndfile"]["version_flag"] is None


def test_every_target_records_a_valid_sha256_url_and_size(lock: dict[str, object]) -> None:
    for name, record in _components(lock).items():
        for target in TARGETS:
            entry = _target(record, target)
            assert entry["kind"] in {"archive", "built"}, (name, target)
            archive = _archive_fields(entry)
            url = archive["url"]
            assert isinstance(url, str), (name, target)
            assert url.startswith("https://"), (name, target)
            filename = archive["filename"]
            assert isinstance(filename, str), (name, target)
            assert filename, (name, target)
            assert HEX64.match(str(archive["sha256"])), (name, target)
            assert isinstance(archive["size"], int), (name, target)
            assert archive["size"] > 0, (name, target)
            expected = PINNED_ARCHIVES[(name, target)]
            assert (filename, archive["size"], archive["sha256"]) == expected, (name, target)


def test_every_target_declares_archive_or_recorded_build_steps(lock: dict[str, object]) -> None:
    for name, record in _components(lock).items():
        for target in TARGETS:
            entry = _target(record, target)
            if entry["kind"] == "archive":
                assert entry["archive_format"] in {"tar.xz", "zip"}, (name, target)
                assert "build" not in entry
                continue
            build = entry["build"]
            assert isinstance(build, dict), (name, target)
            configure = build["configure"]
            assert isinstance(configure, list), (name, target)
            assert configure, (name, target)
            assert all(isinstance(part, str) and part for part in configure), (name, target)
            assert isinstance(build.get("system"), str), (name, target)
            assert build["system"], (name, target)
            assert isinstance(build.get("notes"), str), (name, target)
            assert build["notes"], (name, target)
            assert "archive_format" in build, (name, target)


def test_installs_are_relative_paths_under_vendor(lock: dict[str, object]) -> None:
    for name, record in _components(lock).items():
        for target in TARGETS:
            entry = _target(record, target)
            installs = _installs(entry)
            assert installs, (name, target)
            for item in installs:
                destination = Path(str(item["to"]))
                assert not destination.is_absolute(), (name, target, destination)
                assert ".." not in destination.parts, (name, target, destination)
                assert destination.parts[0] in {"ffmpeg", "deno", "rubberband"}, (
                    name,
                    target,
                    destination,
                )
                assert isinstance(item["from"], str), (name, target)
                assert item["from"], (name, target)
                assert isinstance(item["executable"], bool), (name, target)
                if item.get("sha256") is not None:
                    assert HEX64.match(str(item["sha256"])), (name, target)


def test_the_installed_layout_matches_binaries_and_ticket_10(lock: dict[str, object]) -> None:
    for target in TARGETS:
        system = "win32" if target.startswith("windows") else "linux"
        destinations: dict[str, set[str]] = {}
        for name, record in _components(lock).items():
            destinations[name] = {str(item["to"]) for item in _installs(_target(record, target))}
        assert destinations == EXPECTED_INSTALLS[target], target

        for tool in TOOLS:
            expected = binaries.vendor_path(tool, system=system).as_posix()
            assert expected in destinations[tool], (target, tool)
        library = preflight.shared_library_name("rubberband", system=system)
        assert f"rubberband/{library}" in destinations["libsndfile"], target


def test_the_lock_has_no_model_weights_and_no_hugging_face_url(lock: dict[str, object]) -> None:
    weight_suffixes = {".safetensors", ".pt", ".pth", ".ckpt", ".onnx", ".bin"}
    for name, record in _components(lock).items():
        for target in TARGETS:
            for item in _installs(_target(record, target)):
                destination = Path(str(item["to"]))
                assert destination.suffix.lower() not in weight_suffixes, (name, target)

    text = LOCK_PATH.read_text(encoding="utf-8")
    assert "huggingface.co" not in text
    assert "adefossez" not in text


def test_source_offer_records_the_written_offer_and_every_archive(lock: dict[str, object]) -> None:
    offer = lock["source_offer"]
    assert isinstance(offer, dict)
    assert offer["offer_years"] == 3
    assert offer["contact"] == "https://github.com/alexmarincu/stemforge/issues"
    assert offer["release_page"] == "https://github.com/alexmarincu/stemforge/releases"
    assert "GPL-3.0" in str(offer["written_offer"])
    assert "three years" in str(offer["written_offer"])

    sources = offer["sources"]
    assert isinstance(sources, list)
    assert sources
    recorded = {
        (str(item["url"]), str(item["sha256"])) for item in sources if isinstance(item, dict)
    }
    for name, record in _components(lock).items():
        for target in TARGETS:
            entry = _target(record, target)
            archive = _archive_fields(entry)
            assert (archive["url"], archive["sha256"]) in recorded, (name, target)


def test_preflight_reads_these_versions(lock: dict[str, object]) -> None:
    versions = preflight.read_vendor_lock_versions(LOCK_PATH)
    assert versions is not None
    for tool in preflight.VENDOR_TOOLS:
        assert versions[tool] == _components(lock)[tool]["version"]


def test_sources_markdown_captures_the_ticket_22_inputs() -> None:
    text = SOURCES_PATH.read_text(encoding="utf-8")

    for name in COMPONENTS:
        assert f"`{name}`" in text, name
        assert PINNED_VERSIONS[name] in text, name
        assert PINNED_LICENCES[name] in text, name
    for _key, (filename, _size, sha256) in PINNED_ARCHIVES.items():
        assert filename in text
        assert sha256 in text
    assert "three years" in text
    assert "https://github.com/alexmarincu/stemforge/issues" in text
    assert "ticket 22" in text.lower()
