"""The workspace page: variant A rail-and-detail shell for the load workflow.

One window, a 292 px left rail (brand, Add audio, Tracks, Recent, cache footer)
and a main pane whose selected Track shows its source facts; with no selection
the main pane carries the empty state. One Job bar sits at the bottom of the
main pane. The page only reads immutable snapshots from ``WorkspaceService``;
every mutation goes through the service (ticket 06 §5, ticket 08).

The Outputs card is the version authority: its tabs switch the artifact set, and
its rows are the only playable artifacts. Exactly one ``<audio>`` element serves
the whole page. Pitch and Separation contribute requests and job state only.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Final, Protocol

from nicegui import app, context, events, ui
from nicegui.client import Client
from nicegui.events import NativeEventArguments

from stemforge import __version__, errors
from stemforge.application import (
    SEPARATION_OUTPUT_ROLES,
    JobView,
    PitchSet,
    PreflightView,
    PreviewTarget,
    WorkspaceService,
)
from stemforge.domain.models import OriginKind, RenderScope, Track
from stemforge.errors import StemForgeError
from stemforge.integration.dialogs import (
    AudioFileDialog,
    NativeSaveDialog,
    SaveAudioFileDialog,
    filter_drop_paths,
)
from stemforge.integration.shell import PathOpener
from stemforge.ui import formatting, theme
from stemforge.ui.components.about import SettingsDialog
from stemforge.ui.components.diagnostics import (
    ClipboardWriter,
    DiagnosticsProvider,
    error_details,
)
from stemforge.ui.components.export import (
    ExportFailure,
    ExportNotice,
    ExportSheet,
    ExportSpec,
    ExportStatusBar,
    ExportSuccess,
)
from stemforge.ui.components.pitch import PitchCard
from stemforge.ui.components.separation import SeparationCard
from stemforge.ui.components.youtube import YouTubeRow
from stemforge.ui.failure import JobFailure
from stemforge.ui.toast import toast

TITLE: Final = "StemForge"
POLL_INTERVAL_S: Final = 0.4
RECENT_VISIBLE: Final = 3
OUTPUT_ROLES: Final = SEPARATION_OUTPUT_ROLES
PRIMARY_ROLES: Final = OUTPUT_ROLES[:2]


SECONDARY_ROLES: Final = OUTPUT_ROLES[2:]

SHELL_CSS: Final = (
    theme.TOKENS_CSS
    + """
.nicegui-content { padding: 0 !important; gap: 0 !important; align-items: stretch !important; }
.sf-primary-button { font-weight: 600; }
.q-btn.bg-primary,
.q-btn.bg-negative { color: var(--ink) !important; }
/* Neutral flat controls inherit the muted ink of the prototype, not Quasar's
   blue primary; active tabs and A/B overrides below still win. */
.q-btn--flat { color: var(--muted); }
.q-btn--flat:hover { color: var(--text); }
/* Compact controls own their box through this class, not Quasar's size prop:
   size=sm writes an inline font-size that a stylesheet cannot override. */
.q-btn.sf-control { height: var(--control-h); min-height: var(--control-h);
  min-width: var(--control-min-w); padding: 0 var(--control-pad-x);
  font-size: var(--control-font); line-height: 1; }
.q-btn.sf-control--icon { width: var(--control-icon);
  min-width: var(--control-icon); height: var(--control-icon);
  min-height: var(--control-icon); padding: 0; }
.q-btn.sf-control--primary { height: var(--control-primary-h);
  min-height: var(--control-primary-h);
  min-width: var(--control-primary-min-w);
  padding: 0 var(--control-primary-pad-x);
  font-size: var(--control-primary-font); font-weight: 600; }
.sf-shell { display: grid; grid-template-columns: 292px minmax(0, 1fr);
  width: 100vw; height: 100vh; overflow: hidden; background: var(--bg); color: var(--text);
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }
@media (max-width: 1149.98px) { .sf-shell { grid-template-columns: 260px minmax(0, 1fr); } }
.sf-rail { display: flex; flex-direction: column; min-height: 0;
  background: var(--rail); border-right: 1px solid var(--line); }
.sf-brand { display: flex; align-items: center; gap: 10px; padding: 16px 16px 10px; }
.sf-brand-mark { flex: none; }
.sf-brand-name { font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }
.sf-brand-sub { font-size: 10.5px; color: var(--dim); }
.sf-add { padding: 4px 12px 0; display: flex; flex-direction: column; gap: 6px; }
.sf-add-hint { font-size: 10.5px; color: var(--dim); text-align: center; }
.sf-tracks { flex: 1; min-height: 90px; overflow-y: auto; padding: 12px 8px 8px; }
.sf-rail-label { display: flex; justify-content: space-between; align-items: center;
  padding: 0 6px 6px; font-size: 10.5px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--dim); font-weight: 600; }
.sf-rail-empty { padding: 4px 8px 8px; font-size: 11.5px; color: var(--dim); }
.sf-track { display: flex; align-items: center; gap: 9px; padding: 7px 7px;
  border: 1px solid transparent; border-radius: 8px; cursor: pointer; }
.sf-track:hover { background: var(--panel2); }
.sf-track.is-selected { background: var(--panel2); border-color: var(--line2); }
.sf-track-hit { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 0;
  cursor: pointer; }
.sf-track-ic { width: 26px; height: 26px; flex: none; display: grid; place-items: center;
  border-radius: 6px; background: var(--panel2); color: var(--muted); font-size: 16px; }
.sf-track.is-selected .sf-track-ic { background: var(--accent-soft); color: var(--accent); }
.sf-track-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 1px; }
.sf-track-title { font-size: 12.5px; font-weight: 550; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sf-track-meta { font-size: 10.5px; color: var(--dim); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.sf-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--dim); flex: none; }
.sf-dot.is-ok { background: var(--good); }
.sf-dot.is-run { background: var(--accent); }
.sf-dot.is-bad { background: var(--bad); }
.sf-track-remove { opacity: 0; }
.sf-track:hover .sf-track-remove, .sf-track.is-selected .sf-track-remove { opacity: 1; }
.sf-recent { padding: 10px 8px 6px; border-top: 1px solid var(--line);
  flex: 0 1 auto; min-height: 0; max-height: 38vh; overflow-y: auto; }
.sf-recent-row { display: flex; align-items: center; gap: 5px; padding: 2px 0;
  border-radius: 6px; min-width: 0; }
.sf-recent-hit { display: flex; align-items: center; gap: 7px; padding: 3px 7px;
  border-radius: 6px; cursor: pointer; min-width: 0; flex: 1; }
.sf-recent-hit:hover { background: var(--panel2); }
.sf-recent-row.is-missing { opacity: 0.55; }
.sf-recent-row.is-off { opacity: 0.55; }
.sf-recent-hit.is-disabled { cursor: default; }
.sf-recent-title { flex: 1; min-width: 0; font-size: 11.5px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.sf-recent-meta { font-size: 10px; color: var(--dim); flex: none; }
.sf-foot { border-top: 1px solid var(--line); padding: 10px 14px 12px;
  flex: none; display: flex; flex-direction: column; gap: 7px; }
.sf-cache-line { display: flex; justify-content: space-between; font-size: 10.5px;
  color: var(--muted); }
.sf-bar { height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
.sf-bar > i { display: block; height: 100%; background: var(--accent); border-radius: inherit; }
.sf-foot-line { display: flex; justify-content: space-between; align-items: center; }
.sf-fineprint { font-size: 10px; color: var(--dim); }
.sf-main { display: flex; flex-direction: column; min-width: 0; height: 100vh;
  background: var(--bg); }
.sf-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 28px 28px;
  display: flex; flex-direction: column; }
.sf-notice-slot { flex: none; }
.sf-detail-slot { flex: 1 0 auto; display: flex; flex-direction: column; }
.sf-empty { flex: 1; display: flex; align-items: center; justify-content: center; }
.sf-empty-card { width: min(560px, 100%); display: flex; flex-direction: column; gap: 14px; }
.sf-empty-title { font-size: 24px; font-weight: 650; letter-spacing: -0.02em; }
.sf-lede { font-size: 13px; color: var(--muted); }
.sf-dropzone { display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 30px 20px; border: 1px dashed var(--line2); border-radius: var(--radius);
  background: var(--well); cursor: pointer; text-align: center; }
.sf-dropzone:hover { border-color: var(--accent-line); background: var(--panel2); }
.sf-dropzone.is-disabled { opacity: 0.55; cursor: default; }
.sf-drop-icon { color: var(--muted); }
.sf-drop-title { font-size: 13px; font-weight: 600; }
.sf-drop-sub { font-size: 11.5px; color: var(--muted); }
.sf-or { display: flex; align-items: center; gap: 10px; font-size: 10.5px;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--dim); }
.sf-or::before, .sf-or::after { content: ""; height: 1px; background: var(--line); flex: 1; }
.sf-yt { display: flex; flex-direction: column; gap: 7px; }
.sf-yt-row { display: flex; align-items: stretch; gap: 8px; }
.sf-yt-input { flex: 1; min-width: 0; }
.sf-yt-button { flex: none; }
.sf-yt-error { font-size: 11.5px; color: var(--bad-text); }
.sf-yt-status { font-size: 11.5px; color: var(--good); }
.sf-yt-disclaimer { font-size: 11px; color: var(--dim); line-height: 1.45; }
.sf-setting-row { display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 8px 0; }
.sf-settings-mark { flex: none; }
.sf-setting-main { display: flex; flex-direction: column; gap: 2px; }
.sf-setting-label { font-size: 12.5px; font-weight: 550; }
.sf-field-error { color: var(--bad-text); }
.sf-licence-text { max-height: 58vh; overflow: auto; margin: 8px 0 0; padding: 12px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--bg);
  color: var(--text); font-size: 11px; line-height: 1.45;
  font-family: var(--mono); white-space: pre-wrap; }
