"""The yt-dlp invocation contract and error classification (ticket 16, 09 §4/§8).

No test here constructs a ``YoutubeDL`` or opens a socket. The option table is
checked against ticket 09 §4, and classification is checked against real yt-dlp
exception classes and the message patterns the adapter must recognise.
"""

from __future__ import annotations

import ast
import logging
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from stemforge import errors
from stemforge.engines.youtube import build_youtube_dl_options, classify_youtube_error
from stemforge.errors import StemForgeError

pytestmark = pytest.mark.unit

FFMPEG = Path("/opt/stemforge/vendor/ffmpeg/ffmpeg")
DENO = Path("/opt/stemforge/vendor/deno/deno")
SECRET = "raw extractor text that must never reach the UI"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any connection attempt in this module fails the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the option/classifier tests must not open a connection")

    def refuse_youtube_dl(options: object) -> None:
        del options
        raise AssertionError("these tests must not construct a real YoutubeDL")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr("stemforge.engines.youtube._default_youtube_dl", refuse_youtube_dl)


def _options(tmp_path: Path, *, hooks: list[object] | None = None) -> dict[str, object]:
    home = tmp_path / "cache" / "yt-dlp" / "job-1"
    home.mkdir(parents=True)
    return build_youtube_dl_options(
        home=home,
        cache_root=tmp_path / "cache",
        ffmpeg=FFMPEG,
        deno=DENO,
        log=logging.getLogger("test.youtube"),
        progress_hooks=list(hooks or []),
    )


def test_the_option_table_matches_ticket_09(tmp_path: Path) -> None:
    logger = logging.getLogger("test.youtube")
    cache = tmp_path / "cache"
    home = cache / "yt-dlp" / "job-1"
    home.mkdir(parents=True)

    options = build_youtube_dl_options(
        home=home,
        cache_root=cache,
        ffmpeg=FFMPEG,
        deno=DENO,
        log=logger,
        progress_hooks=[],
    )

    assert options["format"] == "bestaudio/best"
    assert options["noplaylist"] is True
    assert options["outtmpl"] == {"default": "%(id)s.%(ext)s"}
    assert options["paths"] == {"home": str(home)}
    assert home.is_relative_to(cache / "yt-dlp")
    assert options["cachedir"] == str(cache / "yt-dlp")
    assert options["windowsfilenames"] is True
    assert options["trim_file_name"] == 120
    assert options["quiet"] is True
    assert options["no_warnings"] is False
    assert options["noprogress"] is True
    assert options["ignoreerrors"] is False
    assert options["retries"] == 3
    assert options["fragment_retries"] == 3
    assert options["extractor_retries"] == 3
    assert options["socket_timeout"] == 20
    assert options["progress_delta"] == 0.5
    assert options["warn_when_outdated"] is False
    assert options["ffmpeg_location"] == str(FFMPEG.parent)
    assert options["js_runtimes"] == {"deno": {"path": str(DENO)}}
    assert options["postprocessors"] == []
    assert options["progress_hooks"] == []
    assert options["logger"] is logger


def test_the_option_table_has_no_cookies_accounts_or_proxy(tmp_path: Path) -> None:
    options = _options(tmp_path)

    for forbidden in (
        "cookiefile",
        "cookiesfrombrowser",
        "cookies",
        "username",
        "password",
        "videopassword",
        "netrc",
        "proxy",
    ):
        assert forbidden not in options, forbidden
    assert all(
        not (
            isinstance(postprocessor, dict)
            and "FFmpegExtractAudio" in str(postprocessor.get("key", ""))
        )
        for postprocessor in options["postprocessors"]  # type: ignore[union-attr]
    )


def test_progress_hooks_are_passed_through_for_the_download(tmp_path: Path) -> None:
    def hook(data: dict[str, object]) -> None:
        del data

    options = _options(tmp_path, hooks=[hook])

    assert options["progress_hooks"] == [hook]


def test_the_download_hook_raises_download_cancelled_when_the_token_is_set(
    tmp_path: Path,
) -> None:
    from yt_dlp.utils import DownloadCancelled

    from stemforge.domain.cancellation import CancelToken
    from stemforge.engines.youtube import YouTubeVideo, YtDlpImporter
    from stemforge.errors import JobCancelled

    token = CancelToken()
    seen: dict[str, object] = {}

    class Stub:
        def __init__(self, options: dict[str, object]) -> None:
            seen["options"] = options

        def __enter__(self) -> Stub:
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = False) -> dict[str, object]:
            del url, download
            token.cancel()
            hooks = seen["options"]["progress_hooks"]  # type: ignore[index]
            assert isinstance(hooks, list)
            try:
                hooks[0](
                    {
                        "status": "downloading",
                        "downloaded_bytes": 10,
                        "total_bytes": 100,
                    }
                )
            except DownloadCancelled:
                seen["hook_cancelled"] = True
                raise
            raise AssertionError("the hook ignored the cancel token")

    adapter = YtDlpImporter(
        cache_root=tmp_path / "cache",
        ffmpeg=Path("/bin/true"),
        deno=Path("/bin/true"),
        youtube_dl_factory=Stub,  # type: ignore[arg-type]
    )

    with pytest.raises(JobCancelled):
        adapter.download(
            YouTubeVideo(video_id="dQw4w9WgXcQ", canonical_url="https://youtu.be/dQw4w9WgXcQ"),
            tmp_path / "download",
            cancel=token,
            log=logging.getLogger("test.youtube"),
            progress=lambda fraction, detail: None,
        )

    assert seen["hook_cancelled"] is True


