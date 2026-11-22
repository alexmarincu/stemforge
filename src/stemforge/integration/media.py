"""One ``/media`` route mounted over the artifact cache root.

The supported HTTP surface:

* ``GET`` full file -> ``200`` with ``Accept-Ranges: bytes``, ``Content-Length``
  and ``Content-Type: audio/mpeg``.
* ``Range: bytes=N-`` and ``bytes=N-M`` -> ``206`` with an exact
  ``Content-Range`` and body.
* ``HEAD`` -> ``405``; suffix (``bytes=-N``), multi-range, malformed, and
  unsatisfiable requests -> ``416``; traversal, symlink escapes, directories,
  and missing files -> ``404``.

The route is read-only and confined to the resolved cache root. The pure
:func:`serve_media` decision function is independent of FastAPI so the semantics
are tested directly; :func:`register_media_route` is the thin adapter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

MEDIA_PATH: Final = "/media"
MEDIA_CONTENT_TYPE: Final = "audio/mpeg"
DEFAULT_CHUNK_SIZE: Final = 64 * 1024

_RANGE_PATTERN: Final = re.compile(
    r"^bytes\s*=\s*(\d+)\s*-\s*(\d*)$",
    re.IGNORECASE,
)


class RangeNotSatisfiable(ValueError):
    """The ``Range`` header is unsupported by this route or cannot be satisfied."""


@dataclass(frozen=True, slots=True)
class ByteRange:
    """One inclusive byte interval inside a file."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class MediaResponse:
    """The framework-free result of one media request."""

    status_code: int
    headers: Mapping[str, str]
    body: Iterable[bytes] = ()


def parse_range_header(value: str | None, size: int) -> ByteRange | None:
    """Parse the one supported range form.

    ``None`` means "serve the whole file". Anything other than a single
    ``bytes=N-`` or ``bytes=N-M`` range raises :class:`RangeNotSatisfiable`,
    including suffix (``bytes=-N``) and multi-range forms.
    """
    if value is None:
        return None
    if size <= 0:
        raise RangeNotSatisfiable("an empty file has no satisfiable range")
    match = _RANGE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise RangeNotSatisfiable("only a single bytes=N- or bytes=N-M range is supported")
    try:
        start = int(match.group(1))
        end_text = match.group(2)
        end = size - 1 if not end_text else int(end_text)
    except ValueError as exc:  # absurdly long integers hit Python's digit limit
        raise RangeNotSatisfiable("the range is not a valid integer interval") from exc
    if start >= size or end < start:
        raise RangeNotSatisfiable("the range starts beyond the file or ends before it starts")
    return ByteRange(start, min(end, size - 1))


def serve_media(root: Path, request_path: str, range_header: str | None) -> MediaResponse:
    """Decide one media response for a request path relative to ``root``."""
    candidate = _confine(root, request_path)
    if candidate is None:
        return MediaResponse(404, {})
    try:
        if not candidate.is_file():
            return MediaResponse(404, {})
        size = candidate.stat().st_size
    except OSError:
        return MediaResponse(404, {})

    try:
        byte_range = parse_range_header(range_header, size)
    except RangeNotSatisfiable:
        return MediaResponse(
            416,
            {
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{size}",
            },
        )

    if byte_range is None:
        start, end = 0, size - 1
        status_code = 200
    else:
        start, end = byte_range.start, byte_range.end
        status_code = 206

    length = max(0, end - start + 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": MEDIA_CONTENT_TYPE,
    }
    if byte_range is not None:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return MediaResponse(status_code, headers, _iter_file(candidate, start, length))


def register_media_route(app: FastAPI, root: Path) -> None:
    """Register the GET/HEAD pair on a FastAPI/NiceGUI app.

    The explicit HEAD route is registered first because Starlette adds HEAD to a
    GET route automatically; HEAD must return 405 instead of a bodyless 200.
    """
    cache_root = Path(root)

    @app.head(f"{MEDIA_PATH}/{{filename:path}}")
    def head_media(filename: str) -> Response:
        del filename
        return Response(status_code=405, headers={"Allow": "GET"})

    @app.get(f"{MEDIA_PATH}/{{filename:path}}")
    def get_media(request: Request, filename: str) -> Response:
        result = serve_media(cache_root, filename, request.headers.get("range"))
        if result.status_code not in (200, 206):
            return Response(status_code=result.status_code, headers=dict(result.headers))
        return StreamingResponse(
            result.body,
            status_code=result.status_code,
            headers=dict(result.headers),
        )


def _confine(root: Path, request_path: str) -> Path | None:
    """Resolve a request path and refuse anything outside the cache root."""
    if not request_path or "\x00" in request_path:
        return None
    try:
        resolved_root = Path(root).resolve(strict=True)
    except OSError:
        return None
    try:
        candidate = (resolved_root / request_path).resolve(strict=False)
    except (OSError, ValueError):
        return None
    if candidate == resolved_root or not candidate.is_relative_to(resolved_root):
        return None
    return candidate


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    if length <= 0:
        return iter(())
    return _read_chunks(path, start, length)


def _read_chunks(path: Path, start: int, length: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(DEFAULT_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