.sf-detail { display: flex; flex-direction: column; gap: 18px; max-width: 980px; }
.sf-head { display: flex; align-items: flex-start; justify-content: space-between;
  gap: 16px; }
.sf-head-actions { display: flex; align-items: center; gap: 2px; flex: none; }
.sf-two-up { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 18px; align-items: start; }
@media (max-width: 1149.98px) { .sf-two-up { grid-template-columns: minmax(0, 1fr); } }
.sf-title { font-size: 19px; font-weight: 650; letter-spacing: -0.01em; }
.sf-detail-meta { font-size: 11.5px; color: var(--muted); display: flex;
  flex-wrap: wrap; gap: 6px; }
.sf-card { border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--panel); padding: 16px; }
.sf-card-title { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--dim); font-weight: 600; margin-bottom: 10px; }
.sf-card-head { display: flex; align-items: center; justify-content: space-between;
  gap: 12px; margin-bottom: 10px; }
.sf-card-head .sf-card-title { margin-bottom: 0; }
.sf-fact { display: flex; gap: 16px; font-size: 12px; padding: 3px 0; }
.sf-fact-key { width: 100px; color: var(--dim); flex: none; }
.sf-fact-value { color: var(--text); min-width: 0; overflow-wrap: anywhere; }
.sf-sigpath { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 11px; }
.sf-chip { display: inline-flex; gap: 6px; align-items: center; padding: 4px 9px;
  border: 1px solid var(--line2); border-radius: 999px; color: var(--muted); }
.sf-chip b { color: var(--text); font-weight: 600; }
.sf-chip.is-done { border-color: var(--good-line); color: var(--good); }
.sf-notice { border: 1px solid var(--bad-line); background: var(--bad-bg); border-radius: 10px;
  padding: 12px 14px; display: flex; flex-direction: column; gap: 5px; margin-bottom: 16px; }
.sf-notice-headline { font-size: 12.5px; font-weight: 600; color: var(--bad-text); }
.sf-notice-action { font-size: 11.5px; color: var(--bad-action); }
.sf-notice-detail { font-size: 10.5px; color: var(--bad-dim); font-family: var(--mono); }
.sf-details { width: 100%; }
.sf-details .q-expansion-item__container { font-size: 11px; color: var(--muted); }
.sf-details-actions { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.sf-diagnostics { width: min(720px, 94vw); display: flex; flex-direction: column; gap: 10px; }
.sf-diagnostics-text { width: 100%; font-family: var(--mono); font-size: 11px; }
.sf-diagnostics-text textarea { min-height: 42vh; white-space: pre; }
.sf-banner { display: flex; align-items: center; gap: 10px; padding: 10px 14px;
  background: var(--warn-bg); border-bottom: 1px solid var(--warn-line);
  color: var(--warn); font-size: 12px; }
.sf-jobbar { flex: none; min-height: 44px; display: flex; align-items: center; gap: 12px;
  padding: 6px 16px; background: var(--rail); border-top: 1px solid var(--line); }
.sf-job-text { font-size: 11.5px; color: var(--text); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; min-width: 0; max-width: 42%; }
.sf-job-detail { font-size: 10.5px; color: var(--dim); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 34%; flex: 0 1 auto; }
.sf-job-progress { flex: 1; min-width: 80px; }
.sf-job-pct { font-size: 10.5px; color: var(--dim); font-family: var(--mono);
  width: 38px; }
.sf-banner-degraded { background: var(--warn-bg); border-bottom: 1px solid var(--warn-line); }
.sf-banner-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sf-banner-headline { font-size: 12px; font-weight: 600; }
.sf-banner-hint { font-size: 10.5px; color: var(--warn); }
.sf-transport { display: flex; align-items: center; gap: 14px; padding: 12px 14px;
  background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); }
.sf-play-lg { width: 42px; min-width: 42px; height: 42px; }
.sf-play-lg .q-icon { font-size: 20px; }
.sf-transport-main { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 7px; }
.sf-transport-line { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.sf-now-title { flex: 1; min-width: 0; font-size: 12.5px; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sf-transport-time { flex: none; display: flex; align-items: baseline; gap: 4px;
  font-size: 10.5px; color: var(--dim); font-family: var(--mono); }
.sf-time-part { display: inline; }
.sf-progress { width: 100%; }
.sf-progress .q-slider { color: var(--accent); }
.sf-progress .q-slider__track-container--h { padding: 0; }
.sf-progress .q-slider__track { background: var(--line) !important; border-radius: 999px; }
.sf-progress .q-slider__selection { border-radius: inherit; }
.sf-transport-right { display: flex; align-items: center; gap: 8px; flex: none; }
.sf-ab { display: inline-flex; align-items: center; gap: 2px; padding: 2px;
  background: var(--well); border: 1px solid var(--line); border-radius: 8px; }
.sf-ab-label { font-size: 9.5px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--dim); padding: 0 5px; }
.sf-ab .q-btn { border-radius: 6px; }
.sf-ab .q-btn.is-active { background: var(--accent-soft); color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent-line); }
.sf-stepper { display: inline-flex; align-items: center; gap: 8px; }
.sf-semi { min-width: 86px; text-align: center; display: flex; flex-direction: column;
  gap: 1px; padding: 4px 10px; border: 1px solid var(--line2); border-radius: 10px;
  background: var(--well); }
.sf-semi-value { font-size: 20px; font-weight: 650; line-height: 1.15;
  font-family: var(--mono); }
.sf-semi-unit { font-size: 9.5px; color: var(--dim); text-transform: uppercase;
  letter-spacing: 0.08em; }
.sf-pitch-status { font-size: 11.5px; color: var(--muted); margin: 8px 0 2px; }
.sf-versions { display: flex; flex-direction: column; gap: 5px; margin-top: 12px; }
.sf-versions-label { font-size: 10px; color: var(--dim); text-transform: uppercase;
  letter-spacing: 0.08em; font-weight: 600; }