def test_the_adapter_source_has_no_cookie_or_login_path() -> None:
    source = Path("src/stemforge/engines/youtube.py").read_text(encoding="utf-8")

    for forbidden in (
        "cookiefile",
        "cookiesfrombrowser",
        "cookies-from-browser",
    ):
        assert forbidden not in source, forbidden


def test_the_adapter_lives_with_the_parser_and_imports_yt_dlp_lazily() -> None:
    source = Path("src/stemforge/engines/youtube.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_imports = {
        alias.name.split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "yt_dlp" not in module_imports

    all_imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "yt_dlp" in all_imports


def _mapped(exc: BaseException) -> StemForgeError:
    error = classify_youtube_error(exc)
    assert isinstance(error, StemForgeError)
    assert error.code in errors.ALL_CODES
    assert SECRET not in error.message
    assert SECRET not in (error.detail or "")
    return error


def test_private_removed_members_and_geo_are_one_unavailable_class() -> None:
    from yt_dlp.utils import DownloadError, GeoRestrictedError, UnavailableVideoError

    cases: list[BaseException] = [
        DownloadError(f"ERROR: [youtube] abc: Private video. {SECRET}"),
        DownloadError(f"ERROR: [youtube] abc: Video unavailable. {SECRET}"),
        DownloadError(f"ERROR: [youtube] abc: Join this channel to get access. {SECRET}"),
        DownloadError(f"ERROR: [youtube] abc: This video is DRM protected. {SECRET}"),
        GeoRestrictedError(f"not available in your country {SECRET}"),
        UnavailableVideoError(f"video unavailable {SECRET}"),
        DownloadError(f"HTTP Error 404: Not Found {SECRET}"),
    ]

    for exc in cases:
        assert _mapped(exc).code == errors.IMPORT_YOUTUBE_UNAVAILABLE, repr(exc)


def test_age_restricted_is_its_own_code() -> None:
    from yt_dlp.utils import DownloadError

    for message in (
        "Sign in to confirm your age. This video may be inappropriate for some users.",
        "This video is age-restricted",
        "age_verification_required",
    ):
        error = _mapped(DownloadError(f"ERROR: {message} {SECRET}"))
        assert error.code == errors.IMPORT_YOUTUBE_AGE_RESTRICTED


def test_live_and_upcoming_are_refused() -> None:
    from yt_dlp.utils import DownloadError

    for message in (
        "This live event will begin in 3 hours.",
        "ERROR: This video is live",
        "The premiere will start soon",
        "This stream is upcoming",
    ):
        assert _mapped(DownloadError(f"{message} {SECRET}")).code == errors.IMPORT_YOUTUBE_LIVE


def test_bot_checks_and_429_are_rate_limited() -> None:
    from yt_dlp.utils import DownloadError

    for message in (
        "Sign in to confirm you’re not a bot. Use --cookies-from-browser",  # noqa: RUF001
        "HTTP Error 429: Too Many Requests",
        "YouTube is limiting requests, try again later",
        "Too many requests from this IP",
    ):
        assert (
            _mapped(DownloadError(f"{message} {SECRET}")).code == errors.IMPORT_YOUTUBE_RATE_LIMITED
        )


def test_extractor_and_javascript_failures_are_extractor_outdated() -> None:
    from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

    cases = [
        ExtractorError(f"Unable to extract player response {SECRET}"),
        DownloadError(f"ERROR: No video formats found {SECRET}"),
        DownloadError(f"nsig extraction failed {SECRET}"),
        UnsupportedError(f"Unsupported URL: {SECRET}"),
        DownloadError(f"no suitable javascript runtime {SECRET}"),
        ExtractorError(f"Failed to extract any player response {SECRET}"),
    ]

    for exc in cases:
        assert _mapped(exc).code == errors.IMPORT_YOUTUBE_EXTRACTOR_OUTDATED, repr(exc)


def test_network_failures_are_import_network() -> None:
    from yt_dlp.networking.exceptions import CertificateVerifyError, TransportError

    cases: list[BaseException] = [
        TransportError(f"timed out {SECRET}"),
        TransportError(f"connection reset by peer {SECRET}"),
        CertificateVerifyError(f"certificate verify failed {SECRET}"),
        socket.gaierror(f"Name or service not known {SECRET}"),
        ConnectionError(f"network is unreachable {SECRET}"),
        TimeoutError(f"the read operation timed out {SECRET}"),
    ]

    for exc in cases:
        assert _mapped(exc).code == errors.IMPORT_NETWORK, repr(exc)


def test_anything_else_is_download_failed() -> None:
    from yt_dlp.utils import DownloadError

    error = _mapped(DownloadError(f"ERROR: something unexpected {SECRET}"))

    assert error.code == errors.IMPORT_DOWNLOAD_FAILED
    assert error.retryable is True


def test_classification_keeps_a_stable_reason_and_marks_retryability() -> None:
    from yt_dlp.utils import DownloadError

    rate = _mapped(DownloadError("HTTP Error 429: Too Many Requests"))
    network = _mapped(socket.gaierror("Name or service not known"))
    unavailable = _mapped(DownloadError("Private video"))

    assert rate.retryable is True
    assert network.retryable is True
    assert unavailable.retryable is False
    assert rate.detail is not None
    assert network.detail is not None
    assert unavailable.detail is not None
    assert rate.detail.startswith("reason=")


def test_importing_the_engine_does_not_import_yt_dlp() -> None:
    """The guard: the adapter imports yt-dlp lazily, inside a call."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import stemforge.engines.youtube; "
            "assert 'yt_dlp' not in sys.modules, sorted(m for m in sys.modules if 'yt' in m)",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
