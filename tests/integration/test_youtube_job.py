"""Integration: the YouTube import Job through real FFmpeg and a stubbed YoutubeDL.

The stub replaces the network boundary only: it downloads a generated WebM/Opus
file into the Job's staging directory and reports ``requested_downloads``
exactly as yt-dlp does. No test here constructs a real ``YoutubeDL`` or opens a
socket.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.engines.media import FfmpegTool
from stemforge.engines.youtube import YtDlpImporter
from stemforge.jobs import JobRunner, JobState
from stemforge.platform import binaries
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore

pytestmark = pytest.mark.integration

VIDEO_ID = "dQw4w9WgXcQ"
URL = f"https://youtu.be/{VIDEO_ID}?t=5"
TITLE = "Test Video · One"
ARTIST = "Test Uploader"
NATIVE_FORMAT_ID = "251"
NATIVE_ACODEC = "opus"


@pytest.fixture(autouse=True)
def _guard_system_boundaries(no_external_network: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """No external socket and no real ``YoutubeDL``: every test injects a factory."""

    def refuse_youtube_dl(options: object) -> None:
        del options
        raise AssertionError("tests must inject a YoutubeDL factory")

    monkeypatch.setattr("stemforge.engines.youtube._default_youtube_dl", refuse_youtube_dl)


@pytest.fixture(scope="session")
def ffmpeg() -> Path:
    try:
        return binaries.ffmpeg()
    except FileNotFoundError as error:  # pragma: no cover - environment failure
        pytest.fail(f"ffmpeg is required for this layer and was not found: {error}")


@pytest.fixture(scope="session")
def ffprobe() -> Path:
    try:
        return binaries.ffprobe()
    except FileNotFoundError as error:  # pragma: no cover - environment failure
        pytest.fail(f"ffprobe is required for this layer and was not found: {error}")


def _generate_opus_webm(ffmpeg: Path, target: Path, *, channels: str) -> Path:
    expression = "|".join(["sin(2*PI*440*t)"] * (6 if channels == "5.1" else 2))
    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc={expression}:s=48000:d=3:c={channels}",
            "-c:a",
            "libopus",
            "-b:a",
            "160k",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return target


@pytest.fixture(scope="session")
def native_webm(tmp_path_factory: pytest.TempPathFactory, ffmpeg: Path) -> Path:
    target = tmp_path_factory.mktemp("youtube-fixtures") / "downloaded.webm"
    return _generate_opus_webm(ffmpeg, target, channels="stereo")


@pytest.fixture(scope="session")
def surround_webm(tmp_path_factory: pytest.TempPathFactory, ffmpeg: Path) -> Path:
    target = tmp_path_factory.mktemp("youtube-surround-fixtures") / "surround.webm"
    return _generate_opus_webm(ffmpeg, target, channels="5.1")


def _info() -> dict[str, object]:
    return {
        "_type": "video",
        "id": VIDEO_ID,
        "title": TITLE,
        "uploader": ARTIST,
        "duration": 3.0,
        "upload_date": "20240102",
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
    }


@dataclass
class _ScriptedSession:
    """One yt-dlp session: writes the native file the stub owns and reports it."""

    network: YoutubeStub
    options: Mapping[str, object]

    def __enter__(self) -> _ScriptedSession:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
        del url
        self.network.requests.append(download)
        if not download:
            return _info()
        staging = Path(str(dict(self.options["paths"])["home"]))
        target = staging / f"{VIDEO_ID}{self.network.source.suffix}"
        partial = target.with_name(f"{target.name}.part")
        shutil.copyfile(self.network.source, partial)
        hooks = self.options["progress_hooks"]
        assert isinstance(hooks, list)
        assert hooks
        hook = hooks[0]
        assert callable(hook)
        hook(
            {
                "status": "downloading",
                "downloaded_bytes": 1_000_000,
                "total_bytes": 2_000_000,
                "speed": 2_000_000,
            }
        )
        self.network.started.set()
        while self.network.stop is not None:
            time.sleep(0.005)
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 1_000_000,
                    "total_bytes": 2_000_000,
                }
            )
        hook({"status": "finished", "filename": str(target)})
        partial.replace(target)
        return {
            **_info(),
            "requested_downloads": [
                {
                    "filepath": str(target),
                    "format_id": NATIVE_FORMAT_ID,
                    "acodec": NATIVE_ACODEC,
                }
            ],
        }


@dataclass
class YoutubeStub:
    """One test's scripted network boundary.

    Every mutable value belongs to the instance, so a stop event or a request
    log cannot leak into the next test. ``requests`` records each
    ``extract_info`` call by its ``download`` flag: the metadata resolve and the
    media download are the observable behaviour, however many sessions yt-dlp
    opens.
    """

    source: Path
    stop: threading.Event | None = None
    started: threading.Event = field(default_factory=threading.Event)
    requests: list[bool] = field(default_factory=list)

    def __call__(self, options: Mapping[str, object]) -> _ScriptedSession:
        return _ScriptedSession(self, options)

    @property
    def resolves(self) -> int:
        return sum(1 for download in self.requests if not download)

    @property
    def downloads(self) -> int:
        return sum(1 for download in self.requests if download)


@dataclass
class ForbiddenYoutube:
    """A boundary that fails any Job reaching it, so a cache hit proves its silence."""

    constructions: int = 0
    extracts: int = 0

    def __call__(self, options: Mapping[str, object]) -> ForbiddenYoutube:
        del options
        self.constructions += 1
        return self

    def __enter__(self) -> ForbiddenYoutube:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
        del url, download
        self.extracts += 1
        raise AssertionError("a cache hit must not touch YouTube")


@dataclass
class Harness:
    root: Path
    ffmpeg: Path
    ffprobe: Path
    native: Path
    youtube: Any
    delays: list[float] = field(default_factory=list)
    delay: Callable[[float], None] | None = None
    paths: AppPaths = field(init=False)
    settings: SettingsStore = field(init=False)
    store: ArtifactStore = field(init=False)
    runner: JobRunner = field(init=False)
    service: WorkspaceService = field(init=False)

    def __post_init__(self) -> None:
        self.paths = AppPaths.for_home(self.root / "home").ensure()
        self.settings = SettingsStore(self.paths)
        self.store = ArtifactStore(self.paths)
        self.runner = JobRunner(paths=self.paths)
        adapter = YtDlpImporter(
            cache_root=self.paths.cache,
            ffmpeg=self.ffmpeg,
            deno=self.root / "deno",
            youtube_dl_factory=self.youtube,
        )
        self.service = WorkspaceService(
            self.settings,
            paths=self.paths,
            artifacts=self.store,
            runner=self.runner,
            engines=ServiceEngines(
                media=FfmpegTool(ffmpeg_path=self.ffmpeg, ffprobe_path=self.ffprobe),
                youtube=adapter,
            ),
            youtube_delay=self.delay if self.delay is not None else self.delays.append,
        )

    def __enter__(self) -> Harness:
        self.runner.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.runner.stop(timeout=10)


@pytest.fixture
def stub_harness(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, native_webm: Path
) -> Iterator[Harness]:
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=YoutubeStub(source=native_webm),
    ) as harness:
        yield harness


def test_a_youtube_import_commits_a_source_track_and_recent_file(
    stub_harness: Harness,
) -> None:
    harness = stub_harness

    job_id = harness.service.submit_youtube(URL)
    assert harness.runner.wait_idle(timeout=120) is True
    snapshot = harness.runner.snapshot(job_id)

    assert snapshot.state is JobState.SUCCEEDED, snapshot
    assert snapshot.progress == 1.0
    assert snapshot.stage == "Finishing"
    assert snapshot.error_code is None
    assert snapshot.result_keys["source_id"] == f"yt-{VIDEO_ID}"

    source_dir = harness.paths.cache / "sources" / f"yt-{VIDEO_ID}"
    manifest = json.loads((source_dir / "source.json").read_text(encoding="utf-8"))
    assert manifest["origin_kind"] == "youtube"
    assert manifest["origin_url"] == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert manifest["origin"] == manifest["origin_url"]
    assert manifest["video_id"] == VIDEO_ID
    assert manifest["downmixed"] is False
    assert manifest["content_hash"] == manifest["native_sha256"]
    assert manifest["native_bytes"] > 0
    assert manifest["format_id"] == NATIVE_FORMAT_ID
    assert manifest["extractor"] == "youtube"
    assert manifest["extractor_version"]
    assert manifest["tools"]["yt_dlp"]
    assert manifest["tools"]["yt_dlp_ejs"] == "0.8.0"
    assert manifest["tools"]["deno"]

    metadata = manifest["metadata"]
    assert metadata["title"] == TITLE
    assert metadata["artist"] == ARTIST
    assert metadata["date"] == "2024-01-02"
    assert metadata["comment"] == manifest["origin_url"]
    assert metadata["source_url"] == manifest["origin_url"]
    assert metadata["source_id"] == f"yt-{VIDEO_ID}"
    assert metadata["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert metadata["sample_rate"] == 44_100
    assert metadata["channels"] == 2
    assert metadata["album"] == ""

    files = {entry["role"]: entry for entry in manifest["files"]}
    assert {"native", "canonical", "preview"} <= set(files)
    assert "cover" not in files
    for entry in files.values():
        assert (source_dir / entry["path"]).is_file()

    native_entry = files["native"]
    assert native_entry["path"] == "native.webm"
    assert Path(native_entry["path"]).suffix == harness.native.suffix
    assert native_entry["sha256"] == hashlib.sha256(harness.native.read_bytes()).hexdigest()

    tool = FfmpegTool(ffmpeg_path=harness.ffmpeg, ffprobe_path=harness.ffprobe)
    native = tool.probe(source_dir / native_entry["path"])
    assert native.codec == NATIVE_ACODEC
    assert native.format == "matroska,webm"
    canonical = tool.probe(source_dir / files["canonical"]["path"])
    assert canonical.codec == "pcm_f32le"
    assert canonical.sample_rate == 44_100
    assert canonical.channels == 2
    assert canonical.duration_s == pytest.approx(3.0, abs=0.05)
    preview = tool.probe(source_dir / files["preview"]["path"])
    assert preview.codec == "mp3"
    assert preview.duration_s == pytest.approx(3.0, abs=0.05)

    workspace = harness.service.snapshot()
    assert len(workspace) == 1
    track = workspace.tracks[0]
    assert track.title == TITLE
    assert track.source.origin_kind.value == "youtube"
    assert track.source.origin == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert track.source.canonical_path is not None
    assert track.source.canonical_path.is_file()

    recent = harness.service.recent_files()
    assert len(recent) == 1
    assert recent[0].source_id == f"yt-{VIDEO_ID}"
    assert recent[0].origin == f"https://www.youtube.com/watch?v={VIDEO_ID}"
    assert recent[0].origin_kind.value == "youtube"

    # Two 5 s politeness waits between the network phases, injected so the test
    # itself never sleeps.
    assert sum(harness.delays) == pytest.approx(10.0)
    assert not list(harness.paths.cache.rglob("*.part"))
    assert list((harness.paths.cache / "staging").iterdir()) == []
    assert not (harness.paths.cache / "yt-dlp" / job_id).exists()
    # The contract is one metadata resolve and one media download; how many
    # yt-dlp sessions the adapter opens is an internal detail.
    assert harness.youtube.requests == [False, True]
    assert harness.youtube.resolves == 1
    assert harness.youtube.downloads == 1


def test_a_valid_native_file_repairs_missing_derived_files_locally(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, native_webm: Path
) -> None:
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=YoutubeStub(source=native_webm),
    ) as first:
        job_id = first.service.submit_youtube(URL)
        assert first.runner.wait_idle(timeout=120)
        assert first.runner.snapshot(job_id).state is JobState.SUCCEEDED

    source_dir = first.paths.cache / "sources" / f"yt-{VIDEO_ID}"
    manifest = json.loads((source_dir / "source.json").read_text(encoding="utf-8"))
    roles = {entry["role"]: entry["path"] for entry in manifest["files"]}
    canonical = source_dir / roles["canonical"]
    preview = source_dir / roles["preview"]
    canonical.unlink()
    preview.unlink()
    assert first.store.find_source(f"yt-{VIDEO_ID}") is None

    def exploding_delay(seconds: float) -> None:
        raise AssertionError(f"a local repair must not wait ({seconds}s)")

    forbidden = ForbiddenYoutube()
    second = Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=forbidden,
        delay=exploding_delay,
    )
    with second:
        job_id = second.service.submit_youtube(URL)
        assert second.runner.wait_idle(timeout=120)
        snapshot = second.runner.snapshot(job_id)

        assert snapshot.state is JobState.SUCCEEDED, snapshot
        assert snapshot.stage == "Finishing"
        assert forbidden.constructions == 0
        assert forbidden.extracts == 0
        assert second.delays == []
        repaired = second.store.find_source(f"yt-{VIDEO_ID}")
        assert repaired is not None
        assert repaired.path_for("canonical") is not None
        assert repaired.path_for("canonical").is_file()
        assert repaired.path_for("preview") is not None
        assert repaired.path_for("preview").is_file()
        assert second.service.snapshot().tracks[0].title == TITLE


def test_a_six_channel_youtube_source_records_the_downmix(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, surround_webm: Path
) -> None:
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=surround_webm,
        youtube=YoutubeStub(source=surround_webm),
    ) as harness:
        job_id = harness.service.submit_youtube(URL)
        assert harness.runner.wait_idle(timeout=120) is True
        snapshot = harness.runner.snapshot(job_id)
        assert snapshot.state is JobState.SUCCEEDED, snapshot

        source_dir = harness.paths.cache / "sources" / f"yt-{VIDEO_ID}"
        manifest = json.loads((source_dir / "source.json").read_text(encoding="utf-8"))
        assert manifest["downmixed"] is True
        track = harness.service.snapshot().tracks[0]
        assert track.source.downmixed is True
        assert track.source.metadata.channels == 2


def test_a_second_import_of_the_same_link_never_touches_youtube(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, native_webm: Path
) -> None:
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=YoutubeStub(source=native_webm),
    ) as first:
        job_id = first.service.submit_youtube(URL)
        assert first.runner.wait_idle(timeout=120)
        assert first.runner.snapshot(job_id).state is JobState.SUCCEEDED

    # A fresh service over the same home: the committed artifact must satisfy
    # the import, so neither construction nor extraction may be reached.
    def exploding_delay(seconds: float) -> None:
        raise AssertionError(f"a cache hit must not wait ({seconds}s)")

    forbidden = ForbiddenYoutube()
    second = Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=forbidden,
        delay=exploding_delay,
    )

    with second:
        job_id = second.service.submit_youtube(URL)
        assert second.runner.wait_idle(timeout=60)
        snapshot = second.runner.snapshot(job_id)

        assert snapshot.state is JobState.SUCCEEDED, snapshot
        assert snapshot.stage == "Finishing"
        assert snapshot.result_keys["source_id"] == f"yt-{VIDEO_ID}"
        assert forbidden.constructions == 0
        assert forbidden.extracts == 0
        # No pacing either: a cache hit returns before the first wait.
        assert second.delays == []


def test_cancelling_mid_download_leaves_no_staging_or_partial(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, native_webm: Path
) -> None:
    network = YoutubeStub(source=native_webm, stop=threading.Event())
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        native=native_webm,
        youtube=network,
    ) as harness:
        job_id = harness.service.submit_youtube(URL)
        assert network.started.wait(10) is True
        assert harness.runner.cancel(job_id) is True
        network.stop.set()
        assert harness.runner.wait_idle(timeout=30) is True

        snapshot = harness.runner.snapshot(job_id)
        assert snapshot.state is JobState.CANCELLED, snapshot
        assert snapshot.error_code is None
        assert not (harness.paths.cache / "sources" / f"yt-{VIDEO_ID}").exists()
        assert list((harness.paths.cache / "staging").iterdir()) == []
        assert not (harness.paths.cache / "yt-dlp" / job_id).exists()
        assert not list(harness.paths.cache.rglob("*.part"))
        assert harness.service.snapshot().tracks == ()
        assert harness.service.recent_files() == ()


def test_clear_youtube_cache_deletes_only_the_yt_dlp_directory(
    stub_harness: Harness,
) -> None:
    harness = stub_harness
    job_id = harness.service.submit_youtube(URL)
    assert harness.runner.wait_idle(timeout=120)
    assert harness.runner.snapshot(job_id).state is JobState.SUCCEEDED

    local_source = harness.root / "local.webm"
    shutil.copyfile(harness.native, local_source)
    local_job = harness.service.import_local(local_source)
    assert harness.runner.wait_idle(timeout=120)
    assert harness.runner.snapshot(local_job).state is JobState.SUCCEEDED

    youtube_source = harness.paths.cache / "sources" / f"yt-{VIDEO_ID}"
    local_source_dir = next(
        path
        for path in (harness.paths.cache / "sources").iterdir()
        if path.name != f"yt-{VIDEO_ID}"
    )
    youtube_before = _tree_hashes(youtube_source)
    local_before = _tree_hashes(local_source_dir)

    model_file = harness.paths.models / "hub" / "weights.bin"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"pinned weights stand-in")
    deno_file = harness.paths.cache / "deno" / "runtime"
    deno_file.parent.mkdir(parents=True)
    deno_file.write_bytes(b"deno runtime")
    yt_cache = harness.paths.cache / "yt-dlp"
    (yt_cache / "player").mkdir(parents=True, exist_ok=True)
    (yt_cache / "player" / "cache.js").write_bytes(b"cached js")
    (yt_cache / "scratch.part").write_bytes(b"partial")
    recent_before = [(entry.source_id, entry.title) for entry in harness.service.recent_files()]

    freed = harness.service.clear_youtube_cache()

    assert freed >= len(b"cached js") + len(b"partial")
    assert not yt_cache.exists()
    assert deno_file.is_file()
    assert model_file.is_file()
    assert _tree_hashes(youtube_source) == youtube_before
    assert _tree_hashes(local_source_dir) == local_before
    assert harness.store.find_source(f"yt-{VIDEO_ID}") is not None
    assert [(entry.source_id, entry.title) for entry in harness.service.recent_files()] == (
        recent_before
    )


def _tree_hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }
