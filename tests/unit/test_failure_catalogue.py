"""Every error code has a trigger, a headline, and one recovery action.

The trigger table drives the real production owner for each code: a parser, a
classifier, an engine mapping, the runner fallback, or the preflight fatal
mapping. ``internal.unexpected`` is included as the deliberate fallback for an
unknown exception; ``playback.unavailable`` is a browser-event UI state and is
referenced to its headless page trigger.
"""

from __future__ import annotations

import errno
import hashlib
import io
import logging
import re
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stemforge import __main__ as entry
from stemforge import artifacts as artifact_module
from stemforge import errors
from stemforge.application import ServiceEngines, WorkspaceService
from stemforge.artifacts import ArtifactStore
from stemforge.domain.cancellation import CancelToken
from stemforge.domain.models import JobKind
from stemforge.engines import media, pitch, separation, youtube
from stemforge.engines.media import MediaInfo
from stemforge.errors import JobCancelled, StemForgeError
from stemforge.integration import shell
from stemforge.integration.shell import SystemPathOpener
from stemforge.jobs.job import JobContext
from stemforge.models import ModelConsent, ModelStore
from stemforge.pipeline import export_job, import_job, model_job
from stemforge.platform import preflight
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore
from stemforge.ui import formatting
from stemforge.worker_protocol import ProtocolError, WorkerEvent

pytestmark = pytest.mark.unit

LOG = logging.getLogger("test.failure-catalogue")
_METADATA = youtube.YouTubeMetadata(
    video_id="abc",
    title="Title",
    artist="Artist",
    canonical_url="https://www.youtube.com/watch?v=abc",
    duration_s=60.0,
)

EXPLICIT_EXCEPTIONS = {
    errors.PLAYBACK_UNAVAILABLE: (
        "UI state raised by the browser <audio> error event; "
        "tests/ui/test_preview.py::"
        "test_deleting_the_playing_preview_stops_and_reports_unavailable"
    ),
    errors.LOGGING_OPEN_FAILED: (
        "Settings action raised when the injected path opener fails; "
        "tests/ui/test_about_licensing.py::"
        "test_a_failing_opener_is_a_coded_message_not_a_traceback"
    ),
}


@dataclass(frozen=True, slots=True)
class Probe:
    paths: AppPaths
    tmp: Path
    monkeypatch: pytest.MonkeyPatch


def _raised(call: Callable[[], object]) -> str:
    with pytest.raises(StemForgeError) as raised:
        call()
    return raised.value.code


def _context() -> JobContext:
    return JobContext(
        job_id="catalogue-job",
        kind=JobKind.IMPORT_LOCAL,
        cancel=CancelToken(),
        log=LOG,
        update=lambda *args, **kwargs: None,
    )


def _missing_source_file(probe: Probe) -> str:
    return _raised(lambda: import_job._require_readable(probe.tmp / "missing.wav"))


class _LongMedia:
    def probe(self, path: Path) -> MediaInfo:
        return MediaInfo("wav", 44_100, 2, 1_801.0, "wav", {})


def _too_long(probe: Probe) -> str:
    artifacts = ArtifactStore(probe.paths)
    source = probe.tmp / "long.wav"
    source.write_bytes(b"not really audio")
    return _raised(
        lambda: import_job.run_local_import(
            _context(),
            source,
            artifacts=artifacts,
            media=_LongMedia(),  # type: ignore[arg-type]
            finalize=lambda source: None,  # type: ignore[arg-type,return-value]
        )
    )


def _no_audio(probe: Probe) -> str:
    del probe
    return _raised(
        lambda: media._require_audio({"streams": [{"codec_type": "video"}]}, Path("video.mp4"))
    )


def _unsupported_source(probe: Probe) -> str:
    del probe
    return _raised(lambda: youtube.parse_youtube_url("https://vimeo.com/123"))


def _decode_failed(probe: Probe) -> str:
    tool = media.FfmpegTool(ffmpeg_path=probe.tmp / "ffmpeg", ffprobe_path=probe.tmp / "ffprobe")
    probe.monkeypatch.setattr(
        tool, "probe", lambda _path: MediaInfo("wav", 44_100, 2, 3.0, "wav", {})
    )
    probe.monkeypatch.setattr(
        media.process,
        "run_capture",
        lambda *args, **kwargs: type(
            "Run", (), {"ok": False, "stdout": "", "stderr": "ffmpeg failed"}
        )(),
    )
    return _raised(
        lambda: tool.decode_canonical(
            probe.tmp / "in.wav",
            probe.tmp / "out" / "canonical.wav",
            cancel=CancelToken(),
            log=LOG,
        )
    )