.sf-tabs { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sf-version-tab { border: 1px solid var(--line2); border-radius: 999px;
  color: var(--muted); }
.sf-version-tab.is-active { background: var(--accent-soft);
  border-color: var(--accent-line); color: var(--accent); }
.sf-version-tab.is-stale { border-style: dashed; color: var(--warn); }
.sf-version-tab.is-stale.is-active { background: var(--accent-soft); color: var(--accent); }
.sf-output-row { display: flex; align-items: center; gap: 9px; min-height: 40px;
  padding: 4px 0; border-top: 1px solid var(--line); }
.sf-output-row:first-of-type { border-top: 0; }
.sf-output-row.is-active { background: var(--panel2); border-radius: 8px; }
.sf-output-row.is-playing { background: var(--panel2); border-radius: 8px; }
.sf-output-main { flex: 1; min-width: 0; display: flex; align-items: center; gap: 7px;
  flex-wrap: wrap; }
.sf-output-name { font-size: 12.5px; font-weight: 550; }
.sf-output-badges { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.sf-output-repair { display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  flex-basis: 100%; font-size: 10.5px; color: var(--warn); }
.sf-output-duration { font-size: 10.5px; color: var(--dim); font-family: var(--mono); }
.sf-output-row.is-unavailable .sf-output-name { color: var(--bad-dim); }
.sf-placeholder { display: flex; align-items: center; gap: 9px; min-height: 40px;
  padding: 3px 10px; margin-top: 4px; border: 1px dashed var(--line2); border-radius: 8px;
  color: var(--muted); font-size: 11.5px; }
.sf-placeholder span { flex: 1; }
.sf-unavailable { font-size: 11.5px; color: var(--bad-dim); margin-bottom: 6px; }
.sf-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.sf-player { display: none; }
.sf-separation-status { font-size: 11.5px; color: var(--muted); margin: 2px 0 6px; }
.sf-gate { display: flex; flex-direction: column; gap: 6px; padding: 12px;
  border: 1px solid var(--line2); border-radius: 10px; background: var(--well);
  margin: 4px 0 10px; }
.sf-gate-title { font-size: 13px; font-weight: 600; }
.sf-model-card { padding: 10px 12px; border: 1px solid var(--line2); border-radius: 10px;
  background: var(--well); font-size: 11.5px; color: var(--text); line-height: 1.45; }
.sf-gate-summary { font-size: 11.5px; color: var(--text); }
.sf-gate-meta { font-size: 10.5px; color: var(--muted); font-family: var(--mono); }
.sf-gate-notice { font-size: 11px; color: var(--warn); line-height: 1.45; }
.sf-stem-badge { font-size: 9px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); border: 1px solid var(--line2); border-radius: 999px; padding: 0 6px; }
.sf-level-badge { font-size: 10px; color: var(--dim); letter-spacing: 0.01em; }
.sf-stall { border: 1px solid var(--warn-line); background: var(--warn-bg); border-radius: 10px;
  padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
.sf-stall-text { font-size: 11.5px; color: var(--warn); }
.sf-export-modal { width: min(480px, 94vw); display: flex; flex-direction: column;
  gap: 12px; }
.sf-export-body { display: flex; flex-direction: column; gap: 10px; width: 100%; }
.sf-export-title { font-size: 18px; font-weight: 650; letter-spacing: -0.01em; }
.sf-format-list { display: flex; flex-direction: column; gap: 6px; }
.sf-format-row { display: flex; align-items: center; gap: 10px;
  padding: 7px 10px; border: 1px solid var(--line2); border-radius: 9px; cursor: pointer;
  background: var(--well); }
.sf-format-row:hover { border-color: var(--line-hover); }
.sf-format-row.is-active { border-color: var(--accent-line); background: var(--accent-soft); }
.sf-format-radio { width: 12px; height: 12px; flex: none; border-radius: 50%;
  border: 1px solid var(--line-hover); }
.sf-format-row.is-active .sf-format-radio {
  border-color: var(--accent); background: var(--accent);
  box-shadow: inset 0 0 0 2px var(--accent-deep); }
.sf-format-main { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
.sf-format-name { font-size: 12.5px; font-weight: 550; }
.sf-format-size { font-size: 10.5px; color: var(--muted); }
.sf-export-summary { border-top: 1px solid var(--line); padding-top: 8px;
  display: flex; flex-direction: column; gap: 2px; }
.sf-export-footer { display: flex; justify-content: flex-end; gap: 8px;
  border-top: 1px solid var(--line); padding-top: 10px; }
.sf-export-confirm { font-weight: 600; }
.sf-exportbar { flex: none; display: flex; align-items: center; gap: 10px;
  padding: 7px 16px; background: var(--rail); border-top: 1px solid var(--line); }
.sf-exportbar.is-failure { background: var(--bad-bg); border-top-color: var(--bad-line); }
.sf-exportbar-icon { color: var(--good); }
.sf-exportbar.is-failure .sf-exportbar-icon { color: var(--bad-text); }
.sf-exportbar-main { display: flex; flex-direction: column; gap: 1px;
  min-width: 0; flex: 1; }
.sf-exportbar-title { font-size: 11.5px; font-weight: 600; color: var(--text);
  overflow-wrap: anywhere; }
.sf-exportbar-detail { font-size: 10.5px; color: var(--muted); }
.sf-exportbar-code { font-size: 10.5px; color: var(--bad-dim);
  font-family: var(--mono); }
"""
)


@dataclass(slots=True)
class UiContext:
    """Process-wide state the page reads; rebuilt only by a new app launch."""

    service: WorkspaceService
    dialogs: AudioFileDialog
    save_dialog: SaveAudioFileDialog | None = None
    open_path: PathOpener | None = None
    show_recovery: bool = False
    recovery_dismissed: bool = False
    preflight: PreflightView | None = None
    diagnostics: DiagnosticsProvider | None = None
    clipboard: ClipboardWriter | None = None
    resource_warning_dismissed: bool = False

    @property
    def playback_available(self) -> bool:
        """False only for the preflight's degraded preview stack (ticket 14 §3)."""
        return self.preflight is None or not self.preflight.degraded


@dataclass(frozen=True, slots=True)
class PitchJobSpec:
    """What one submitted Render asked for, so a finished Job routes by kind."""

    track_id: str
    semitones: int
    scope: RenderScope


@dataclass(frozen=True, slots=True)
class Notice:
    """One coded inline error card; the raw detail never reaches the UI."""

    failure: JobFailure
    origin: OriginKind | None = None
    retry: Callable[[], str | None] | None = None


@dataclass(frozen=True, slots=True)
class OutputRow:
    """One playable artifact row inside the active Outputs version."""

    key: str
    name: str
    badges: tuple[str, ...]
    preview: PreviewTarget | None
    export_key: str | None


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
        self._import_retries: dict[str, Callable[[], str | None]] = {}
        self._job_signature: tuple[str, str, int, str, str] | None = None
        self._job_bar_visible = False
        self._notice: Notice | None = None
        self._add_button: ui.button | None = None
        self._youtube_row: YouTubeRow | None = None
        self._recent_signature: tuple[tuple[str, bool], ...] | None = None
        self._client: Client | None = None
        self._dialog_host: ui.element | None = None
        self._last_busy = False
        self._player: ui.audio | None = None
        self._loaded_track_id: str | None = None
        self._loaded_preview: PreviewTarget | None = None
        self._is_playing = False
        self._position_s = 0.0
        self._elapsed_label: ui.label | None = None
        self._seek_slider: ui.slider | None = None
        self._seek_max = 0.1
        self._active_key: dict[str, str] = {}
        self._show_all_stems: set[str] = set()
        self._export_jobs: dict[str, ExportSpec] = {}
        self._export_notice: ExportNotice | None = None
        self._export_status = ExportStatusBar()
        self._unavailable_previews: set[tuple[str, str]] = set()
        self._pitch = PitchCard(
            self.service,
            on_select=self._select_version,
            on_job_started=self._pitch_job_started,
            on_clear_cache=self._clear_cache,
            diagnostics=context.diagnostics,
            clipboard=context.clipboard,
            open_path=context.open_path,
        )
        self._pitch_jobs: dict[str, PitchJobSpec] = {}
        self._separation = SeparationCard(
            self.service,
            on_job_started=self._separation_job_started,
            on_cancel=self._cancel_active_job,
            on_open_about=self._open_settings,
            on_clear_cache=self._clear_cache,
            diagnostics=context.diagnostics,
            clipboard=context.clipboard,
            open_path=context.open_path,
        )
        self._separation_jobs: dict[str, str] = {}
        self._export_sheet = ExportSheet(
            self.service,
            context.save_dialog if context.save_dialog is not None else NativeSaveDialog(),
            on_job_started=self._export_job_started,
            diagnostics=context.diagnostics,
            clipboard=context.clipboard,
            open_path=context.open_path,
        )

    # -- composition --------------------------------------------------------

    def build(self) -> None:
        self._client = context.client
        ui.page_title(TITLE)
        ui.add_css(SHELL_CSS)
        theme.apply().mark("theme-colors")
        ui.dark_mode(True)
        with ui.element("div").classes("sf-shell"):
            self._build_rail()
            with ui.element("main").classes("sf-main"):
                with ui.element("div"):
                    self._banner()
                with ui.element("div").classes("sf-scroll"):
                    with ui.element("div").classes("sf-notice-slot"):
                        self._notice_block()
                    with ui.element("div").classes("sf-detail-slot"):
                        self._detail()
                with ui.element("div"):
                    self._job_bar()
                # One element per client for the whole app (ticket 08 §8).
                with ui.element("div").classes("sf-player"):
                    self._player = ui.audio("", controls=False).props("preload=none").mark("player")
                    self._player.on("error", self._on_player_error)
                    self._player.on(
                        "timeupdate",
                        self._on_time_update,
                        js_handler="(event) => emit(event.target.currentTime)",
                    )
                    self._player.on(
                        "ended",
                        self._on_ended,
                        js_handler="() => emit(true)",
                    )
                # A host outside every refreshable region so Settings & About
                # stays open when a Job finishes and refreshes the rail footer.
                self._dialog_host = ui.element("div")
        # The sheet is one persistent dialog; see ExportSheet.build for why.
        self._export_sheet.build()
        ui.timer(POLL_INTERVAL_S, self.poll)
        self._install_drop_handler()

    def _build_rail(self) -> None:
        with ui.element("aside").classes("sf-rail"):
            with ui.element("div").classes("sf-brand"):
                mark = theme.image_html(theme.LOGO_SVG, size=28, css_class="sf-brand-mark")
                if mark is not None:
                    ui.html(mark, sanitize=False).mark("brand-mark")
                with ui.element("div"):
                    ui.label("StemForge").classes("sf-brand-name")
                    ui.label("local · offline").classes("sf-brand-sub")
            with ui.element("div").classes("sf-add"):
                self._add_button = (
                    ui.button("Add audio", icon="add", on_click=self._open_files)
                    .props("unelevated no-caps")
                    .classes("w-full sf-primary-button")
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
            status_text, status_class = self._track_status(track)
            with row:
                hit = (
                    ui.element("div")
                    .classes("sf-track-hit")
                    .mark("track-row", f"track-row-{track.track_id}")
                    .on("click", lambda _=None, item=track: self._select_track(item.track_id))
                )
                with hit:
                    (
                        ui.icon(formatting.track_icon(track.source.origin_kind))
                        .classes("sf-track-ic")
                        .mark("track-icon", f"track-icon-{track.track_id}")
                    )
                    with ui.element("div").classes("sf-track-main"):
                        ui.label(track.title).classes("sf-track-title")
                        (
                            ui.label(self._track_meta(track, status_text))
                            .classes("sf-track-meta")
                            .mark("track-meta", f"track-meta-{track.track_id}")
                        )
                    ui.element("span").classes(f"sf-dot {status_class}").mark(
                        "track-dot", f"track-dot-{track.track_id}"
                    )
                playing = self._loaded_track_id == track.track_id and self._is_playing
                play = (
                    ui.button(
                        icon="pause" if playing else "play_arrow",
                        on_click=lambda _=None, item=track: self._play_from_row(item.track_id),
                        color=None,
                    )
                    .props("flat dense round size=sm")
                    .mark("track-play", f"track-play-{track.track_id}")
                )
                self._apply_play_enabled(play, available=self._track_has_playable(track))
                (
                    ui.button(
                        icon="close",
                        on_click=lambda _=None, item=track: self._confirm_remove(item),
                        color=None,
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
                            color=None,
                        )
                        .props("flat dense round size=xs")
                        .mark("forget-recent")
                    )
        if len(entries) > RECENT_VISIBLE:
            ui.button(
                "Show less" if self._recent_expanded else "Show all",
                on_click=self._toggle_recent,
                color=None,
            ).props("flat dense no-caps size=sm").classes("sf-fineprint")

    @ui.refreshable_method
    def _footer(self) -> None:
        used = self.service.cache_usage_bytes()
        cap = self.service.cache_cap_bytes()
        percent = formatting.cache_percent(used, cap)
        with ui.element("div").classes("sf-cache-line"):
            ui.label("Cache")
            ui.label(f"{formatting.format_bytes(used)} / {formatting.format_bytes(cap)}")
        bar = ui.element("div").classes("sf-bar")
        with bar:
            ui.element("i").style(f"width:{percent}%")
        with ui.element("div").classes("sf-foot-line"):
            (
                ui.button(formatting.SETTINGS_TITLE, on_click=self._open_settings, color=None)
                .props("flat dense no-caps size=sm")
                .classes("sf-fineprint")
                .mark("settings-about")
            )
            ui.label(f"v{__version__}").classes("sf-fineprint")

    @ui.refreshable_method
    def _banner(self) -> None:
        """At most two banners stack, degraded first (ticket 14 §2)."""
        banners: list[str] = []
        if not self.context.playback_available:
            banners.append("degraded")
        if self._resource_warning() is not None and not self.context.resource_warning_dismissed:
            banners.append("resource")
        if self.context.show_recovery and not self.context.recovery_dismissed:
            banners.append("recovery")
        for kind in banners[:2]:
            if kind == "degraded":
                copy = formatting.copy_for(errors.PREFLIGHT_PREVIEW_UNAVAILABLE)
                with (
                    ui.element("div")
                    .classes("sf-banner sf-banner-degraded")
                    .mark("playback-banner")
                ):
                    ui.icon("volume_off")
                    with ui.element("div").classes("sf-banner-text flex-1"):
                        ui.label(copy.headline).classes("sf-banner-headline")
                        ui.label(copy.action).classes("sf-banner-hint")
            elif kind == "resource":
                warning = self._resource_warning()
                assert warning is not None
                with ui.element("div").classes("sf-banner").mark("resource-banner"):
                    ui.icon("memory")
                    ui.label(warning).classes("flex-1")
                    (
                        ui.button(
                            formatting.RESOURCE_WARNING_DISMISS,
                            on_click=self._dismiss_resource_warning,
                            color=None,
                        )
                        .props("flat dense no-caps size=sm")
                        .mark("resource-dismiss")
                    )
            else:
                with ui.element("div").classes("sf-banner").mark("recovery-banner"):
                    ui.icon("info")
                    ui.label(formatting.RECOVERY_HEADLINE).classes("flex-1")
                    ui.button(
                        formatting.RECOVERY_DISMISS, on_click=self._dismiss_recovery, color=None
                    ).props("flat dense no-caps size=sm")

    def _resource_warning(self) -> str | None:
        report = self.context.preflight
        if report is None:
            return None
        for check in report.checks:
            if check.name == "resources" and check.status == "warn":
                return check.message
        return None

    def _dismiss_resource_warning(self) -> None:
        self.context.resource_warning_dismissed = True
        self._banner.refresh()

    @ui.refreshable_method
    def _notice_block(self) -> None:
        if self._notice is None:
            return
        notice = self._notice
        failure = notice.failure
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
            origin=notice.origin,
        )
        with ui.element("div").classes("sf-notice").mark("import-error"):
            ui.label(copy.headline).classes("sf-notice-headline")
            ui.label(copy.action).classes("sf-notice-action")
            error_details(
                code=failure.code,
                marker="import-details",
                job_id=failure.job_id,
                log_path=failure.log_path,
                diagnostics=self.context.diagnostics,
                clipboard=self.context.clipboard,
                open_path=self.context.open_path,
            )
            if notice.retry is not None:
                retry = (
                    ui.button(
                        formatting.RETRY,
                        on_click=self._retry_import,
                    )
                    .props("unelevated no-caps size=sm")
                    .mark("import-retry")
                )
                retry.set_enabled(not self._busy())
            if failure.code in {errors.STORAGE_DISK_FULL, errors.STORAGE_CACHE_FULL}:
                clear = (
                    ui.button(
                        formatting.SETTINGS_CACHE_CLEAR,
                        on_click=self._clear_cache,
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("notice-clear-cache")
                )
                clear.set_enabled(not self._busy())

    @ui.refreshable_method
    def _detail(self) -> None:
        track = self._selected_track()
        if track is None:
            self._empty_state()
            return
        self._youtube_row = None
        meta = formatting.detail_meta(track)
        with ui.element("div").classes("sf-detail").mark("track-detail"):
            with ui.element("div").classes("sf-head"):
                with ui.element("div"):
                    ui.label(track.title).classes("sf-title")
                    (ui.label(" · ".join(meta)).classes("sf-detail-meta").mark("detail-meta"))
                    level_adjusted = formatting.level_adjusted_label(track.gain_db)
                    if level_adjusted is not None:
                        ui.label(level_adjusted).classes("sf-level-badge").mark("level-adjusted")
                    if track.source.downmixed:
                        ui.label(formatting.DOWNMIX_NOTE).classes("sf-fineprint").mark(
                            "downmix-note"
                        )
                with ui.element("div").classes("sf-head-actions"):
                    (
                        ui.button(icon="settings", on_click=self._open_settings, color=None)
                        .props("flat dense round size=sm")
                        .mark("detail-settings")
                        .tooltip(formatting.SETTINGS_TITLE)
                    )
                    (
                        ui.button(
                            icon="delete",
                            on_click=lambda _=None, item=track: self._confirm_remove(item),
                            color=None,
                        )
                        .props("flat dense round size=sm")
                        .mark("detail-remove")
                        .tooltip("Remove track and cached audio")
                    )
            self._signal_path(track)
            sets = self.service.pitch_sets(track)
            rows, version_label = self._version_rows(track)
            self._transport(track, rows, sets, version_label)
            with ui.element("div").classes("sf-two-up"):
                self._pitch_card(track)
                self._separation_card(track)
            self._outputs(track, sets)

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
                ui.icon("upload_file").classes("text-3xl sf-drop-icon")
                ui.label("Drop audio files here").classes("sf-drop-title")
                ui.label("WAV, MP3, FLAC, M4A — or choose files…").classes("sf-drop-sub")
            if youtube_on:
                ui.label("or").classes("sf-or")
                self._youtube_row = YouTubeRow(
                    self.service,
                    busy=busy,
                    on_submitted=self._on_youtube_submitted,
                    diagnostics=self.context.diagnostics,
                    clipboard=self.context.clipboard,
                    open_path=self.context.open_path,
                )
                self._youtube_row.build()
            else:
                self._youtube_row = None
            # Ticket 08 §2: informational only; the download gate stays in the
            # Separation card where the action is invoked.
            if not self.service.model_status().installed:
                ui.label(formatting.EMPTY_MODEL_CARD).classes("sf-model-card").mark(
                    "empty-model-card"
                )

    # -- version and output rows -------------------------------------------

    def _active_version(self, track: Track, sets: Sequence[PitchSet]) -> str:
        key = self._valid_version_key(self._active_key.get(track.track_id, "original"), sets)
        self._active_key[track.track_id] = key
        return key

    @staticmethod
    def _valid_version_key(key: str, sets: Sequence[PitchSet]) -> str:
        if key != "original" and not any(item.render_key == key for item in sets):
            return "original"
        return key

    def _version_label(self, key: str, sets: Sequence[PitchSet]) -> str:
        if key == "original":
            return formatting.PITCH_ORIGINAL
        item = next((view for view in sets if view.render_key == key), None)
        return item.label if item is not None else formatting.PITCH_ORIGINAL

    def _version_rows(self, track: Track) -> tuple[tuple[OutputRow, ...], str]:
        sets = self.service.pitch_sets(track)
        key = self._active_version(track, sets)
        rows = self._rows_for(track, key, sets)
        return rows, self._version_label(key, sets)

    def _rows_for(
        self,
        track: Track,
        version_key: str,
        sets: Sequence[PitchSet],
    ) -> tuple[OutputRow, ...]:
        exports = {
            (target.scope, target.role, target.render_key): target.key
            for target in self.service.export_targets(track)
        }
        if version_key == "original":
            original = next(
                (
                    item
                    for item in self.service.preview_targets(track.track_id)
                    if item.key == "original"
                ),
                None,
            )
            rows = [
                OutputRow(
                    key="original",
                    name=formatting.PITCH_ORIGINAL,
                    badges=(formatting.OUTPUTS_SOURCE_BADGE,),
                    preview=original,
                    export_key=exports.get(("master", "original", None)),
                )
            ]
            separation_sets = self.service.separation_sets(track)
            if not separation_sets:
                return tuple(rows)
            set_key = separation_sets[-1].set_key
            for role in OUTPUT_ROLES:
                rows.append(
                    OutputRow(
                        key=role,
                        name=formatting.SEPARATION_ROLE_LABELS[role],
                        badges=self._badges_for(role),
                        preview=self.service.separation_preview_target(track, set_key, role),
                        export_key=exports.get(("separation", role, None)),
                    )
                )
            return tuple(rows)
        item = next((view for view in sets if view.render_key == version_key), None)
        if item is None:
            return ()
        shift = formatting.format_semitones(item.semitones)
        if item.scope is RenderScope.ORIGINAL:
            return (
                OutputRow(
                    key="master",
                    name=formatting.OUTPUTS_SHIFTED_MASTER,
                    badges=(shift,),
                    preview=self.service.pitch_render_preview_target(
                        track, item.render_key, "original"
                    ),
                    export_key=exports.get(
                        (RenderScope.ORIGINAL.value, "original", item.render_key)
                    ),
                ),
            )
        rows = []
        for role in OUTPUT_ROLES:
            rows.append(
                OutputRow(
                    key=role,
                    name=formatting.SEPARATION_ROLE_LABELS[role],
                    badges=self._badges_for(role, shift=shift),
                    preview=self.service.pitch_render_preview_target(track, item.render_key, role),
                    export_key=exports.get((RenderScope.STEMS.value, role, item.render_key)),
                )
            )
        return tuple(rows)

    @staticmethod
    def _badges_for(role: str, *, shift: str | None = None) -> tuple[str, ...]:
        badges: list[str] = []
        if role in PRIMARY_ROLES:
            badges.append(formatting.SEPARATION_PRIMARY_BADGE)
        if role == "instrumental":
            badges.append(formatting.SEPARATION_SUM_BADGE)
        if role in SECONDARY_ROLES:
            badges.append(formatting.OUTPUTS_STEM_BADGE)
        if shift is not None:
            badges.append(shift)
        return tuple(badges)

    def _pitch_card(self, track: Track) -> None:
        sets = self.service.pitch_sets(track)
        self._pitch.build(
            track,
            active_key=self._active_version(track, sets),
            busy=self._busy(),
        )

    def _separation_card(self, track: Track) -> None:
        self._separation.build(track, busy=self._busy())

    def _transport(
        self,
        track: Track,
        rows: Sequence[OutputRow],
        sets: Sequence[PitchSet],
        version_label: str,
    ) -> None:
        current = self._current_row(track, rows)
        playing = self._loaded_track_id == track.track_id and self._is_playing
        duration = track.duration_s or 0.0
        position = self._position_s if self._loaded_track_id == track.track_id else 0.0
        slider_max = duration if duration > 0 else 0.1
        self._seek_max = slider_max
        latest = sets[-1] if sets else None
        active_key = self._active_version(track, sets)
        unavailable = self._preview_unavailable(
            track.track_id, current.preview if current is not None else None
        )
        if current is None or unavailable:
            now_text = formatting.TRANSPORT_PREVIEW_UNAVAILABLE
        else:
            now_text = current.name
            if active_key != "original":
                now_text = f"{current.name} · {version_label}"
        with (
            ui.element("section")
            .classes("sf-transport")
            .props("aria-label=Transport")
            .mark("transport")
        ):
            play = (
                ui.button(
                    icon="pause" if playing else "play_arrow",
                    on_click=lambda _=None: self._toggle_transport(track.track_id),
                )
                .props("unelevated round")
                .classes("sf-play-lg")
                .mark("transport-play")
            )
            self._apply_play_enabled(
                play,
                available=current is not None and current.preview is not None and not unavailable,
            )
            with ui.element("div").classes("sf-transport-main"):
                with ui.element("div").classes("sf-transport-line"):
                    ui.label(now_text).classes("sf-now-title").mark("now-playing")
                    with ui.element("div").classes("sf-transport-time"):
                        self._elapsed_label = (
                            ui.label(formatting.format_duration(position))
                            .classes("sf-time-part")
                            .mark("elapsed")
                        )
                        ui.label(f"/ {formatting.format_duration(track.duration_s)}").classes(
                            "sf-time-part"
                        )
                self._seek_slider = (
                    ui.slider(
                        min=0.0,
                        max=slider_max,
                        step=0.1,
                        value=min(max(position, 0.0), slider_max),
                        on_change=self._seek,
                    )
                    .props("dense track-size=5px thumb-size=0px")
                    .classes("sf-progress")
                    .mark("seek")
                )
                self._seek_slider.set_enabled(self._playback_allowed() and current is not None)
            with ui.element("div").classes("sf-transport-right"):
                with ui.element("div").classes("sf-ab"):
                    ui.label(formatting.AB_LABEL).classes("sf-ab-label").mark("ab-label")
                    (
                        ui.button(
                            formatting.AB_ORIGINAL,
                            on_click=lambda _=None: self._select_ab(track, "original"),
                            color=None,
                        )
                        .props("flat dense no-caps")
                        .classes("sf-control" + (" is-active" if active_key == "original" else ""))
                        .mark("ab-original")
                        .set_enabled(self._playback_allowed())
                    )
                    (
                        ui.button(
                            latest.label if latest is not None else formatting.AB_LATEST_FALLBACK,
                            on_click=lambda _=None: self._select_ab(track, "latest"),
                            color=None,
                        )
                        .props("flat dense no-caps")
                        # B names the latest set, so a non-latest render leaves
                        # neither side lit rather than mislabelling the active set.
                        .classes(
                            "sf-control"
                            + (
                                " is-active"
                                if latest is not None and active_key == latest.render_key
                                else ""
                            )
                        )
                        .mark("ab-preview", "ab-latest")
                        .set_enabled(latest is not None and self._playback_allowed())
                    )
                export_key = current.export_key if current is not None else None
                export = (
                    ui.button(
                        formatting.TRANSPORT_EXPORT,
                        icon="file_download",
                        on_click=lambda _=None: self._open_export(track, export_key=export_key),
                        color=None,
                    )
                    .props("flat dense no-caps")
                    .classes("sf-control")
                    .mark("export-action")
                )
                export.set_enabled(not self._busy() and export_key is not None)

    def _outputs(self, track: Track, sets: Sequence[PitchSet]) -> None:
        active_key = self._active_version(track, sets)
        rows = self._rows_for(track, active_key, sets)
        with ui.element("section").classes("sf-card").mark("outputs"):
            with ui.element("div").classes("sf-card-head"):
                ui.label(formatting.OUTPUTS_TITLE).classes("sf-card-title")
                if sets:
                    self._version_tabs(track, sets, active_key)
            if not rows or all(row.preview is None for row in rows):
                ui.label(formatting.OUTPUTS_UNAVAILABLE).classes("sf-unavailable").mark(
                    "preview-unavailable"
                )
                error_details(
                    code=errors.PLAYBACK_UNAVAILABLE,
                    marker="playback-error",
                    diagnostics=self.context.diagnostics,
                    clipboard=self.context.clipboard,
                    open_path=self.context.open_path,
                )
            expanded = track.track_id in self._show_all_stems
            visible_roles = OUTPUT_ROLES if expanded else PRIMARY_ROLES
            for row in rows:
                if row.key not in ("original", "master") and row.key not in visible_roles:
                    continue
                self._output_row(track, row)
            has_secondary = any(row.key in SECONDARY_ROLES for row in rows)
            if has_secondary:
                if expanded:
                    (
                        ui.button(
                            formatting.OUTPUTS_SHOW_LESS,
                            on_click=lambda _=None: self._toggle_stems(track),
                            color=None,
                        )
                        .props("flat dense no-caps size=sm")
                        .classes("sf-fineprint")
                        .mark("outputs-show-less")
                    )
                else:
                    (
                        ui.button(
                            formatting.OUTPUTS_SHOW_MORE,
                            icon="expand_more",
                            on_click=lambda _=None: self._toggle_stems(track),
                            color=None,
                        )
                        .props("flat dense no-caps size=sm")
                        .classes("sf-fineprint")
                        .mark("outputs-show-more")
                    )
            elif active_key == "original" and not self.service.has_separation(track):
                self._outputs_placeholder(track, running=self._separation_running(track))

    def _version_tabs(self, track: Track, sets: Sequence[PitchSet], active_key: str) -> None:
        with ui.element("div").classes("sf-tabs").mark("output-tabs"):
            (
                ui.button(
                    formatting.PITCH_ORIGINAL,
                    on_click=lambda _=None: self._select_version(track.track_id, "original"),
                    color=None,
                )
                .props("flat dense no-caps")
                .classes(
                    "sf-version-tab sf-control"
                    + (" is-active" if active_key == "original" else "")
                )
                .mark("output-tab", "output-tab-original")
            )
            for item in sets:
                classes = "sf-version-tab sf-control"
                if item.render_key == active_key:
                    classes += " is-active"
                if item.based_on_earlier_separation:
                    classes += " is-stale"
                text = item.label
                if item.based_on_earlier_separation:
                    text += f" · {formatting.PITCH_EARLIER_SEPARATION}"
                (
                    ui.button(
                        text,
                        on_click=lambda _=None, key=item.render_key: self._select_version(
                            track.track_id, key
                        ),
                        color=None,
                    )
                    .props("flat dense no-caps")
                    .classes(classes)
                    .mark("output-tab", f"output-tab-{item.render_key}")
                )

    def _preview_unavailable(self, track_id: str, preview: PreviewTarget | None) -> bool:
        """Whether the browser already failed to load this artifact (ticket 14)."""
        return preview is not None and (track_id, preview.key) in self._unavailable_previews

    def _output_row(self, track: Track, row: OutputRow) -> None:
        playing = (
            self._loaded_track_id == track.track_id
            and self._loaded_preview is not None
            and row.preview is not None
            and self._loaded_preview.key == row.preview.key
            and self._is_playing
        )
        unavailable = row.preview is None or self._preview_unavailable(track.track_id, row.preview)
        classes = "sf-output-row"
        if playing:
            classes += " is-playing"
        if unavailable:
            classes += " is-unavailable"
        with ui.element("div").classes(classes).mark("output-row", f"output-row-{row.key}"):
            play = (
                ui.button(
                    icon="pause" if playing else "play_arrow",
                    on_click=lambda _=None, item=row: self._toggle_row(track, item),
                    color=None,
                )
                .props("flat dense round size=sm")
                .mark("output-play", f"output-play-{row.key}")
            )
            self._apply_play_enabled(play, available=not unavailable)
            with ui.element("div").classes("sf-output-main"):
                ui.label(row.name).classes("sf-output-name")
                if row.badges:
                    with ui.element("div").classes("sf-output-badges"):
                        for badge in row.badges:
                            ui.label(badge).classes("sf-stem-badge")
                        if row.preview is not None and (
                            level_adjusted := formatting.level_adjusted_label(row.preview.gain_db)
                        ):
                            ui.label(level_adjusted).classes("sf-level-badge").mark(
                                "output-level-adjusted", f"output-level-{row.key}"
                            )
                if unavailable:
                    self._repair_action(track, row)
            ui.label(formatting.format_duration(track.duration_s)).classes("sf-output-duration")
            export = (
                ui.button(
                    icon="file_download",
                    on_click=lambda _=None, item=row: self._open_export(
                        track, export_key=item.export_key
                    ),
                    color=None,
                )
                .props("flat dense round size=sm")
                .mark("output-export", f"output-export-{row.key}")
            )
            export.set_enabled(not self._busy() and row.export_key is not None)
            export.tooltip(formatting.OUTPUTS_EXPORT_TOOLTIP)

    def _repair_action(self, track: Track, row: OutputRow) -> None:
        """Ticket 14 §4.4: an unavailable row names the repair for its kind."""
        active_key = self._active_version(track, self.service.pitch_sets(track))
        with (
            ui.element("div")
            .classes("sf-output-repair")
            .mark("output-repair", f"output-repair-{row.key}")
        ):
            if active_key != "original":
                ui.label(formatting.OUTPUTS_REPAIR_RENDER)
                button = (
                    ui.button(
                        formatting.OUTPUTS_REPAIR_RENDER_ACTION,
                        on_click=lambda _=None: self._rerender(track, active_key),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("output-repair-render")
                )
            elif row.key == "original":
                ui.label(formatting.OUTPUTS_REPAIR_IMPORT)
                button = (
                    ui.button(
                        formatting.OUTPUTS_REPAIR_IMPORT_ACTION,
                        on_click=self._open_files,
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("output-repair-import")
                )
            else:
                ui.label(formatting.OUTPUTS_REPAIR_SEPARATE)
                button = (
                    ui.button(
                        formatting.OUTPUTS_REPAIR_SEPARATE_ACTION,
                        on_click=lambda _=None: self._separation.request_separation(track),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("output-repair-separate")
                )
            button.set_enabled(not self._busy())

    def _rerender(self, track: Track, render_key: str) -> None:
        item = next(
            (view for view in self.service.pitch_sets(track) if view.render_key == render_key),
            None,
        )
        if item is None or self._busy():
            return
        try:
            job_id = self.service.render_pitch(track, item.semitones, item.scope)
        except StemForgeError as error:
            self._pitch.record_failure(
                track.track_id,
                JobFailure.from_error(error),
                item.semitones,
                item.scope,
            )
            return
        except ValueError:
            return
        self._pitch_job_started(job_id, track.track_id, item.semitones, item.scope)

    def _outputs_placeholder(self, track: Track, *, running: bool) -> None:
        with ui.element("div").classes("sf-placeholder").mark("outputs-placeholder"):
            ui.icon("graphic_eq")
            ui.label(
                formatting.OUTPUTS_PLACEHOLDER_RUNNING
                if running
                else formatting.OUTPUTS_PLACEHOLDER
            )
            if not running:
                button = (
                    ui.button(
                        formatting.OUTPUTS_SEPARATE,
                        on_click=lambda _=None: self._separation.request_separation(track),
                        color=None,
                    )
                    .props("flat dense no-caps size=sm")
                    .mark("outputs-separate")
                )
                button.set_enabled(not self._busy())

    def _current_row(self, track: Track, rows: Sequence[OutputRow]) -> OutputRow | None:
        if self._loaded_track_id == track.track_id and self._loaded_preview is not None:
            match = next(
                (
                    row
                    for row in rows
                    if row.preview is not None and row.preview.key == self._loaded_preview.key
                ),
                None,
            )
            if match is not None:
                return match
        return next((row for row in rows if row.preview is not None), None)

    def _select_ab(self, track: Track, side: str) -> None:
        sets = self.service.pitch_sets(track)
        if side == "original" or not sets:
            self._select_version(track.track_id, "original")
            return
        self._select_version(track.track_id, sets[-1].render_key)

    def _select_version(self, track_id: str, key: str) -> None:
        """Switch one Track's active version; position never carries over."""
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return
        sets = self.service.pitch_sets(track)
        key = self._valid_version_key(key, sets)
        self._active_key[track_id] = key
        manages_player = self._loaded_track_id == track_id or (
            self._loaded_track_id is None and self._selected_id == track_id
        )
        if manages_player:
            rows = self._rows_for(track, key, sets)
            still_present = self._loaded_preview is not None and any(
                row.preview is not None and row.preview.key == self._loaded_preview.key
                for row in rows
            )
            if not still_present:
                if self._player is not None:
                    self._player.pause()
                self._is_playing = False
                self._position_s = 0.0
                first = next((row for row in rows if row.preview is not None), None)
                self._loaded_preview = first.preview if first is not None else None
                if (
                    first is not None
                    and first.preview is not None
                    and self._player is not None
                    and self._playback_allowed()
                ):
                    self._player.set_source(first.preview.url)
                    self._player.seek(0.0)
        self._refresh_playback()

    def _toggle_stems(self, track: Track) -> None:
        if track.track_id in self._show_all_stems:
            self._show_all_stems.discard(track.track_id)
        else:
            self._show_all_stems.add(track.track_id)
        self._detail.refresh()

    # -- preview player -----------------------------------------------------

    def _play_from_row(self, track_id: str) -> None:
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return
        rows, _ = self._version_rows(track)
        row = next((item for item in rows if item.preview is not None), None)
        if row is not None:
            self._toggle_row(track, row)

    def _toggle_transport(self, track_id: str) -> None:
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return
        rows, _ = self._version_rows(track)
        row = self._current_row(track, rows)
        if row is not None:
            self._toggle_row(track, row)

    def _toggle_row(self, track: Track, row: OutputRow) -> None:
        preview = row.preview
        if preview is None:
            return
        same = (
            self._loaded_track_id == track.track_id
            and self._loaded_preview is not None
            and self._loaded_preview.key == preview.key
        )
        if same and self._is_playing:
            self._pause()
            return
        self._start_preview(track, row, resume=same)

    def _start_preview(self, track: Track, row: OutputRow, *, resume: bool) -> None:
        player = self._player
        preview = row.preview
        if player is None or preview is None or not self._playback_allowed():
            return
        self._unavailable_previews.discard((track.track_id, preview.key))
        if resume:
            player.play()
        else:
            player.pause()
            self._position_s = 0.0
            player.set_source(preview.url)
            # The element outlives the Track: reset its clock explicitly so a
            # reused source cannot resume at the previous Track's position.
            player.seek(0.0)
            player.play()
        self._loaded_track_id = track.track_id
        self._loaded_preview = preview
        self._is_playing = True
        self._refresh_playback()

    def _pause(self) -> None:
        if self._player is not None:
            self._player.pause()
        self._is_playing = False
        self._refresh_playback()

    def _on_time_update(self, event: events.GenericEventArguments) -> None:
        value = event.args
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        self._position_s = max(0.0, float(value))
        if self._loaded_track_id != self._selected_id:
            return
        if self._elapsed_label is not None:
            self._elapsed_label.set_text(formatting.format_duration(self._position_s))
        if self._seek_slider is not None:
            self._seek_slider.set_value(min(self._position_s, self._seek_max))

    def _on_ended(self) -> None:
        self._is_playing = False
        self._position_s = 0.0
        self._refresh_playback()

    def _reconcile_playback(self) -> None:
        """Stop when a Job removed or replaced the file that is currently playing."""
        if self._loaded_track_id is None:
            return
        track = next(
            (
                item
                for item in self.service.snapshot().tracks
                if item.track_id == self._loaded_track_id
            ),
            None,
        )
        if track is None:
            self._mark_preview_unavailable(self._loaded_track_id)
            return
        if self._loaded_preview is None:
            return
        rows, _ = self._version_rows(track)
        if not any(
            row.preview is not None and row.preview.key == self._loaded_preview.key for row in rows
        ):
            self._mark_preview_unavailable(track.track_id)

    def _on_player_error(self) -> None:
        """The browser's ``<audio>`` error handler maps to playback.unavailable."""
        track_id = self._loaded_track_id
        if track_id is not None:
            self._mark_preview_unavailable(track_id)

    def _mark_preview_unavailable(self, track_id: str) -> None:
        if self._player is not None:
            self._player.pause()
        if self._loaded_preview is not None:
            self._unavailable_previews.add((track_id, self._loaded_preview.key))
        self._is_playing = False
        self._position_s = 0.0
        self._loaded_track_id = None
        self._loaded_preview = None
        fallback = self._fallback_version(track_id)
        if fallback is None:
            if self._player is not None:
                self._player.set_source("")
        else:
            key, preview = fallback
            self._active_key[track_id] = key
            if self._player is not None and self._playback_allowed():
                self._player.set_source(preview.url)
                self._player.seek(0.0)
            self._loaded_track_id = track_id
            self._loaded_preview = preview
        toast(formatting.copy_for(errors.PLAYBACK_UNAVAILABLE).headline, type="negative")
        self._refresh_playback()

    def _fallback_version(self, track_id: str) -> tuple[str, PreviewTarget] | None:
        """Original first, then the other sets, per ticket 14 §4.8."""
        track = next(
            (item for item in self.service.snapshot().tracks if item.track_id == track_id),
            None,
        )
        if track is None:
            return None
        sets = self.service.pitch_sets(track)
        active = self._active_version(track, sets)
        if active == "original":
            keys = ["original", *(item.render_key for item in sets)]
        else:
            keys = [
                "original",
                *(item.render_key for item in sets if item.render_key != active),
                active,
            ]
        for key in keys:
            for row in self._rows_for(track, key, sets):
                if row.preview is None:
                    continue
                if self._preview_unavailable(track_id, row.preview):
                    continue
                return key, row.preview
        return None

    def _seek(self, event: events.ValueChangeEventArguments[float | None]) -> None:
        if event.value is None:
            return
        self._position_s = float(event.value)
        if self._player is not None and self._loaded_track_id == self._selected_id:
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
                else formatting.OUTPUTS_NO_PREVIEW
            )
            button.tooltip(reason)

    def _refresh_playback(self) -> None:
        self._tracks.refresh()
        self._detail.refresh()

    @ui.refreshable_method
    def _job_bar(self) -> None:
        snapshot = self._active_job()
        if snapshot is None:
            if self._export_notice is not None:
                self._export_status.build(
                    self._export_notice,
                    busy=self._busy(),
                    on_retry=self._retry_export,
                    on_dismiss=self._dismiss_export,
                    on_choose_folder=self._choose_export_folder,
                    diagnostics=self.context.diagnostics,
                    clipboard=self.context.clipboard,
                    open_path=self.context.open_path,
                )
            return
        with ui.element("div").classes("sf-jobbar").mark("job-bar"):
            ui.element("span").classes("sf-dot is-run")
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
            ui.label(f"{round(snapshot.progress * 100)}%").classes("sf-job-pct").mark("job-percent")
            (
                ui.button("Cancel", on_click=self._cancel_job, color=None)
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
        first_retry: Callable[[], str | None] | None = None
        submitted = False
        for path in paths:
            submit = partial(self.service.import_local, path)
            try:
                job_id = submit()
            except StemForgeError as error:
                if first_error is None:
                    first_error = error
                    first_retry = submit
                continue
            self._start_import_job(job_id, submit)
            submitted = True
        if first_error is not None:
            self._show_error(first_error, retry=first_retry)
        if submitted:
            self._notice = None
            self._notice_block.refresh()
            self._job_signature = None
            self._job_bar_visible = True
            self._job_bar.refresh()

    def _start_import_job(self, job_id: str, submit: Callable[[], str | None]) -> None:
        """Remember the request so a retryable failure can re-submit it exactly."""
        self._import_retries[job_id] = submit
        self._watch_job(job_id)

    def _watch_job(self, job_id: str) -> None:
        self._watched_jobs.add(job_id)
        self._reported_jobs.discard(job_id)
        self._export_notice = None

    def _on_youtube_submitted(self, job_id: str, url: str) -> None:
        """Mirror the local-import submit: watch the Job and show the bar."""
        self._start_import_job(job_id, lambda: self.service.submit_youtube(url))
        self._notice = None
        self._notice_block.refresh()
        self._job_signature = None
        self._job_bar_visible = True
        self._job_bar.refresh()
        self._sync_actions()

    def _retry_import(self) -> None:
        """Ticket 14 §8: re-submit the identical import request, no dialog."""
        notice = self._notice
        if notice is None or notice.retry is None or self._busy():
            return
        submit = notice.retry
        self._notice = None
        self._notice_block.refresh()
        try:
            job_id = submit()
        except StemForgeError as error:
            self._show_error(error, retry=submit)
            self._refresh_all()
            return
        if job_id is not None:
            self._start_import_job(job_id, submit)
            self._job_signature = None
            self._job_bar_visible = True
            self._job_bar.refresh()
        self._refresh_all()

    def _clear_cache(self) -> None:
        """Ticket 14 §4.7: offer the cache action beside a disk-full failure."""
        if self._busy():
            return
        try:
            report = self.service.clear_cache()
        except StemForgeError as error:
            self._show_error(error)
            return
        toast(formatting.cache_cleared_message(report.freed_bytes), type="positive")
        self._refresh_all()

    def _pitch_job_started(
        self,
        job_id: str,
        track_id: str,
        semitones: int,
        scope: RenderScope,
    ) -> None:
        """Record which Track and request a Render Job belongs to."""
        self._pitch_jobs[job_id] = PitchJobSpec(
            track_id=track_id,
            semitones=semitones,
            scope=scope,
        )
        self._pitch.clear_failure(track_id)
        self._watch_job(job_id)
        self._job_signature = None
        self._job_bar_visible = True
        self._job_bar.refresh()
        self._refresh_all()

    def _separation_job_started(
        self,
        job_id: str,
        kind: str,
        track_id: str | None,
    ) -> None:
        """Watch a Separation or model-install Job started by the card."""
        if kind == "separate" and track_id is not None:
            self._separation_jobs[job_id] = track_id
            self._separation.clear_failure(track_id)
        self._watch_job(job_id)
        self._job_signature = None
        self._job_bar_visible = True
        self._job_bar.refresh()
        self._refresh_all()

    # -- export -------------------------------------------------------------

    def _open_export(self, track: Track, *, export_key: str | None) -> None:
        if self._busy() or export_key is None:
            return
        self._export_sheet.open(track, target_key=export_key, busy=False)

    def _export_job_started(self, job_id: str, spec: ExportSpec) -> None:
        self._export_jobs[job_id] = spec
        self._export_notice = None
        self._watch_job(job_id)
        self._job_signature = None
        self._job_bar_visible = True
        self._job_bar.refresh()
        self._refresh_all()

    def _finish_export_job(self, snapshot: JobView) -> None:
        spec = self._export_jobs.pop(snapshot.job_id, None)
        if spec is None:
            return
        if snapshot.state.value == "succeeded":
            result = self.service.export_result(snapshot.job_id)
            if result is None:
                self._export_notice = ExportFailure(
                    spec,
                    failure=JobFailure(errors.INTERNAL_UNEXPECTED),
                )
            else:
                self._export_notice = ExportSuccess(spec, result.path)
                with contextlib.suppress(OSError):
                    self.service.remember_export_dir(result.path.parent)
                toast(f"Exported to {result.path}", type="positive")
        elif snapshot.state.value == "failed":
            failure = JobFailure.from_job(
                snapshot,
                fallback_code=errors.INTERNAL_UNEXPECTED,
            )
            self._export_notice = ExportFailure(spec, failure=failure)
            reason = (
                "export"
                if failure.code == errors.STORAGE_DISK_FULL
                else formatting.reason_from_detail(failure.detail)
            )
            toast(
                formatting.copy_for(
                    failure.code,
                    reason,
                    context=failure.context,
                ).headline,
                type="negative",
            )
        elif snapshot.state.value == "cancelled":
            toast(formatting.EXPORT_CANCELLED, type="info")
        self._job_bar.refresh()
        self._refresh_all()

    def _retry_export(self) -> None:
        notice = self._export_notice
        if notice is None or self._busy():
            return
        spec = notice.spec
        try:
            job_id = self.service.submit_export(
                spec.track_id,
                spec.target_key,
                spec.fmt,
                spec.destination_dir,
                destination_name=spec.destination_name,
            )
        except StemForgeError as error:
            self._export_notice = ExportFailure(
                spec,
                failure=JobFailure.from_error(error),
            )
            self._job_bar.refresh()
            return
        self._export_job_started(job_id, spec)

    def _dismiss_export(self) -> None:
        self._export_notice = None
        self._job_bar.refresh()

    def _choose_export_folder(self) -> None:
        """Reopen the sheet at the failed destination for another location."""
        notice = self._export_notice
        if notice is None or self._busy():
            return
        track = next(
            (
                item
                for item in self.service.snapshot().tracks
                if item.track_id == notice.spec.track_id
            ),
            None,
        )
        if track is None:
            return
        self._export_sheet.open(
            track,
            target_key=notice.spec.target_key,
            busy=False,
            directory=notice.spec.destination_dir,
        )

    def _open_recent(self, entry: RecentEntry) -> None:
        if self._busy():
            return
        if entry.origin_kind is OriginKind.YOUTUBE and not self.service.youtube_enabled():
            toast(formatting.YOUTUBE_OFF, type="info")
            return
        submit = partial(self.service.open_recent, entry.source_id)
        try:
            job_id = submit()
        except StemForgeError as error:
            self._show_error(error, retry=submit)
            self._refresh_all()
            return
        if job_id is not None:
            self._start_import_job(job_id, submit)
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
        self.service.cancel_job(snapshot.job_id)
        if snapshot.kind.value == "separate":
            toast(formatting.SEPARATION_CANCELLED, type="info")
        elif snapshot.kind.value == "model_install":
            toast(formatting.SEPARATION_MODEL_CANCELLED, type="info")
        self._job_bar.refresh()
        self._separation.observe(None)

    def _cancel_active_job(self) -> None:
        """The card's Cancel buttons use the same one-Job cancellation path."""
        self._cancel_job()

    def poll(self) -> None:
        self._sync_recent_availability()
        snapshot = self._active_job()
        self._separation.observe(snapshot)
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
                snapshot = self.service.job_snapshot(job_id)
            except KeyError:
                self._watched_jobs.discard(job_id)
                self._pitch_jobs.pop(job_id, None)
                self._separation_jobs.pop(job_id, None)
                self._import_retries.pop(job_id, None)
                continue
            if not snapshot.finished:
                continue
            self._watched_jobs.discard(job_id)
            if job_id in self._reported_jobs:
                continue
            self._reported_jobs.add(job_id)
            if snapshot.kind.value == "render_pitch":
                self._finish_pitch_job(snapshot)
                continue
            if snapshot.kind.value == "separate":
                self._finish_separation_job(snapshot)
                continue
            if snapshot.kind.value == "model_install":
                self._finish_model_job(snapshot)
                continue
            if snapshot.kind.value == "export":
                self._finish_export_job(snapshot)
                continue
            retry = self._import_retries.pop(job_id, None)
            if snapshot.state.value == "succeeded":
                self._on_import_success(snapshot)
            elif snapshot.state.value == "failed":
                self._on_import_failure(snapshot, retry=retry)
            elif snapshot.state.value == "cancelled":
                toast("Import cancelled.", type="info")
                self._refresh_all()

    def _finish_separation_job(self, snapshot: JobView) -> None:
        """Route a Separation outcome to the card; artifacts are the only truth."""
        track_id = self._separation_jobs.pop(snapshot.job_id, None)
        self._separation.observe(None)
        if track_id is None:
            return
        if snapshot.state.value == "failed":
            self._separation.record_failure(
                track_id,
                JobFailure.from_job(
                    snapshot,
                    fallback_code=errors.INTERNAL_UNEXPECTED,
                ),
            )
        elif snapshot.state.value == "cancelled":
            self._separation.clear_failure(track_id)
        else:
            self._separation.record_success(track_id)
        self._refresh_all()

    def _finish_model_job(self, snapshot: JobView) -> None:
        """On a successful install, resubmit a pending Separation exactly once."""
        self._separation.observe(None)
        if snapshot.state.value == "failed":
            self._separation.record_model_failure(
                JobFailure.from_job(
                    snapshot,
                    fallback_code=errors.SEPARATION_MODEL_DOWNLOAD_FAILED,
                )
            )
            self._refresh_all()
            return
        if snapshot.state.value == "cancelled":
            self._separation.model_installed()
            self._refresh_all()
            return
        self._separation.model_installed()
        pending = self.service.pending_separation_track_id()
        if pending is not None:
            track = next(
                (item for item in self.service.snapshot().tracks if item.track_id == pending),
                None,
            )
            if track is not None:
                try:
                    job_id = self.service.resubmit_pending_separation(track)
                except StemForgeError as error:
                    self._separation.record_failure(
                        track.track_id,
                        JobFailure.from_error(error),
                    )
                else:
                    if job_id is not None:
                        self._separation_job_started(job_id, "separate", track.track_id)
        self._refresh_all()

    def _finish_pitch_job(self, snapshot: JobView) -> None:
        """Route a Render outcome to the Pitch card; the view only moves on success."""
        spec = self._pitch_jobs.pop(snapshot.job_id, None)
        if spec is None:
            return
        if snapshot.state.value == "failed":
            self._pitch.record_failure(
                spec.track_id,
                JobFailure.from_job(
                    snapshot,
                    fallback_code=errors.INTERNAL_UNEXPECTED,
                ),
                spec.semitones,
                spec.scope,
            )
            self._refresh_all()
            return
        if snapshot.state.value == "cancelled":
            self._refresh_all()
            return
        self._pitch.clear_failure(spec.track_id)
        render_key = snapshot.result_keys.get("render_key")
        if render_key:
            self._select_version(spec.track_id, render_key)
        self._refresh_all()

    def _on_import_success(self, snapshot: JobView) -> None:
        track_id = snapshot.result_keys.get("track_id")
        if track_id is not None:
            self._selected_id = track_id
            self._active_key[track_id] = "original"
            self._unavailable_previews = {
                pair for pair in self._unavailable_previews if pair[0] != track_id
            }
        self._notice = None
        self._notice_block.refresh()
        self._refresh_all()

    def _on_import_failure(
        self,
        snapshot: JobView,
        *,
        retry: Callable[[], str | None] | None = None,
    ) -> None:
        origin = OriginKind.YOUTUBE if snapshot.kind.value == "import_youtube" else OriginKind.FILE
        failure = JobFailure.from_job(
            snapshot,
            fallback_code=errors.INTERNAL_UNEXPECTED,
        )
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
            origin=origin,
        )
        toast(copy.headline, type="negative")
        self._notice = Notice(
            failure=failure,
            origin=origin,
            retry=retry if failure.retryable else None,
        )
        self._notice_block.refresh()
        self._refresh_all()

    def _show_error(
        self,
        error: StemForgeError,
        *,
        retry: Callable[[], str | None] | None = None,
    ) -> None:
        failure = JobFailure.from_error(error)
        copy = formatting.copy_for(
            failure.code,
            formatting.reason_from_detail(failure.detail),
            context=failure.context,
        )
        toast(copy.headline, type="negative")
        self._notice = Notice(
            failure=failure,
            retry=retry if failure.retryable else None,
        )
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
            toast("That drop didn't include a usable audio file. Use Add audio.", type="info")
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
                ui.button("Cancel", on_click=dialog.close, color=None).props("flat no-caps")
                (
                    ui.button("Remove", on_click=lambda: self._remove_track(track, dialog))
                    .props("unelevated no-caps color=negative")
                    .mark("confirm-remove")
                )
        dialog.open()

    def _remove_track(self, track: Track, dialog: ui.dialog) -> None:
        dialog.close()
        if self._loaded_track_id == track.track_id:
            self._pause()
            self._loaded_track_id = None
            self._loaded_preview = None
        if self._track_owns_active_job(track.track_id):
            toast(formatting.REMOVING, type="info")
        try:
            self.service.remove_track(track.track_id)
        except StemForgeError as error:
            self._show_error(error)
            return
        if self._selected_id == track.track_id:
            remaining = self.service.snapshot().tracks
            self._selected_id = remaining[0].track_id if remaining else None
        self._active_key.pop(track.track_id, None)
        self._show_all_stems.discard(track.track_id)
        self._export_jobs = {
            job_id: spec
            for job_id, spec in self._export_jobs.items()
            if spec.track_id != track.track_id
        }
        if self._export_notice is not None and self._export_notice.spec.track_id == track.track_id:
            self._export_notice = None
        self._unavailable_previews = {
            (track_id, key)
            for track_id, key in self._unavailable_previews
            if track_id != track.track_id
        }
        self._separation.forget_track(track.track_id)
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
        if self._dialog_host is not None:
            self._dialog_host.clear()
        with self._dialog_host if self._dialog_host is not None else contextlib.nullcontext():
            SettingsDialog(
                self.service,
                on_youtube_toggle=self._on_youtube_toggle,
                open_path=self.context.open_path,
                diagnostics=self.context.diagnostics,
                clipboard=self.context.clipboard,
            ).open()

    def _on_youtube_toggle(self) -> None:
        self._detail.refresh()
        self._recent.refresh()

    # -- small state --------------------------------------------------------

    def _busy(self) -> bool:
        return self._dialog_busy or self._active_job() is not None

    def _active_job(self) -> JobView | None:
        """Prefer the running Job over one queued behind it (ticket 08 §11)."""
        queued: JobView | None = None
        for snapshot in self.service.jobs():
            if snapshot.state.value == "running":
                return snapshot
            if snapshot.active and (queued is None or snapshot.created_at < queued.created_at):
                queued = snapshot
        return queued

    def _active_separation_job(self, track_id: str) -> JobView | None:
        for snapshot in self.service.jobs():
            if snapshot.active and self._separation_jobs.get(snapshot.job_id) == track_id:
                return snapshot
        return None

    def _track_owns_active_job(self, track_id: str) -> bool:
        """Whether removing this Track cancels a Job it owns (ticket 14 §6)."""
        if self._active_separation_job(track_id) is not None:
            return True
        for snapshot in self.service.jobs():
            if not snapshot.active:
                continue
            if snapshot.kind.value == "render_pitch":
                spec = self._pitch_jobs.get(snapshot.job_id)
                if spec is not None and spec.track_id == track_id:
                    return True
            elif snapshot.kind.value == "export":
                export_spec = self._export_jobs.get(snapshot.job_id)
                if export_spec is not None and export_spec.track_id == track_id:
                    return True
        return False

    def _separation_running(self, track: Track) -> bool:
        job = self._active_separation_job(track.track_id)
        return job is not None and job.kind.value == "separate"

    def _selected_track(self) -> Track | None:
        if self._selected_id is None:
            return None
        for track in self.service.snapshot().tracks:
            if track.track_id == self._selected_id:
                return track
        return None

    def _select_track(self, track_id: str) -> None:
        if track_id != self._loaded_track_id and self._is_playing:
            self._pause()
        if track_id != self._loaded_track_id:
            self._position_s = 0.0
            # The player outlives the selection; clear its clock so returning
            # to the old Track cannot resume where it stopped while the UI
            # shows 0:00 (ticket 08 §8).
            if self._player is not None:
                self._player.seek(0.0)
        self._selected_id = track_id
        self._sync_title()
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
            self._notice_block.refresh()
            self._detail.refresh()

    def _refresh_all(self) -> None:
        self._tracks.refresh()
        self._recent.refresh()
        self._detail.refresh()
        self._footer.refresh()
        self._sync_title()
        self._sync_actions()

    def _sync_title(self) -> None:
        track = self._selected_track()
        ui.page_title(f"{TITLE} — {track.title}" if track is not None else TITLE)

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

    def _track_status(self, track: Track) -> tuple[str, str]:
        if self._active_separation_job(track.track_id) is not None:
            return formatting.TRACK_STATUS_SEPARATING, "is-run"
        failure = self._separation.failure_code(track.track_id)
        if failure is not None and failure != errors.SEPARATION_MODEL_MISSING:
            return formatting.TRACK_STATUS_FAILED, "is-bad"
        latest = self._latest_render(track)
        if self.service.has_separation(track):
            if latest is not None:
                return (
                    formatting.TRACK_STATUS_STEMS_RENDERED.format(
                        stems=formatting.TRACK_STATUS_STEMS,
                        label=latest.label,
                    ),
                    "is-ok",
                )
            return formatting.TRACK_STATUS_STEMS, "is-ok"
        if latest is not None:
            return formatting.TRACK_STATUS_RENDERED.format(label=latest.label), ""
        return formatting.TRACK_STATUS_NO_STEMS, ""

    def _track_meta(self, track: Track, status: str) -> str:
        parts = [
            formatting.format_duration(track.duration_s),
            formatting.origin_label(track.source.origin_kind),
            status,
        ]
        return " · ".join(parts)

    def _latest_render(self, track: Track) -> PitchSet | None:
        sets = self.service.pitch_sets(track)
        return sets[-1] if sets else None

    def _track_has_playable(self, track: Track) -> bool:
        rows, _ = self._version_rows(track)
        return any(row.preview is not None for row in rows)

    def _signal_path(self, track: Track) -> None:
        has_stems = self.service.has_separation(track)
        latest = self._latest_render(track)
        with ui.element("div").classes("sf-sigpath").mark("signal-path"):
            with ui.element("span").classes("sf-chip is-done"):
                ui.icon("check", size="xs")
                ui.label(formatting.SIGNAL_SOURCE).classes("sf-chip-label")
                ui.label(
                    formatting.SIGNAL_SOURCE_YOUTUBE
                    if track.source.origin_kind is OriginKind.YOUTUBE
                    else formatting.SIGNAL_SOURCE_LOCAL
                )
            ui.icon("chevron_right", size="xs")
            with (
                ui.element("span")
                .classes("sf-chip" + (" is-done" if has_stems else ""))
                .mark("signal-separation")
            ):
                if has_stems:
                    ui.icon("check", size="xs")
                else:
                    ui.element("span").classes("sf-dot")
                ui.label(formatting.SIGNAL_SEPARATION).classes("sf-chip-label")
                ui.label(
                    formatting.SIGNAL_SEPARATION_DONE if has_stems else formatting.SIGNAL_NOT_RUN
                )
            ui.icon("chevron_right", size="xs")
            with (
                ui.element("span")
                .classes("sf-chip" + (" is-done" if latest is not None else ""))
                .mark("signal-pitch")
            ):
                if latest is not None:
                    ui.icon("check", size="xs")
                else:
                    ui.element("span").classes("sf-dot")
                ui.label(formatting.SIGNAL_PITCH).classes("sf-chip-label")
                ui.label(
                    formatting.SIGNAL_RENDERED.format(label=latest.label)
                    if latest is not None
                    else formatting.SIGNAL_NOT_RENDERED
                )

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
