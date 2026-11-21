"""The YouTube-import feature the UI calls through ``WorkspaceService``."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from stemforge import errors
from stemforge.application.state import JobLedger
from stemforge.artifacts import ArtifactStore
from stemforge.domain.models import JobKind, Source, Track
from stemforge.engines.media import MediaTool
from stemforge.engines.youtube import (
    YouTubeTool,
    YouTubeVideo,
    YtDlpImporter,
    parse_youtube_url,
    shipped_tool_versions,
)
from stemforge.errors import StemForgeError
from stemforge.jobs.job import Job, JobContext, JobRun
from stemforge.pipeline.youtube_job import run_youtube_import
from stemforge.platform.paths import AppPaths
from stemforge.settings import SettingsStore


class YouTubeService:
    """Owns the YouTube toggle, notice, URL validation, and download Job."""

    def __init__(
        self,
        settings: SettingsStore,
        paths: AppPaths,
        artifacts: ArtifactStore,
        ledger: JobLedger,
        accept_source: Callable[[Source], Track],
        youtube: YouTubeTool | None,
        media: MediaTool | None,
        delay: Callable[[float], None],
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._artifacts = artifacts
        self._ledger = ledger
        self._accept_source = accept_source
        self._youtube = youtube
        self._media = media
        self._delay = delay

    def youtube_enabled(self) -> bool:
        """Whether the empty-state YouTube row is enabled (ticket 09 §7)."""
        return self._settings.youtube_enabled

    def youtube_tool_versions(self) -> Mapping[str, str]:
        """The yt-dlp, yt-dlp-ejs, and Deno versions About must show (09 §9).

        About renders from package metadata and the pinned Deno version. It
        never instantiates or calls an engine adapter, so opening the modal
        cannot start a subprocess or block the UI event loop (ADR 0003).
        """
        return dict(shipped_tool_versions())

    def set_youtube_enabled(self, enabled: bool) -> None:
        self._settings.set_youtube_enabled(enabled)

    def youtube_notice_acknowledged(self) -> bool:
        """Whether the one-time YouTube disclaimer was accepted."""
        return self._settings.youtube_notice_is_current()

    def accept_youtube_notice(self) -> None:
        """Persist the one-time YouTube ToS acknowledgment."""
        self._settings.accept_youtube_notice()

    def validate_youtube(self, url: str) -> YouTubeVideo:
        """Validate one YouTube URL for the field; no Job and no service state.

        The UI calls this before the consent gate, so a rejected paste is only a
        field error and never reaches ``submit_youtube``.
        """
        return parse_youtube_url(url)

    def submit_youtube(self, url: str) -> str:
        """Validate one YouTube URL synchronously, then submit its import Job.

        The field-level parser runs first, so a playlist, channel, search, or
        foreign host never creates a Job (ticket 09 §1). The runner dedupes the
        Job identity, so a second submit while any ``import_youtube`` Job is
        active returns that Job's id (ticket 09 §3).
        """
        video = parse_youtube_url(url)
        youtube = self._youtube_tool()
        job = Job(
            kind=JobKind.IMPORT_YOUTUBE,
            identity=(JobKind.IMPORT_YOUTUBE.value,),
            title=video.canonical_url,
            run=self._youtube_run(video, youtube),
        )
        return self._ledger.submit(job)

    def _youtube_tool(self) -> YouTubeTool:
        if self._youtube is not None:
            return self._youtube
        return YtDlpImporter(cache_root=self._paths.cache)

    def _youtube_run(self, video: YouTubeVideo, youtube: YouTubeTool) -> JobRun:
        def run(ctx: JobContext) -> Mapping[str, str]:
            media = self._media
            if media is None:
                raise StemForgeError(
                    errors.INTERNAL_UNEXPECTED,
                    "no media engine is configured for YouTube import",
                )
            result = run_youtube_import(
                ctx,
                video,
                artifacts=self._artifacts,
                media=media,
                youtube=youtube,
                finalize=self._accept_source,
                sleep=self._delay,
            )
            return {
                "source_id": result.source.source_id,
                "track_id": result.track.track_id,
            }

        return run
