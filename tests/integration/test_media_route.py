"""The single cache-rooted ``/media`` route over a real localhost socket.

GET 200, partial ranges 206, suffix/multi 416, HEAD 405,
traversal/absolute/encoded/symlink escapes 404, and exact bytes. The server is a
real uvicorn instance bound to an ephemeral 127.0.0.1 port; requests are sent
with ``http.client`` so percent-encoded paths reach the app unnormalised.
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI

from stemforge.integration.media import register_media_route

pytestmark = pytest.mark.integration

CONTENT = bytes(range(64))
CONTENT_LENGTH = len(CONTENT)


@dataclass
class MediaServer:
    root: Path
    port: int
    secret: Path

    def request(
        self, method: str, path: str, *, headers: Mapping[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, headers=dict(headers or {}))
            response = connection.getresponse()
            body = response.read()
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            return response.status, response_headers, body
        finally:
            connection.close()


@pytest.fixture
def media_server(tmp_path: Path) -> Iterator[MediaServer]:
    root = tmp_path / "cache"
    preview = root / "sources" / "source-1" / "original.mp3"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(CONTENT)
    secret = tmp_path / "outside-secret.mp3"
    secret.write_bytes(b"never serve this")
    (preview.parent / "escape.mp3").symlink_to(secret)

    import uvicorn

    application = FastAPI()
    register_media_route(application, root)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            lifespan="off",
            log_level="critical",
            access_log=False,
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="stemforge-media-test",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            thread.join(timeout=5)
            raise RuntimeError("the test media server did not start")
        time.sleep(0.01)
    try:
        yield MediaServer(root=root, port=port, secret=secret)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()


def _preview_path() -> str:
    return "/media/sources/source-1/original.mp3"


def test_get_returns_the_full_file_with_range_metadata(media_server: MediaServer) -> None:
    status, headers, body = media_server.request("GET", _preview_path())

    assert status == 200
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "audio/mpeg"
    assert int(headers["content-length"]) == CONTENT_LENGTH
    assert "content-range" not in headers
    assert body == CONTENT


@pytest.mark.parametrize(
    ("range_header", "expected_start", "expected_end"),
    [
        ("bytes=0-", 0, CONTENT_LENGTH - 1),
        ("bytes=7-", 7, CONTENT_LENGTH - 1),
        ("bytes=0-0", 0, 0),
        ("bytes=0-3", 0, 3),
        ("bytes=10-19", 10, 19),
        (
            f"bytes={CONTENT_LENGTH - 1}-{CONTENT_LENGTH - 1}",
            CONTENT_LENGTH - 1,
            CONTENT_LENGTH - 1,
        ),
        (f"bytes=60-{CONTENT_LENGTH + 500}", 60, CONTENT_LENGTH - 1),
    ],
)
def test_partial_ranges_return_exactly_the_requested_bytes(
    media_server: MediaServer,
    range_header: str,
    expected_start: int,
    expected_end: int,
) -> None:
    status, headers, body = media_server.request(
        "GET", _preview_path(), headers={"Range": range_header}
    )

    assert status == 206
    assert headers["accept-ranges"] == "bytes"
    assert headers["content-type"] == "audio/mpeg"
    assert headers["content-range"] == f"bytes {expected_start}-{expected_end}/{CONTENT_LENGTH}"
    assert int(headers["content-length"]) == expected_end - expected_start + 1
    assert body == CONTENT[expected_start : expected_end + 1]


def test_suffix_ranges_are_416(media_server: MediaServer) -> None:
    for header in ("bytes=-1", "bytes=-10"):
        status, headers, body = media_server.request(
            "GET", _preview_path(), headers={"Range": header}
        )
        assert status == 416
        assert headers["content-range"] == f"bytes */{CONTENT_LENGTH}"
        assert body == b""


def test_multi_range_is_416(media_server: MediaServer) -> None:
    status, headers, body = media_server.request(
        "GET", _preview_path(), headers={"Range": "bytes=0-1,3-4"}
    )

    assert status == 416
    assert headers["content-range"] == f"bytes */{CONTENT_LENGTH}"
    assert body == b""


@pytest.mark.parametrize("header", ["bytes=64-", "bytes=100-200", "bytes=5-2", "items=0-1"])
def test_unsatisfiable_or_invalid_ranges_are_416(media_server: MediaServer, header: str) -> None:
    status, headers, body = media_server.request("GET", _preview_path(), headers={"Range": header})

    assert status == 416
    assert headers["content-range"] == f"bytes */{CONTENT_LENGTH}"
    assert body == b""


def test_head_is_405_with_allow_get(media_server: MediaServer) -> None:
    status, headers, body = media_server.request("HEAD", _preview_path())

    assert status == 405
    assert headers["allow"] == "GET"
    assert body == b""


def test_a_missing_preview_is_404(media_server: MediaServer) -> None:
    status, _headers, body = media_server.request(
        "GET", "/media/sources/source-1/does-not-exist.mp3"
    )

    assert status == 404
    assert body == b""


@pytest.mark.parametrize(
    "path",
    [
        "/media/../outside-secret.mp3",
        "/media/%2e%2e/outside-secret.mp3",
        "/media/..%2foutside-secret.mp3",
        "/media/%2e%2e%2foutside-secret.mp3",
        "/media//etc/passwd",
        "/media/%2fetc%2fpasswd",
        "/media/..%5coutside-secret.mp3",
    ],
)
def test_traversal_absolute_and_encoded_escapes_are_404(
    media_server: MediaServer, path: str
) -> None:
    status, _headers, body = media_server.request("GET", path)

    assert status == 404
    assert body == b""


def test_a_symlink_escape_is_404(media_server: MediaServer) -> None:
    assert media_server.secret.is_file()
    status, _headers, body = media_server.request("GET", "/media/sources/source-1/escape.mp3")

    assert status == 404
    assert body == b""


@pytest.mark.parametrize("path", ["/media/", "/media/sources/", "/media/sources"])
def test_directories_are_404_and_never_listed(media_server: MediaServer, path: str) -> None:
    status, _headers, body = media_server.request("GET", path)

    assert status == 404
    assert body == b""


def test_the_full_and_partial_bodies_hash_as_expected(media_server: MediaServer) -> None:
    """The body is the file, not a re-encoded or truncated stream."""
    full = media_server.request("GET", _preview_path())[2]
    partial = media_server.request("GET", _preview_path(), headers={"Range": "bytes=16-47"})[2]

    assert hashlib.sha256(full).hexdigest() == hashlib.sha256(CONTENT).hexdigest()
    assert partial == CONTENT[16:48]
