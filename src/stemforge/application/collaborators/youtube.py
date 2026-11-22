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
        return self._settings.youtube_enabled

    def youtube_tool_versions(self) -> Mapping[str, str]:
        return dict(shipped_tool_versions())

    def set_youtube_enabled(self, enabled: bool) -> None:
        self._settings.set_youtube_enabled(enabled)

    def youtube_notice_acknowledged(self) -> bool:
        return self._settings.youtube_notice_is_current()

    def accept_youtube_notice(self) -> None:
        self._settings.accept_youtube_notice()

    def validate_youtube(self, url: str) -> YouTubeVideo:
        return parse_youtube_url(url)

    def submit_youtube(self, url: str) -> str:
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
