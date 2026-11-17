"""Integration: the YouTube import Job through real FFmpeg and a stubbed YoutubeDL.

The stub replaces the network boundary only: it writes a generated WAV into the
Job's staging directory and reports ``requested_downloads`` exactly as yt-dlp
does. No test here constructs a real ``YoutubeDL`` or opens a socket.
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
from typing import Any, ClassVar

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


@pytest.fixture(scope="session")
def source_wav(tmp_path_factory: pytest.TempPathFactory, ffmpeg: Path) -> Path:
    target = tmp_path_factory.mktemp("youtube-fixtures") / "downloaded.wav"
    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=sin(2*PI*440*t)|sin(2*PI*440*t):s=44100:d=3:c=stereo",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return target


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


class StubYoutubeDL:
    """The network boundary: writes a known WAV and reports it like yt-dlp."""

    source_wav: Path
    stop: threading.Event | None = None
    started: threading.Event | None = None
    constructed: ClassVar[list[dict[str, object]]] = []

    def __init__(self, options: Mapping[str, object]) -> None:
        type(self).constructed.append(dict(options))
        self.options = options

    def __enter__(self) -> StubYoutubeDL:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
        del url
        if not download:
            return _info()
        staging = Path(str(dict(self.options["paths"])["home"]))
        target = staging / f"{VIDEO_ID}.wav"
        partial = target.with_name(f"{target.name}.part")
        shutil.copyfile(self.source_wav, partial)
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
        if self.started is not None:
            self.started.set()
        while self.stop is not None:
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
                    "format_id": "251",
                    "acodec": "opus",
                }
            ],
        }


class ExplodingYoutubeDL:
    """A ``YoutubeDL`` whose construction is a hard failure."""

    constructed = 0

    def __init__(self, options: Mapping[str, object]) -> None:
        del options
        type(self).constructed += 1
        raise AssertionError("a cache hit must not construct YoutubeDL")


@dataclass
class Harness:
    root: Path
    ffmpeg: Path
    ffprobe: Path
    source_wav: Path
    factory: Callable[[Mapping[str, object]], Any]
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
            youtube_dl_factory=self.factory,
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
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, source_wav: Path
) -> Iterator[Harness]:
    StubYoutubeDL.source_wav = source_wav
    StubYoutubeDL.stop = None
    StubYoutubeDL.started = None
    StubYoutubeDL.constructed = []
    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=StubYoutubeDL,
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
    assert manifest["content_hash"] == manifest["native_sha256"]
    assert manifest["native_bytes"] > 0
    assert manifest["format_id"] == "251"
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

    roles = {entry["role"]: entry["path"] for entry in manifest["files"]}
    assert {"native", "canonical", "preview"} <= set(roles)
    assert "cover" not in roles
    for relative in roles.values():
        assert (source_dir / relative).is_file()

    tool = FfmpegTool(ffmpeg_path=harness.ffmpeg, ffprobe_path=harness.ffprobe)
    canonical = tool.probe(source_dir / roles["canonical"])
    assert canonical.codec == "pcm_f32le"
    assert canonical.sample_rate == 44_100
    assert canonical.channels == 2
    assert canonical.duration_s == pytest.approx(3.0, abs=0.05)
    preview = tool.probe(source_dir / roles["preview"])
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
    # itself never sleeps. The stub saw only a generated WAV.
    assert sum(harness.delays) == pytest.approx(10.0)
    assert not list(harness.paths.cache.rglob("*.part"))
    assert list((harness.paths.cache / "staging").iterdir()) == []
    assert not (harness.paths.cache / "yt-dlp" / job_id).exists()
    assert len(StubYoutubeDL.constructed) == 2


def test_a_valid_native_file_repairs_missing_derived_files_locally(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, source_wav: Path
) -> None:
    StubYoutubeDL.source_wav = source_wav
    StubYoutubeDL.stop = None
    StubYoutubeDL.started = None
    StubYoutubeDL.constructed = []

    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=StubYoutubeDL,
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

    ExplodingYoutubeDL.constructed = 0

    def exploding_delay(seconds: float) -> None:
        raise AssertionError(f"a local repair must not wait ({seconds}s)")

    second = Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=ExplodingYoutubeDL,
        delay=exploding_delay,
    )
    with second:
        job_id = second.service.submit_youtube(URL)
        assert second.runner.wait_idle(timeout=120)
        snapshot = second.runner.snapshot(job_id)

        assert snapshot.state is JobState.SUCCEEDED, snapshot
        assert snapshot.stage == "Finishing"
        assert ExplodingYoutubeDL.constructed == 0
        assert second.delays == []
        repaired = second.store.find_source(f"yt-{VIDEO_ID}")
        assert repaired is not None
        assert repaired.path_for("canonical") is not None
        assert repaired.path_for("canonical").is_file()
        assert repaired.path_for("preview") is not None
        assert repaired.path_for("preview").is_file()
        assert second.service.snapshot().tracks[0].title == TITLE


def test_a_second_import_of_the_same_link_constructs_no_youtube_dl(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, source_wav: Path
) -> None:
    StubYoutubeDL.source_wav = source_wav
    StubYoutubeDL.stop = None
    StubYoutubeDL.started = None
    StubYoutubeDL.constructed = []

    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=StubYoutubeDL,
    ) as first:
        job_id = first.service.submit_youtube(URL)
        assert first.runner.wait_idle(timeout=120)
        assert first.runner.snapshot(job_id).state is JobState.SUCCEEDED

    # A fresh service over the same home: nothing but the committed artifact can
    # satisfy the import, and constructing YoutubeDL now raises.
    ExplodingYoutubeDL.constructed = 0

    def exploding_delay(seconds: float) -> None:
        raise AssertionError(f"a cache hit must not wait ({seconds}s)")

    second = Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=ExplodingYoutubeDL,
        delay=exploding_delay,
    )

    with second:
        job_id = second.service.submit_youtube(URL)
        assert second.runner.wait_idle(timeout=60)
        snapshot = second.runner.snapshot(job_id)

        assert snapshot.state is JobState.SUCCEEDED, snapshot
        assert snapshot.stage == "Finishing"
        assert snapshot.result_keys["source_id"] == f"yt-{VIDEO_ID}"
        assert ExplodingYoutubeDL.constructed == 0
        # No pacing either: a cache hit returns before the first wait.
        assert second.delays == []


def test_cancelling_mid_download_leaves_no_staging_or_partial(
    tmp_path: Path, ffmpeg: Path, ffprobe: Path, source_wav: Path
) -> None:
    started = threading.Event()
    StubYoutubeDL.source_wav = source_wav
    StubYoutubeDL.stop = threading.Event()
    StubYoutubeDL.started = started
    StubYoutubeDL.constructed = []

    with Harness(
        root=tmp_path,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        source_wav=source_wav,
        factory=StubYoutubeDL,
    ) as harness:
        job_id = harness.service.submit_youtube(URL)
        assert started.wait(10) is True
        assert harness.runner.cancel(job_id) is True
        StubYoutubeDL.stop.set()
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

    local_source = harness.root / "local.wav"
    shutil.copyfile(harness.source_wav, local_source)
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