def _model_missing(probe: Probe) -> str:
    return _raised(lambda: ModelStore(probe.paths).require_verified())


def _model_download_failed(probe: Probe) -> str:
    store = ModelStore(probe.paths)

    def transport(url: str) -> object:
        raise urllib.error.URLError(OSError("offline"))

    job = model_job.ModelInstallJob(
        store=store,
        consent=ModelConsent(version=1, acknowledged=True, accepted_at=datetime.now(UTC)),
        transport=transport,  # type: ignore[arg-type]
    )
    return _raised(lambda: job.run(_context()))


class _FakeProcess:
    """A child that exited hard before printing a protocol event."""

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.pid = 4242
        self.returncode = 9

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode


def _worker_crashed(probe: Probe) -> str:
    model = probe.tmp / "weights.bin"
    model.write_bytes(b"weights")
    engine = separation.WorkerSeparationEngine(argv=["stemforge"])
    probe.monkeypatch.setattr(separation.process, "spawn", lambda *a, **k: _FakeProcess())
    return _raised(
        lambda: engine.separate(
            probe.tmp / "canonical.wav",
            probe.tmp / "stems",
            model_path=model,
            expected_duration_s=1.0,
            progress=lambda *args: None,
            cancel=CancelToken(),
            log=LOG,
        )
    )


def _worker_engine_error(probe: Probe) -> str:
    del probe
    return separation._worker_error(
        WorkerEvent("error", code=errors.SEPARATION_ENGINE_ERROR), ""
    ).code


def _worker_output_invalid(probe: Probe) -> str:
    del probe
    return separation._worker_error(
        WorkerEvent("error", code=errors.SEPARATION_OUTPUT_INVALID), ""
    ).code


def _protocol_mismatch(probe: Probe) -> str:
    del probe
    return separation._protocol_error(ProtocolError("bad line"), "", 0).code


def _pitch_engine_missing(probe: Probe) -> str:
    del probe
    return pitch._engine_missing("missing", None).code


def _pitch_render_failed(probe: Probe) -> str:
    del probe
    return pitch._render_failed("boom").code


def _pitch_output_invalid(probe: Probe) -> str:
    del probe
    return pitch._output_invalid(Path("render.wav"), "bad").code


def _probe_failed(probe: Probe) -> str:
    del probe
    return media._probe_error(Path("clip.wav"), "bad").code


def _encode_failed(probe: Probe) -> str:
    tool = media.FfmpegTool(ffmpeg_path=probe.tmp / "ffmpeg", ffprobe_path=probe.tmp / "ffprobe")
    probe.monkeypatch.setattr(
        tool, "probe", lambda _path: MediaInfo("wav", 44_100, 2, 1.0, "wav", {})
    )
    return _raised(
        lambda: tool.encode_export(
            probe.tmp / "in.wav",
            probe.tmp / "out.flac",
            fmt=media.ExportFormat.FLAC24,
            metadata=media.CanonicalMetadata(title="T"),
            gain_db=0.0,
            cover=probe.tmp / "missing-cover.png",
            cancel=CancelToken(),
            log=LOG,
        )
    )


def _validation_failed(probe: Probe) -> str:
    del probe
    return media._validation_error(Path("out.wav"), "decode", "channels=1").code


def _write_failed(probe: Probe) -> str:
    del probe
    return export_job._destination_error(
        PermissionError(errno.EACCES, "denied"), Path("/dest")
    ).code


def _disk_full(probe: Probe) -> str:
    del probe
    return _raised(lambda: artifact_module._raise_wrapped(OSError(errno.ENOSPC, "full"), "write"))


def _cache_full(probe: Probe) -> str:
    artifacts = ArtifactStore(probe.paths)
    staging = artifacts.stage("catalogue-source")
    canonical = staging / "canonical.wav"
    canonical.write_bytes(b"canonical")
    data = canonical.read_bytes()
    artifacts.commit_source(
        staging,
        "src-catalogue",
        {
            "schema_version": 1,
            "kind": "source",
            "source_id": "src-catalogue",
            "created_at": datetime.now(UTC).isoformat(),
            "files": [
                {
                    "path": "canonical.wav",
                    "role": "canonical",
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            ],
        },
    )
    capped = ArtifactStore(probe.paths, cap_bytes=0)
    capped.retain("src-catalogue")
    return _raised(lambda: capped.enforce_cap())


def _permission_denied(probe: Probe) -> str:
    del probe
    return _raised(lambda: artifact_module._raise_wrapped(OSError(errno.EACCES, "denied"), "write"))


def _internal_unexpected(probe: Probe) -> str:
    service = WorkspaceService(
        SettingsStore(probe.paths),
        paths=probe.paths,
        engines=ServiceEngines(),
    )
    return _raised(lambda: service.import_local(probe.tmp / "unused.wav"))


def _platform_unsupported(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("platform", "fail", "unsupported", True),),
            system="linux",
        )
    )


