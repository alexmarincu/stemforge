"""The workspace page: variant A rail-and-detail shell for the load workflow.

One window, a 292 px left rail (brand, Add audio, Tracks, Recent, cache footer)
and a main pane whose selected Track shows its source facts; with no selection
the main pane carries the empty state. One Job bar sits at the bottom of the
main pane. The page only reads immutable snapshots from ``WorkspaceService``;
every mutation goes through the service (ticket 06 §5, ticket 08).

The shell is also importable headlessly: ``--smoke-test`` registers ``/`` and
checks that the page responds before any service or native window exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from nicegui import app, context, events, ui
from nicegui.client import Client
from nicegui.events import NativeEventArguments

from stemforge import __version__, errors
from stemforge.application import PreviewTarget, WorkspaceService
from stemforge.domain.models import OriginKind, Track
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import AudioFileDialog, filter_drop_paths
from stemforge.platform.preflight import PreflightReport
from stemforge.ui import formatting
from stemforge.ui.components.about import SettingsDialog
from stemforge.ui.components.youtube import YouTubeRow

TITLE: Final = "StemForge"
POLL_INTERVAL_S: Final = 0.4
RECENT_VISIBLE: Final = 3

SHELL_CSS: Final = """
.nicegui-content { padding: 0 !important; gap: 0 !important; align-items: stretch !important; }
.sf-shell { display: grid; grid-template-columns: 292px minmax(0, 1fr);
  width: 100vw; height: 100vh; overflow: hidden; background: #0e1014; color: #e8edf4;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
@media (max-width: 1150px) { .sf-shell { grid-template-columns: 260px minmax(0, 1fr); } }
.sf-rail { display: flex; flex-direction: column; min-height: 0;
  background: #11141a; border-right: 1px solid #232833; }
.sf-brand { display: flex; align-items: center; gap: 10px; padding: 16px 16px 10px; }
.sf-brand-name { font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }
.sf-brand-sub { font-size: 10.5px; color: #7c8798; }
.sf-add { padding: 4px 12px 0; display: flex; flex-direction: column; gap: 6px; }
.sf-add-hint { font-size: 10.5px; color: #7c8798; text-align: center; }
.sf-tracks { flex: 1; min-height: 90px; overflow-y: auto; padding: 12px 8px 8px; }
.sf-rail-label { display: flex; justify-content: space-between; align-items: center;
  padding: 0 6px 6px; font-size: 10.5px; letter-spacing: 0.08em;
  text-transform: uppercase; color: #7c8798; font-weight: 600; }
.sf-rail-empty { padding: 4px 8px 8px; font-size: 11.5px; color: #7c8798; }
.sf-track { display: flex; align-items: center; gap: 9px; padding: 7px 7px;
  border: 1px solid transparent; border-radius: 8px; cursor: pointer; }
.sf-track:hover { background: #171d26; }
.sf-track.is-selected { background: #1b2230; border-color: #2c3646; }
.sf-track-hit { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 0;
  cursor: pointer; }
.sf-track-ic { width: 26px; height: 26px; flex: none; display: grid; place-items: center;
  border-radius: 6px; background: #232b38; color: #9aa6b7; font-size: 16px; }
.sf-track.is-selected .sf-track-ic { background: #3a2c14; color: #f0a83a; }
.sf-track-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 1px; }
.sf-track-title { font-size: 12.5px; font-weight: 550; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sf-track-meta { font-size: 10.5px; color: #7c8798; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sf-dot { width: 7px; height: 7px; border-radius: 50%; background: #5c6675; flex: none; }
.sf-track-remove { opacity: 0; }
.sf-track:hover .sf-track-remove, .sf-track.is-selected .sf-track-remove { opacity: 1; }
.sf-recent { padding: 10px 8px 6px; border-top: 1px solid #1d222c; }
.sf-recent-row { display: flex; align-items: center; gap: 5px; padding: 2px 0;
  border-radius: 6px; min-width: 0; }
.sf-recent-hit { display: flex; align-items: center; gap: 7px; padding: 3px 7px;
  border-radius: 6px; cursor: pointer; min-width: 0; flex: 1; }
.sf-recent-hit:hover { background: #171d26; }
.sf-recent-row.is-missing { opacity: 0.55; }
.sf-recent-row.is-off { opacity: 0.55; }
.sf-recent-hit.is-disabled { cursor: default; }
.sf-recent-title { flex: 1; min-width: 0; font-size: 11.5px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.sf-recent-meta { font-size: 10px; color: #7c8798; flex: none; }
.sf-foot { border-top: 1px solid #232833; padding: 10px 14px 12px;
  display: flex; flex-direction: column; gap: 7px; }
.sf-cache-line { display: flex; justify-content: space-between; font-size: 10.5px; color: #8994a4; }
.sf-bar { height: 4px; border-radius: 999px; background: #232a35; overflow: hidden; }
.sf-bar > i { display: block; height: 100%; background: #f0a83a; border-radius: inherit; }
.sf-foot-line { display: flex; justify-content: space-between; align-items: center; }
.sf-fineprint { font-size: 10px; color: #697486; }
.sf-main { display: flex; flex-direction: column; min-width: 0; height: 100vh;
  background: #0e1014; }
.sf-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 28px 28px; }
.sf-empty { min-height: 100%; display: flex; align-items: center; justify-content: center; }
.sf-empty-card { width: min(560px, 100%); display: flex; flex-direction: column; gap: 14px; }
.sf-empty-title { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.sf-lede { font-size: 13px; color: #9aa6b7; }
.sf-dropzone { display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 30px 20px; border: 1px dashed #354052; border-radius: 12px;
  background: #12161d; cursor: pointer; text-align: center; }
.sf-dropzone:hover { border-color: #55647a; background: #151a22; }
.sf-dropzone.is-disabled { opacity: 0.55; cursor: default; }
.sf-drop-title { font-size: 13px; font-weight: 600; }
.sf-drop-sub { font-size: 11.5px; color: #8b97a8; }
.sf-or { display: flex; align-items: center; gap: 10px; font-size: 10.5px;
  letter-spacing: 0.08em; text-transform: uppercase; color: #7c8798; }
.sf-or::before, .sf-or::after { content: ""; height: 1px; background: #232833; flex: 1; }
.sf-yt { display: flex; flex-direction: column; gap: 7px; }
.sf-yt-row { display: flex; align-items: stretch; gap: 8px; }
.sf-yt-input { flex: 1; min-width: 0; }
.sf-yt-button { flex: none; }
.sf-yt-error { font-size: 11.5px; color: #f1b7b7; }
.sf-yt-status { font-size: 11.5px; color: #9fd3ad; }
.sf-yt-disclaimer { font-size: 11px; color: #7c8798; line-height: 1.45; }
.sf-setting-row { display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 8px 0; }
.sf-setting-main { display: flex; flex-direction: column; gap: 2px; }
.sf-setting-label { font-size: 12.5px; font-weight: 550; }
.sf-detail { display: flex; flex-direction: column; gap: 18px; max-width: 980px; }
.sf-title { font-size: 19px; font-weight: 650; letter-spacing: -0.01em; }
.sf-detail-meta { font-size: 11.5px; color: #8994a4; display: flex; flex-wrap: wrap; gap: 6px; }
.sf-card { border: 1px solid #232a35; border-radius: 12px; background: #151920; padding: 16px; }
.sf-card-title { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em;
  color: #7c8798; font-weight: 600; margin-bottom: 10px; }
.sf-fact { display: flex; gap: 16px; font-size: 12px; padding: 3px 0; }
.sf-fact-key { width: 100px; color: #7c8798; flex: none; }
.sf-fact-value { color: #d8e0eb; min-width: 0; overflow-wrap: anywhere; }
.sf-sigpath { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 11px; }
.sf-chip { display: inline-flex; gap: 6px; align-items: center; padding: 4px 9px;
  border: 1px solid #2a3240; border-radius: 999px; color: #8b97a8; }
.sf-chip b { color: #b9c4d3; font-weight: 600; }
.sf-chip.is-done { border-color: #2f4636; color: #9fd3ad; }
.sf-notice { border: 1px solid #5b2c2c; background: #241617; border-radius: 10px;
  padding: 12px 14px; display: flex; flex-direction: column; gap: 5px; margin-bottom: 16px; }
.sf-notice-headline { font-size: 12.5px; font-weight: 600; color: #f1b7b7; }
.sf-notice-action { font-size: 11.5px; color: #d5a3a3; }
.sf-notice-detail { font-size: 10.5px; color: #a47878; font-family: ui-monospace, monospace; }
.sf-banner { display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  background: #1d1a12; border-bottom: 1px solid #40341d; color: #e4d3a9; font-size: 12px; }
.sf-jobbar { flex: none; min-height: 44px; display: flex; align-items: center; gap: 12px;
  padding: 6px 16px; background: #11141a; border-top: 1px solid #232833; }
.sf-job-text { font-size: 11.5px; color: #b9c4d3; white-space: nowrap; }
.sf-job-detail { font-size: 10.5px; color: #7c8798; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 34%; flex: 0 1 auto; }
.sf-job-progress { flex: 1; min-width: 80px; }
.sf-job-pct { font-size: 10.5px; color: #7c8798; font-family: ui-monospace, monospace;
  width: 38px; }
.sf-banner-degraded { background: #1d1a12; border-bottom: 1px solid #40341d; }
.sf-banner-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sf-banner-headline { font-size: 12px; font-weight: 600; }
.sf-banner-hint { font-size: 10.5px; color: #b9a77c; }
.sf-transport { display: flex; flex-direction: column; gap: 8px; }
.sf-transport-row { display: flex; align-items: center; gap: 10px; min-width: 0; }
.sf-now { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 1px; }
.sf-now-title { font-size: 12.5px; font-weight: 600; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.sf-now-target { font-size: 10.5px; color: #8994a4; }
.sf-time { font-size: 10.5px; color: #7c8798; font-family: ui-monospace, monospace; }
.sf-ab { display: inline-flex; border: 1px solid #2a3240; border-radius: 8px; overflow: hidden; }
.sf-ab .q-btn { border-radius: 0; font-size: 10.5px; }
.sf-ab .q-btn.is-active { background: #3a2c14; color: #f0a83a; }
.sf-output-row { display: flex; align-items: center; gap: 9px; padding: 4px 0;
  border-top: 1px solid #1d222c; }
.sf-output-row:first-of-type { border-top: 0; }
.sf-output-row.is-active { background: #1b2230; border-radius: 8px; }
.sf-output-name { flex: 1; min-width: 0; font-size: 12px; }
.sf-output-meta { font-size: 10.5px; color: #7c8798; }
.sf-unavailable { font-size: 11.5px; color: #a47878; }
.sf-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.sf-player { display: none; }
"""


@dataclass(slots=True)
class UiContext:
    """Process-wide state the page reads; rebuilt only by a new app launch."""

    service: WorkspaceService
    dialogs: AudioFileDialog
    show_recovery: bool = False
    recovery_dismissed: bool = False
    preflight: PreflightReport | None = None

    @property
    def playback_available(self) -> bool:
        """False only for the preflight's degraded preview stack (ticket 14 §3)."""
        return self.preflight is None or not self.preflight.degraded


class RecentEntry(Protocol):
    """The fields the rail needs from ``settings.RecentFile`` without importing it."""

    @property
    def source_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def origin_kind(self) -> OriginKind: ...

    @property
    def last_opened(self) -> datetime: ...


class JobKindValue(Protocol):
    """A Job kind or state that renders its stable string form."""

    @property
    def value(self) -> str: ...


class JobView(Protocol):
    """The fields the Job bar needs from ``jobs.JobSnapshot`` without importing it.

    ``ui`` must not import ``stemforge.jobs`` (import-linter); this structural
    view keeps the dependency on the application seam and stays mypy-strict.
    """

    @property
    def job_id(self) -> str: ...

    @property
    def kind(self) -> JobKindValue: ...

    @property
    def state(self) -> JobKindValue: ...

    @property
    def progress(self) -> float: ...

    @property
    def stage(self) -> str: ...

    @property
    def detail(self) -> str | None: ...

    @property
    def active(self) -> bool: ...

    @property
    def finished(self) -> bool: ...

    @property
    def error_code(self) -> str | None: ...

    @property
    def error_detail(self) -> str | None: ...

    @property
    def result_keys(self) -> Mapping[str, str]: ...


_context: UiContext | None = None
_drop_target: WorkspacePage | None = None
_drop_installed = False


def configure(context: UiContext) -> UiContext:
    """Set the context ``index`` uses; production calls this before ``ui.run``."""
    global _context
    _context = context
    return context


def build_page(context: UiContext) -> WorkspacePage:
    """Build one client's workspace page from the shared context."""
    return WorkspacePage(context)


@ui.page("/")
def index() -> None:
    """Render the root page."""
    if _context is None:
        _render_unconfigured()
        return
    build_page(_context).build()


def _render_unconfigured() -> None:
    ui.page_title(TITLE)
    with ui.column().classes("absolute-center items-center gap-2"):
        ui.label(TITLE).classes("text-3xl font-bold")
        ui.label("Local stem separation, pitch shifting, and export.").classes("text-base")


class WorkspacePage:
    """The variant A shell for one client."""

    def __init__(self, context: UiContext) -> None:
        self.context = context
        self.service = context.service
        self.dialogs = context.dialogs
        self._selected_id: str | None = None
        self._recent_expanded = False
        self._dialog_busy = False
        self._watched_jobs: set[str] = set()
        self._reported_jobs: set[str] = set()
        self._job_signature: tuple[str, str, int, str, str] | None = None
        self._job_bar_visible = False
        self._notice: tuple[str, str, str, str | None] | None = None
        self._add_button: ui.button | None = None
        self._youtube_row: YouTubeRow | None = None
        self._recent_signature: tuple[tuple[str, bool], ...] | None = None
        self._client: Client | None = None
        self._last_busy = False
        self._player: ui.audio | None = None
        self._playing_track_id: str | None = None
        self._playing_key: str | None = None
        self._is_playing = False
        self._position_s = 0.0
        self._active_key: dict[str, str] = {}
        self._unavailable_preview: set[str] = set()

    # -- composition --------------------------------------------------------

    def build(self) -> None:
        self._client = context.client
        ui.page_title(TITLE)
        ui.add_css(SHELL_CSS)
        ui.dark_mode(True)
        with ui.element("div").classes("sf-shell"):
            self._build_rail()
            with ui.element("main").classes("sf-main"):
                with ui.element("div"):
                    self._banner()
                with ui.element("div").classes("sf-scroll"):
                    with ui.element("div"):
                        self._notice_block()
                    with ui.element("div"):
                        self._detail()
                with ui.element("div"):
                    self._job_bar()
                # One element per client for the whole app (ticket 08 §8).
                with ui.element("div").classes("sf-player"):
                    self._player = ui.audio("", controls=False).props("preload=none").mark("player")
        ui.timer(POLL_INTERVAL_S, self.poll)
        self._install_drop_handler()

    def _build_rail(self) -> None:
        with ui.element("aside").classes("sf-rail"):
            with ui.element("div").classes("sf-brand"):
                ui.icon("graphic_eq").classes("text-2xl").style("color:#f0a83a")
                with ui.element("div"):
                    ui.label("StemForge").classes("sf-brand-name")
                    ui.label("local · offline").classes("sf-brand-sub")
            with ui.element("div").classes("sf-add"):
                self._add_button = (
                    ui.button("Add audio", icon="add", on_click=self._open_files)
                    .props("unelevated no-caps")
                    .classes("w-full")
                    .style("background:#f0a83a;color:#141414;font-weight:600")
                    .mark("add-audio")
                )
                ui.label("or drop files anywhere").classes("sf-add-hint")
            with ui.element("div").classes("sf-tracks").mark("tracks"):
                self._tracks()
            with ui.element("div").classes("sf-recent"):
                self._recent()
            with ui.element("div").classes("sf-foot"):
                self._footer()

    # -- refreshable regions ------------------------------------------------

    @ui.refreshable_method
    def _tracks(self) -> None:
        snapshot = self.service.snapshot()
        ui.label(f"Tracks {len(snapshot)}").classes("sf-rail-label")
        if not snapshot.tracks:
            ui.label("No tracks yet").classes("sf-rail-empty")
            return
        for track in snapshot.tracks:
            selected = " is-selected" if track.track_id == self._selected_id else ""
            row = (
                ui.element("div")
                .classes(f"sf-track{selected}")
                .mark("track", f"track-{track.track_id}")
            )
            with row:
                hit = (
                    ui.element("div")
                    .classes("sf-track-hit")
                    .mark("track-row", f"track-row-{track.track_id}")
                    .on("click", lambda _=None, item=track: self._select_track(item.track_id))
                )
                with hit:
                    ui.icon("music_note").classes("sf-track-ic")
                    with ui.element("div").classes("sf-track-main"):
                        ui.label(track.title).classes("sf-track-title")
                        ui.label(self._track_meta(track)).classes("sf-track-meta")
                    ui.element("span").classes("sf-dot")
                playing = self._playing_track_id == track.track_id and self._is_playing
                play = (
                    ui.button(
                        icon="pause" if playing else "play_arrow",
                        on_click=lambda _=None, item=track: self._play_from_row(item.track_id),
                    )
                    .props("flat dense round size=sm")
                    .mark("track-play", f"track-play-{track.track_id}")
                )
                self._apply_play_enabled(play, available=self.service.has_preview(track.track_id))
                (
                    ui.button(
                        icon="close", on_click=lambda _=None, item=track: self._confirm_remove(item)
                    )
                    .props("flat dense round size=sm")
                    .classes("sf-track-remove")
                    .mark("remove-track")
                )

    @ui.refreshable_method
    def _recent(self) -> None:
        self._recent_signature = self._recent_state()
        entries = self.service.recent_files()
        if not entries:
            return
        youtube_off = not self.service.youtube_enabled()
        ui.label("Recent").classes("sf-rail-label")
        visible = entries if self._recent_expanded else entries[:RECENT_VISIBLE]
        for entry in visible:
            available = self.service.recent_available(entry.source_id)
            missing = "" if available else " is-missing"
            blocked = youtube_off and entry.origin_kind is OriginKind.YOUTUBE
            off = " is-off" if blocked else ""
            row = (
                ui.element("div")
                .classes(f"sf-recent-row{missing}{off}")
                .mark("recent", f"recent-{entry.source_id}")
            )
            with row:
                hit = (
                    ui.element("div")
                    .classes(f"sf-recent-hit{' is-disabled' if blocked else ''}")
                    .mark("recent-row")
                )
                if not blocked:
                    hit.on(
                        "click",
                        lambda _=None, item=entry: self._open_recent(item),
                    )
                with hit:
                    ui.icon("movie" if entry.origin_kind is OriginKind.YOUTUBE else "description")
                    ui.label(entry.title).classes("sf-recent-title")
                    if blocked:
                        ui.label(formatting.YOUTUBE_OFF).classes("sf-recent-meta")
                    else:
                        ui.label(
                            formatting.format_relative_time(entry.last_opened)
                            if available
                            else "missing"
                        ).classes("sf-recent-meta")
                if not available:
                    (
                        ui.button(
                            icon="close",
                            on_click=lambda _=None, item=entry: self._remove_recent(item),
                        )
                        .props("flat dense round size=xs")
                        .mark("forget-recent")
                    )
        if len(entries) > RECENT_VISIBLE:
            ui.button(
                "Show less" if self._recent_expanded else "Show all",
                on_click=self._toggle_recent,
            ).props("flat dense no-caps size=sm").classes("sf-fineprint")

    @ui.refreshable_method
    def _footer(self) -> None:
        used = self.service.artifacts.usage_bytes()
        cap = self.service.artifacts.cap_bytes
        percent = 0 if cap <= 0 else min(100, round(used * 100 / cap))
        with ui.element("div").classes("sf-cache-line"):
            ui.label("Cache")
            ui.label(f"{formatting.format_bytes(used)} / {formatting.format_bytes(cap)}")
        bar = ui.element("div").classes("sf-bar")
        with bar:
            ui.element("i").style(f"width:{percent}%")
        with ui.element("div").classes("sf-foot-line"):
            (
                ui.button(formatting.SETTINGS_TITLE, on_click=self._open_settings)
                .props("flat dense no-caps size=sm")
                .classes("sf-fineprint")
                .mark("settings-about")
            )
            ui.label(f"v{__version__}").classes("sf-fineprint")

    @ui.refreshable_method
    def _banner(self) -> None:
        # Degraded mode is persistent and stacks first (ticket 14 §2).
        if not self.context.playback_available:
            copy = formatting.copy_for(errors.PREFLIGHT_PREVIEW_UNAVAILABLE)
            with ui.element("div").classes("sf-banner sf-banner-degraded").mark("playback-banner"):
                ui.icon("volume_off")
                with ui.element("div").classes("sf-banner-text flex-1"):
                    ui.label(copy.headline).classes("sf-banner-headline")
                    ui.label(copy.action).classes("sf-banner-hint")
        if not self.context.show_recovery or self.context.recovery_dismissed:
            return
        with ui.element("div").classes("sf-banner").mark("recovery-banner"):
            ui.icon("info")
            ui.label(
                "StemForge closed unexpectedly last time. Unfinished work was discarded; "
                "cached audio and completed renders are intact."
            ).classes("flex-1")
            ui.button("Dismiss", on_click=self._dismiss_recovery).props(
                "flat dense no-caps size=sm"
            )

    @ui.refreshable_method
    def _notice_block(self) -> None:
        if self._notice is None:
            return
        code, headline, action, detail = self._notice
        with ui.element("div").classes("sf-notice").mark("import-error"):
            ui.label(headline).classes("sf-notice-headline")
            ui.label(action).classes("sf-notice-action")
            ui.label(f"{code} · {detail}" if detail else code).classes("sf-notice-detail")

    @ui.refreshable_method
    def _detail(self) -> None:
        track = self._selected_track()
        if track is None:
            self._empty_state()
            return
        self._youtube_row = None
        meta = [self._origin_label(track)]
        filename = self._filename(track)
        if filename:
            meta.append(filename)
        meta.extend(self._audio_facts(track))
        with ui.element("div").classes("sf-detail").mark("track-detail"):
            ui.label(track.title).classes("sf-title")
            ui.label(" · ".join(meta)).classes("sf-detail-meta")
            self._signal_path(track)
            targets = self._targets(track)
            self._transport(track, targets)
            self._outputs(track, targets)
            self._actions(track)
            with ui.element("section").classes("sf-card"):
                ui.label("Source").classes("sf-card-title")
                self._fact("File", filename or track.source.origin)
                self._fact("Duration", formatting.format_duration(track.source.metadata.duration_s))
                sample_rate = formatting.format_sample_rate(track.source.metadata.sample_rate)
                if sample_rate:
                    self._fact("Sample rate", sample_rate)
                channels = formatting.format_channels(track.source.metadata.channels)
                if channels:
                    self._fact("Channels", channels)
                self._fact("Status", "No stems yet")

    def _empty_state(self) -> None:
        busy = self._busy()
        disabled = " is-disabled" if busy else ""
        youtube_on = self.service.youtube_enabled()
        lede = formatting.EMPTY_LEDE_YOUTUBE if youtube_on else formatting.EMPTY_LEDE
        with (
            ui.element("div").classes("sf-empty").mark("empty-state"),
            ui.element("div").classes("sf-empty-card"),
        ):
            ui.label("Bring in a track").classes("sf-empty-title")
            ui.label(lede).classes("sf-lede")
            dropzone = (
                ui.element("div")
                .classes(f"sf-dropzone{disabled}")
                .mark("dropzone")
                .on("click", self._open_files)
            )
            with dropzone:
                ui.icon("upload_file").classes("text-3xl").style("color:#8b97a8")
                ui.label("Drop audio files here").classes("sf-drop-title")
                ui.label("WAV, MP3, FLAC, M4A — or choose files…").classes("sf-drop-sub")
            if youtube_on:
                ui.label("or").classes("sf-or")
                self._youtube_row = YouTubeRow(
                    self.service,
                    busy=busy,
                    on_submitted=self._on_youtube_submitted,
                )
                self._youtube_row.build()
            else:
                self._youtube_row = None

    # -- preview player -----------------------------------------------------

    def _targets(self, track: Track) -> tuple[PreviewTarget, ...]:
        return self.service.preview_targets(track.track_id)

    def _active_target(
        self, track: Track, targets: Sequence[PreviewTarget] | None = None
    ) -> PreviewTarget | None:
        if targets is None:
            targets = self._targets(track)
        if not targets:
            return None
        key = self._active_key.get(track.track_id)
        for target in targets:
            if target.key == key:
                return target
        return targets[0]

    def _ab_pair(
        self, track: Track, targets: Sequence[PreviewTarget] | None = None
    ) -> tuple[PreviewTarget | None, PreviewTarget | None]:
        if targets is None:
            targets = self._targets(track)
        original = next((target for target in targets if target.key == "original"), None)
        alternate = next(
            (target for target in reversed(targets) if target.key != "original"),
            None,
        )
        return original, alternate

    def _transport(self, track: Track, targets: Sequence[PreviewTarget]) -> None:
        unavailable = track.track_id in self._unavailable_preview
        active = None if unavailable else self._active_target(track, targets)
        original, alternate = self._ab_pair(track, targets)
        playing = self._playing_track_id == track.track_id and self._is_playing
        duration = track.source.metadata.duration_s or 0.0
        slider_max = duration if duration > 0 else 0.1
        with ui.element("section").classes("sf-card").mark("transport"):
            ui.label("Transport").classes("sf-card-title")
            with ui.element("div").classes("sf-transport"):
                with ui.element("div").classes("sf-transport-row"):
                    play = (
                        ui.button(
                            icon="pause" if playing else "play_arrow",
                            on_click=lambda _=None: self._toggle_transport(track.track_id),
                        )
                        .props("unelevated round")
                        .style("background:#f0a83a;color:#141414")
                        .mark("transport-play")
                    )
                    self._apply_play_enabled(play, available=active is not None)
                    with ui.element("div").classes("sf-now"):
                        ui.label(track.title).classes("sf-now-title")
                        ui.label(
                            f"{active.label}" if active is not None else "Preview unavailable"
                        ).classes("sf-now-target").mark("now-playing")
                    ui.label(formatting.format_duration(self._position_s)).classes("sf-time")
                    ui.label(
                        f"/ {formatting.format_duration(track.source.metadata.duration_s)}"
                    ).classes("sf-time")
                seek = (
                    ui.slider(
                        min=0.0,
                        max=slider_max,
                        step=0.1,
                        value=min(max(self._position_s, 0.0), slider_max),
                        on_change=self._seek,
                    )
                    .props("dense")
                    .mark("seek")
                )
                seek.set_enabled(self._playback_allowed() and active is not None)
                with ui.element("div").classes("sf-transport-row"):
                    with ui.element("div").classes("sf-ab"):
                        ab_original = (
                            ui.button(
                                "Original",
                                on_click=lambda _=None: self._select_ab(track, "original"),
                            )
                            .props("flat dense no-caps size=sm")
                            .mark("ab-original")
                        )
                        ab_preview = (
                            ui.button(
                                alternate.label if alternate is not None else "Preview",
                                on_click=lambda _=None: self._select_ab(track, "preview"),
                            )
                            .props("flat dense no-caps size=sm")
                            .mark("ab-preview")
                        )
                    for button, target in (
                        (ab_original, original),
                        (ab_preview, alternate),
                    ):
                        active_side = (
                            target is not None and active is not None and target.key == active.key
                        )
                        if active_side:
                            button.classes("is-active")
                        button.set_enabled(target is not None and self._playback_allowed())
                    export = (
                        ui.button(
                            "Export",
                            icon="file_download",
                            on_click=lambda _=None: self._not_yet("Export"),
                        )
                        .props("flat dense no-caps size=sm")
                        .mark("export-action")
                    )
                    export.set_enabled(not self._busy())

    def _outputs(self, track: Track, targets: Sequence[PreviewTarget]) -> None:
        unavailable = track.track_id in self._unavailable_preview
        active = None if unavailable else self._active_target(track, targets)
        with ui.element("section").classes("sf-card").mark("outputs"):
            ui.label("Outputs").classes("sf-card-title")
            if not targets or unavailable:
                ui.label("Preview unavailable. Re-import the file to rebuild it.").classes(
                    "sf-unavailable"
                ).mark("preview-unavailable")
                if not targets:
                    return
            for target in targets:
                playing = (
                    self._playing_track_id == track.track_id
                    and self._playing_key == target.key
                    and self._is_playing
                )
                selected = active is not None and target.key == active.key
                row = (
                    ui.element("div")
                    .classes(f"sf-output-row{' is-active' if selected else ''}")
                    .mark("output-row", f"output-row-{target.key}")
                )
                with row:
                    play = (
                        ui.button(
                            icon="pause" if playing else "play_arrow",
                            on_click=lambda _=None, item=target: self._toggle_target(track, item),
                        )
                        .props("flat dense round size=sm")
                        .mark("output-play", f"output-play-{target.key}")
                    )
                    self._apply_play_enabled(play)
                    ui.label(target.label).classes("sf-output-name")
                    if target.key == "original":
                        ui.label("source").classes("sf-output-meta")
                    else:
                        ui.label("render").classes("sf-output-meta")

    def _actions(self, track: Track) -> None:
        del track
        with ui.element("section").classes("sf-card").mark("track-actions"):
            ui.label("Pitch and Separation").classes("sf-card-title")
            with ui.element("div").classes("sf-actions"):
                separation = (
                    ui.button(
                        "Separate vocals",
                        icon="call_split",
                        on_click=lambda _=None: self._not_yet("Separation"),
                    )
                    .props("flat no-caps")
                    .mark("separation-action")
                )
                pitch = (
                    ui.button(
                        "Render pitch",
                        icon="tune",
                        on_click=lambda _=None: self._not_yet("Pitch"),
                    )
                    .props("flat no-caps")
                    .mark("pitch-action")
                )
                separation.set_enabled(not self._busy())
                pitch.set_enabled(not self._busy())

    def _play_from_row(self, track_id: str) -> None:
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return
        target = self._active_target(track)
        if target is None:
            return
        self._toggle_target(track, target)

    def _toggle_transport(self, track_id: str) -> None:
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return
        target = self._active_target(track)
        if target is not None:
            self._toggle_target(track, target)

    def _toggle_target(self, track: Track, target: PreviewTarget) -> None:
        same_target = self._playing_track_id == track.track_id and self._playing_key == target.key
        if same_target and self._is_playing:
            self._pause()
            return
        self._start_target(track, target)

    def _start_target(self, track: Track, target: PreviewTarget) -> None:
        player = self._player
        if player is None or not self._playback_allowed():
            return
        same_track = self._playing_track_id == track.track_id
        position = self._position_s if same_track else 0.0
        if not same_track:
            self._position_s = 0.0
        player.pause()
        self._unavailable_preview.discard(track.track_id)
        self._active_key[track.track_id] = target.key
        self._playing_track_id = track.track_id
        self._playing_key = target.key
        self._is_playing = True
        player.set_source(target.url)
        if position > 0:
            player.seek(position)
        player.play()
        self._refresh_playback()

    def _select_ab(self, track: Track, side: str) -> None:
        original, alternate = self._ab_pair(track)
        target = original if side == "original" else alternate
        if target is None or not self._playback_allowed():
            return
        was_playing = self._playing_track_id == track.track_id and self._is_playing
        player = self._player
        if player is None:
            return
        player.pause()
        self._unavailable_preview.discard(track.track_id)
        self._active_key[track.track_id] = target.key
        player.set_source(target.url)
        if self._position_s > 0:
            player.seek(self._position_s)
        if was_playing:
            player.play()
        self._playing_track_id = track.track_id if was_playing else None
        self._playing_key = target.key if was_playing else None
        self._is_playing = was_playing
        self._refresh_playback()

    def _pause(self) -> None:
        if self._player is not None:
            self._player.pause()
        self._is_playing = False
        self._playing_track_id = None
        self._playing_key = None
        self._refresh_playback()

    def _reconcile_playback(self) -> None:
        """Stop when a Job removed or replaced the file that is currently playing."""
        track_id = self._playing_track_id
        if track_id is None or not self._is_playing:
            return
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            self._mark_preview_unavailable(track_id)
            return
        targets = self._targets(track)
        if not any(target.key == self._playing_key for target in targets):
            self._mark_preview_unavailable(track_id)

    def _mark_preview_unavailable(self, track_id: str) -> None:
        if self._player is not None:
            self._player.pause()
        self._is_playing = False
        self._playing_track_id = None
        self._playing_key = None
        self._active_key.pop(track_id, None)
        self._unavailable_preview.add(track_id)
        self._refresh_playback()

    def _seek(self, event: events.ValueChangeEventArguments[float | None]) -> None:
        if event.value is None:
            return
        self._position_s = float(event.value)
        if self._player is not None:
            self._player.seek(self._position_s)

    def _playback_allowed(self) -> bool:
        """Playback is independent of Jobs (ticket 08 §11); only a degraded host blocks it."""
        return self.context.playback_available

    def _apply_play_enabled(self, button: ui.button, *, available: bool = True) -> None:
        enabled = available and self._playback_allowed()
        button.set_enabled(enabled)
        if not enabled:
            reason = (
                "Playback unavailable"
                if not self.context.playback_available
                else "No cached preview"
            )
            button.tooltip(reason)

    def _refresh_playback(self) -> None:
        self._tracks.refresh()
        self._detail.refresh()

    def _not_yet(self, feature: str) -> None:
        ui.notify(f"{feature} arrives in a later build.", type="info")

    @ui.refreshable_method
    def _job_bar(self) -> None:
        snapshot = self._active_job()
        if snapshot is None:
            return
        with ui.element("div").classes("sf-jobbar").mark("job-bar"):
            ui.element("span").classes("sf-dot")
            ui.label(
                f"{self._job_label(snapshot.kind.value)} · {snapshot.stage or 'Starting'}"
            ).classes("sf-job-text").mark("job-stage")
            # Local imports keep their filename out of the bar until the Track
            # exists; YouTube's wait countdown and byte progress are user-facing.
            if snapshot.kind.value == "import_youtube" and snapshot.detail:
                ui.label(snapshot.detail).classes("sf-job-detail").mark("job-detail")
            ui.linear_progress(value=float(snapshot.progress), show_value=False).classes(
                "sf-job-progress"
            )
            ui.label(f"{round(snapshot.progress * 100)}%").classes("sf-job-pct")
            (
                ui.button("Cancel", on_click=self._cancel_job)
                .props("flat dense no-caps size=sm")
                .mark("cancel-job")
            )

    # -- load flow ----------------------------------------------------------

    async def _open_files(self) -> None:
        if self._busy() or self._dialog_busy:
            return
        self._dialog_busy = True
        self._sync_actions()
        try:
            selected = await self.dialogs.choose_audio_files()
        except StemForgeError as error:
            self._show_error(error)
            selected = ()
        except Exception as error:  # pragma: no cover - defensive; no raw trace in UI
            self._show_error(StemForgeError(errors.INTERNAL_UNEXPECTED, str(error)))
            selected = ()
        finally:
            self._dialog_busy = False
        if selected:
            self._submit_paths(selected)
        self._sync_actions()
        self._refresh_all()

    def _submit_paths(self, paths: Sequence[Path]) -> None:
        first_error: StemForgeError | None = None
        submitted = False
        for path in paths:
            try:
                job_id = self.service.import_local(path)
            except StemForgeError as error:
                first_error = first_error or error
                continue
            self._watch_job(job_id)
            submitted = True
        if first_error is not None:
            self._show_error(first_error)
        if submitted:
            self._notice = None
            self._notice_block.refresh()
            self._job_signature = None
            self._job_bar_visible = True
            self._job_bar.refresh()

    def _watch_job(self, job_id: str) -> None:
        self._watched_jobs.add(job_id)
        self._reported_jobs.discard(job_id)

    def _on_youtube_submitted(self, job_id: str) -> None:
        """Mirror the local-import submit: watch the Job and show the bar."""
        self._watch_job(job_id)
        self._notice = None
        self._notice_block.refresh()
        self._job_signature = None
        self._job_bar_visible = True
        self._job_bar.refresh()
        self._sync_actions()

    def _open_recent(self, entry: RecentEntry) -> None:
        if self._busy():
            return
        if entry.origin_kind is OriginKind.YOUTUBE and not self.service.youtube_enabled():
            ui.notify(formatting.YOUTUBE_OFF, type="info")
            return
        try:
            job_id = self.service.open_recent(entry.source_id)
        except StemForgeError as error:
            self._show_error(error)
            self._refresh_all()
            return
        if job_id is not None:
            self._watch_job(job_id)
            self._notice = None
            self._notice_block.refresh()
            self._job_signature = None
            self._job_bar_visible = True
            self._job_bar.refresh()
            self._sync_actions()

    def _cancel_job(self) -> None:
        snapshot = self._active_job()
        if snapshot is None:
            return
        self.service.runner.cancel(snapshot.job_id)
        self._job_bar.refresh()

    def poll(self) -> None:
        self._sync_recent_availability()
        snapshot = self._active_job()
        if snapshot is not None:
            self._watch_job(snapshot.job_id)
            signature = (
                snapshot.job_id,
                snapshot.state.value,
                round(snapshot.progress * 1000),
                snapshot.stage,
                snapshot.detail or "",
            )
            if signature != self._job_signature or not self._job_bar_visible:
                self._job_signature = signature
                self._job_bar_visible = True
                self._job_bar.refresh()
        elif self._job_bar_visible:
            self._job_signature = None
            self._job_bar_visible = False
            self._job_bar.refresh()

        self._process_finished_jobs()
        self._reconcile_playback()
        self._sync_actions()

    def _process_finished_jobs(self) -> None:
        for job_id in tuple(self._watched_jobs):
            try:
                snapshot = self.service.runner.snapshot(job_id)
            except KeyError:
                self._watched_jobs.discard(job_id)
                continue
            if not snapshot.finished:
                continue
            self._watched_jobs.discard(job_id)
            if job_id in self._reported_jobs:
                continue
            self._reported_jobs.add(job_id)
            if snapshot.state.value == "succeeded":
                self._on_import_success(snapshot)
            elif snapshot.state.value == "failed":
                self._on_import_failure(snapshot)
            elif snapshot.state.value == "cancelled":
                ui.notify("Import cancelled.", type="info")
                self._refresh_all()

    def _on_import_success(self, snapshot: JobView) -> None:
        track_id = snapshot.result_keys.get("track_id")
        if track_id is not None:
            self._selected_id = track_id
        self._notice = None
        self._notice_block.refresh()
        self._refresh_all()

    def _on_import_failure(self, snapshot: JobView) -> None:
        code = snapshot.error_code or errors.INTERNAL_UNEXPECTED
        copy = formatting.copy_for(code, snapshot.error_detail)
        ui.notify(copy.headline, type="negative")
        detail = f"job {snapshot.job_id[:8]}" if snapshot.job_id else None
        self._notice = (code, copy.headline, copy.action, detail)
        self._notice_block.refresh()
        self._refresh_all()

    def _show_error(self, error: StemForgeError) -> None:
        copy = formatting.copy_for(error.code, error.detail)
        ui.notify(copy.headline, type="negative")
        self._notice = (error.code, copy.headline, copy.action, error.detail)
        self._notice_block.refresh()

    # -- native drop --------------------------------------------------------

    def _install_drop_handler(self) -> None:
        global _drop_target, _drop_installed
        _drop_target = self
        if _drop_installed:
            return
        app.native.on("drop", _dispatch_drop)
        _drop_installed = True

    def handle_drop(self, paths: Sequence[object]) -> None:
        if self._client is None or self._client.is_deleted:
            return
        with self._client:
            self._handle_drop(paths)

    def _handle_drop(self, paths: Sequence[object]) -> None:
        if self._busy():
            return
        selection = filter_drop_paths(paths)
        if selection.accepted:
            self._submit_paths(selection.accepted)
        if selection.ignored:
            ui.notify("That drop didn't include a usable audio file. Use Add audio.", type="info")
        self._sync_actions()
        self._refresh_all()

    # -- remove and recent --------------------------------------------------

    def _confirm_remove(self, track: Track) -> None:
        with ui.dialog() as dialog, ui.card().classes("sf-card").mark("remove-dialog"):
            ui.label(f"Remove {track.title}?").classes("sf-title")
            ui.label(
                "This removes the track and deletes its cached audio. "
                "The file on disk is not touched."
            ).classes("sf-lede")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat no-caps")
                (
                    ui.button("Remove", on_click=lambda: self._remove_track(track, dialog))
                    .props("unelevated no-caps color=negative")
                    .mark("confirm-remove")
                )
        dialog.open()

    def _remove_track(self, track: Track, dialog: ui.dialog) -> None:
        dialog.close()
        if self._playing_track_id == track.track_id:
            self._pause()
        try:
            self.service.remove_track(track.track_id)
        except StemForgeError as error:
            self._show_error(error)
            return
        if self._selected_id == track.track_id:
            remaining = self.service.snapshot().tracks
            self._selected_id = remaining[0].track_id if remaining else None
        self._active_key.pop(track.track_id, None)
        self._unavailable_preview.discard(track.track_id)
        self._refresh_all()

    def _remove_recent(self, entry: RecentEntry) -> None:
        self.service.remove_recent(entry.source_id)
        self._recent.refresh()

    def _toggle_recent(self) -> None:
        self._recent_expanded = not self._recent_expanded
        self._recent.refresh()

    def _dismiss_recovery(self) -> None:
        self.context.recovery_dismissed = True
        self._banner.refresh()

    def _open_settings(self) -> None:
        SettingsDialog(self.service, on_youtube_toggle=self._on_youtube_toggle).open()

    def _on_youtube_toggle(self) -> None:
        self._detail.refresh()
        self._recent.refresh()

    # -- small state --------------------------------------------------------

    def _busy(self) -> bool:
        return self._dialog_busy or self._active_job() is not None

    def _active_job(self) -> JobView | None:
        for snapshot in self.service.runner.jobs():
            if snapshot.active:
                return snapshot
        return None

    def _selected_track(self) -> Track | None:
        if self._selected_id is None:
            return None
        for track in self.service.snapshot().tracks:
            if track.track_id == self._selected_id:
                return track
        return None

    def _select_track(self, track_id: str) -> None:
        if track_id != self._playing_track_id and self._is_playing:
            self._pause()
        if track_id != self._playing_track_id:
            self._position_s = 0.0
        self._selected_id = track_id
        track = self._selected_track()
        if track is not None:
            ui.page_title(f"{TITLE} — {track.title}")
        self._tracks.refresh()
        self._detail.refresh()

    def _sync_actions(self) -> None:
        busy = self._busy()
        if self._add_button is not None:
            self._add_button.set_enabled(not busy)
        if self._youtube_row is not None:
            self._youtube_row.set_busy(busy)
        if busy != self._last_busy:
            self._last_busy = busy
            self._detail.refresh()

    def _refresh_all(self) -> None:
        self._tracks.refresh()
        self._recent.refresh()
        self._detail.refresh()
        self._footer.refresh()
        self._sync_actions()

    def _recent_state(self) -> tuple[tuple[str, bool], ...]:
        return tuple(
            (entry.source_id, self.service.recent_available(entry.source_id))
            for entry in self.service.recent_files()
        )

    def _sync_recent_availability(self) -> None:
        state = self._recent_state()
        if state != self._recent_signature:
            self._recent_signature = state
            self._recent.refresh()

    def _track_meta(self, track: Track) -> str:
        parts = [
            formatting.format_duration(track.source.metadata.duration_s),
            self._origin_label(track),
            "No stems yet",
        ]
        return " · ".join(parts)

    def _origin_label(self, track: Track) -> str:
        return "YouTube" if track.source.origin_kind is OriginKind.YOUTUBE else "Local file"

    def _filename(self, track: Track) -> str:
        if track.source.origin_kind is OriginKind.YOUTUBE:
            return ""
        return Path(track.source.origin).name

    def _audio_facts(self, track: Track) -> list[str]:
        facts = []
        sample_rate = formatting.format_sample_rate(track.source.metadata.sample_rate)
        if sample_rate:
            facts.append(sample_rate)
        channels = formatting.format_channels(track.source.metadata.channels)
        if channels:
            facts.append(channels)
        return facts

    def _signal_path(self, track: Track) -> None:
        del track
        with ui.element("div").classes("sf-sigpath"):
            with ui.element("span").classes("sf-chip is-done"):
                ui.icon("check", size="xs")
                ui.label("Source")
            ui.icon("chevron_right", size="xs")
            with ui.element("span").classes("sf-chip"):
                ui.element("span").classes("sf-dot")
                ui.label("Separation")
            ui.icon("chevron_right", size="xs")
            with ui.element("span").classes("sf-chip"):
                ui.element("span").classes("sf-dot")
                ui.label("Pitch")

    def _fact(self, key: str, value: str) -> None:
        with ui.element("div").classes("sf-fact"):
            ui.label(key).classes("sf-fact-key")
            ui.label(value).classes("sf-fact-value")

    def _job_label(self, kind: str) -> str:
        labels: Mapping[str, str] = {
            "import_local": "Importing",
            "import_youtube": "Importing",
            "separate": "Separating",
            "render_pitch": "Rendering",
            "export": "Exporting",
            "model_install": "Downloading model",
        }
        return labels.get(kind, "Working")


def _dispatch_drop(event: NativeEventArguments) -> None:
    target = _drop_target
    if target is None:
        return
    files = event.args.get("files", ()) if isinstance(event.args, Mapping) else ()
    if isinstance(files, (list, tuple)):
        target.handle_drop(files)