def _window_unavailable(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("window", "fail", "no GTK", True),),
            system="linux",
        )
    )


def _webview2_missing(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("window", "fail", "no WebView2", True),),
            system="win32",
        )
    )


def _bundle_damaged(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("bundle", "fail", "missing ffmpeg", True),),
            system="linux",
        )
    )


def _storage_unwritable(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("storage", "fail", "cannot write", True),),
            system="linux",
        )
    )


def _preview_unavailable(probe: Probe) -> str:
    del probe
    return entry._fatal_code(
        preflight.PreflightReport(
            checks=(preflight.CheckResult("preview", "degraded", "GStreamer is missing.", False),),
            system="linux",
        )
    )


def _licensing_missing(probe: Probe) -> str:
    return _raised(lambda: SystemPathOpener().open_path(probe.tmp / "missing-licences"))


def _licensing_open_failed(probe: Probe) -> str:
    target = probe.tmp / "licences"
    target.mkdir()
    probe.monkeypatch.setattr(shell.sys, "platform", "linux")
    probe.monkeypatch.setattr(
        shell.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no file manager")),
    )
    return _raised(lambda: SystemPathOpener().open_path(target))


def _cancelled(probe: Probe) -> str:
    del probe
    return _raised(lambda: (_ for _ in ()).throw(JobCancelled()))


TRIGGERS: dict[str, Callable[[Probe], str]] = {
    errors.IMPORT_NO_AUDIO_STREAM: _no_audio,
    errors.IMPORT_UNSUPPORTED_SOURCE: _unsupported_source,
    errors.IMPORT_DOWNLOAD_FAILED: lambda probe: (
        youtube.classify_youtube_error(RuntimeError("something completely unexpected")).code
    ),
    errors.IMPORT_YOUTUBE_UNAVAILABLE: lambda probe: (
        youtube.classify_youtube_error(RuntimeError("Video unavailable")).code
    ),
    errors.IMPORT_YOUTUBE_AGE_RESTRICTED: lambda probe: _raised(
        lambda: youtube._validate_resolved({"age_limit": 18}, _METADATA)
    ),
    errors.IMPORT_FILE_UNAVAILABLE: _missing_source_file,
    errors.IMPORT_TOO_LONG: _too_long,
    errors.IMPORT_YOUTUBE_URL_INVALID: lambda probe: _raised(
        lambda: youtube.parse_youtube_url("not a url")
    ),
    errors.IMPORT_YOUTUBE_PLAYLIST: lambda probe: _raised(
        lambda: youtube.parse_youtube_url("https://www.youtube.com/playlist?list=PL123")
    ),
    errors.IMPORT_YOUTUBE_TOO_LONG: lambda probe: _raised(
        lambda: youtube._validate_resolved({}, _metadata_with_duration(901.0))
    ),
    errors.IMPORT_YOUTUBE_LIVE: lambda probe: _raised(
        lambda: youtube._validate_resolved({"is_live": True}, _METADATA)
    ),
    errors.IMPORT_YOUTUBE_RATE_LIMITED: lambda probe: (
        youtube.classify_youtube_error(RuntimeError("HTTP Error 429: Too Many Requests")).code
    ),
    errors.IMPORT_YOUTUBE_EXTRACTOR_OUTDATED: lambda probe: (
        youtube.classify_youtube_error(RuntimeError("Unable to extract player response")).code
    ),
    errors.IMPORT_NETWORK: lambda probe: (
        youtube.classify_youtube_error(RuntimeError("timed out")).code
    ),
    errors.DECODE_FAILED: _decode_failed,
    errors.SEPARATION_MODEL_MISSING: _model_missing,
    errors.SEPARATION_MODEL_DOWNLOAD_FAILED: _model_download_failed,
    errors.SEPARATION_WORKER_CRASHED: _worker_crashed,
    errors.SEPARATION_ENGINE_ERROR: _worker_engine_error,
    errors.SEPARATION_PROTOCOL_MISMATCH: _protocol_mismatch,
    errors.SEPARATION_OUTPUT_INVALID: _worker_output_invalid,
    errors.PITCH_ENGINE_MISSING: _pitch_engine_missing,
    errors.PITCH_RENDER_FAILED: _pitch_render_failed,
    errors.PITCH_OUTPUT_INVALID: _pitch_output_invalid,
    errors.MEDIA_PROBE_FAILED: _probe_failed,
    errors.MEDIA_ENCODE_FAILED: _encode_failed,
    errors.MEDIA_VALIDATION_FAILED: _validation_failed,
    errors.EXPORT_WRITE_FAILED: _write_failed,
    errors.STORAGE_DISK_FULL: _disk_full,
    errors.STORAGE_CACHE_FULL: _cache_full,
    errors.STORAGE_PERMISSION_DENIED: _permission_denied,
    errors.INTERNAL_UNEXPECTED: _internal_unexpected,
    errors.PREFLIGHT_PLATFORM_UNSUPPORTED: _platform_unsupported,
    errors.PREFLIGHT_WINDOW_UNAVAILABLE: _window_unavailable,
    errors.PREFLIGHT_WEBVIEW2_MISSING: _webview2_missing,
    errors.PREFLIGHT_BUNDLE_DAMAGED: _bundle_damaged,
    errors.PREFLIGHT_STORAGE_UNWRITABLE: _storage_unwritable,
    errors.PREFLIGHT_PREVIEW_UNAVAILABLE: _preview_unavailable,
    errors.LICENSING_FILES_MISSING: _licensing_missing,
    errors.LICENSING_OPEN_FAILED: _licensing_open_failed,
    errors.CANCELLED: _cancelled,
}


def _metadata_with_duration(duration: float) -> youtube.YouTubeMetadata:
    return youtube.YouTubeMetadata(
        video_id="abc",
        title="Title",
        artist="Artist",
        canonical_url="https://www.youtube.com/watch?v=abc",
        duration_s=duration,
    )


@pytest.mark.parametrize("code", sorted(errors.ALL_CODES))
def test_every_code_has_a_headline_and_one_recovery_action(code: str) -> None:
    assert code in formatting.COPY_BY_CODE, code
    copy = formatting.COPY_BY_CODE[code]
    assert copy.headline.strip(), code
    assert len(copy.headline) <= formatting.MAX_HEADLINE_LENGTH == 90, code
    assert copy.action.strip(), code
    assert "\n" not in copy.headline, code
    assert "\n" not in copy.action, code
    assert code not in copy.headline, code
    assert "Traceback" not in copy.headline + copy.action, code
    assert not re.search(
        r"\.(?:wav|mp3|flac|m4a|json|exe|dll|safetensors)\b",
        copy.headline,
        re.IGNORECASE,
    ), code


@pytest.mark.parametrize("code", sorted(errors.ALL_CODES))
def test_every_code_has_a_trigger(
    code: str, app_paths: AppPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if code in EXPLICIT_EXCEPTIONS:
        pytest.skip(EXPLICIT_EXCEPTIONS[code])
    trigger = TRIGGERS[code]
    assert trigger(Probe(app_paths, tmp_path, monkeypatch)) == code


def test_the_trigger_table_covers_the_taxonomy() -> None:
    assert set(TRIGGERS) | set(EXPLICIT_EXCEPTIONS) == errors.ALL_CODES


def test_no_duplicate_trigger_or_copy_keys() -> None:
    assert len(TRIGGERS) == len(set(TRIGGERS))
    assert len(formatting.COPY_BY_CODE) == len(errors.ALL_CODES)


def test_the_independent_taxonomy_copies_stay_in_sync() -> None:
    from tests.unit.test_error_codes import TICKET_14_CODES
    from tests.unit.test_scaffold import EXPECTED_CODES

    assert errors.ALL_CODES == TICKET_14_CODES == EXPECTED_CODES


def test_retry_flags_are_owned_by_the_raising_code() -> None:
    assert separation._protocol_error(ProtocolError("bad"), "", 0).retryable is False
    assert (
        separation._worker_error(
            WorkerEvent("error", code=errors.SEPARATION_ENGINE_ERROR), ""
        ).retryable
        is True
    )
    assert (
        separation._worker_error(
            WorkerEvent("error", code=errors.SEPARATION_OUTPUT_INVALID), ""
        ).retryable
        is True
    )
    assert pitch._output_invalid(Path("render.wav"), "bad").retryable is True
    assert media._validation_error(Path("out.wav"), "decode", "bad").retryable is True
    with pytest.raises(StemForgeError) as raised:
        artifact_module._raise_wrapped(OSError(errno.ENOSPC, "full"), "write")
    assert raised.value.retryable is True


def test_internal_unexpected_is_only_the_unknown_fallback() -> None:
    fallback = formatting.COPY_BY_CODE[errors.INTERNAL_UNEXPECTED]
    assert fallback.headline == "Something went wrong."
    assert "log" in fallback.action.lower()
    # The only supported-workflow trigger is an unknown exception class, not a
    # user action; every named user failure above resolves to its own code.
    assert formatting.copy_for(errors.DECODE_FAILED).headline != fallback.headline
