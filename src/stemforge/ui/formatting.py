"""Formatting helpers and the ``(code, reason)`` error copy table.

User-visible page copy resolves through the English catalog; this module keeps
the formatting helpers and the coded error table. ``errors.py`` stays copy-free.
Every code has an entry so a newer worker's unknown code falls back to
``internal.unexpected``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from stemforge import errors, i18n
from stemforge.domain.models import JobKind, OriginKind

if TYPE_CHECKING:
    from stemforge.domain.models import Track
    from stemforge.models.spec import ModelSpec

MAX_HEADLINE_LENGTH: Final = 90
_MIB: Final = 1024 * 1024
_GIB: Final = 1024 * _MIB

APP_NAME: Final = "StemForge"
APP_TAGLINE: Final = "Local stem separation, pitch shifting, and export."
BRAND_SUBTITLE: Final = "local · offline"
WINDOW_TITLE: Final = "{app} — {track}"

MODEL_LICENCE_TITLE: Final = "Separation model not installed"
MODEL_LICENCE_SUMMARY: Final = (
    "htdemucs runs locally. The weights download once from Hugging Face and are "
    "verified by SHA-256."
)
MODEL_LICENCE_NOTICE: Final = (
    "These weights are **not part of StemForge** and are **not covered by StemForge's "
    "GPL-3.0 licence**. The Demucs author states their licence is unclear: the training "
    "data included research-only MusDB material, and FAIR's models are likely "
    "non-commercial. Download only if that is acceptable to you."
)
MODEL_LICENCE_ACCEPT_LABEL: Final = "Download model"
MODEL_LICENCE_DETAILS_LABEL: Final = "Licence details"
MODEL_LICENCE_DECLINE_LABEL: Final = "Cancel"

# The shipped YouTube copy, exact and in one place.
YOUTUBE_DISCLAIMER: Final = (
    "Only import content you have the right to use. "
    "YouTube's Terms of Service prohibit downloading."
)
YOUTUBE_CONSENT_TITLE: Final = "Import from YouTube"
YOUTUBE_CONSENT_BODY: Final = (
    "StemForge downloads audio with yt-dlp, entirely on this machine. YouTube's Terms of "
    "Service prohibit downloading except where the law permits, and downloading may infringe "
    "copyright. Only continue if you have the right to use this content."
)
YOUTUBE_CONSENT_ACCEPT: Final = "I have the right to use this content"
YOUTUBE_CONSENT_CANCEL: Final = "Cancel"
YOUTUBE_ACTION_LABEL: Final = "Import from YouTube"
YOUTUBE_MODAL_TITLE: Final = "Import from YouTube"
IMPORT_AUDIO_LABEL: Final = "Import audio"
IMPORT_CANCELLED: Final = "Import cancelled."
ADD_HINT: Final = "or drop files anywhere"
ADD_BUSY_HINT: Final = "Wait for the current job to finish."
ADD_DIALOG_HINT: Final = "Finish choosing files first."
DROP_UNUSABLE_AUDIO: Final = (
    f"That drop didn't include a usable audio file. Use {IMPORT_AUDIO_LABEL}."
)
YOUTUBE_IMPORT_LABEL: Final = "Import"
YOUTUBE_URL_PLACEHOLDER: Final = "https://www.youtube.com/watch?v=…"
YOUTUBE_DECLINED: Final = "YouTube import is off until you accept the notice."
YOUTUBE_OFF: Final = "YouTube import is off."
YOUTUBE_SETTING_LABEL: Final = "YouTube import"
YOUTUBE_SETTING_HINT: Final = (
    "Off hides YouTube import. Local files, tracks, playback, and exports are unaffected."
)
SETTINGS_TITLE: Final = "Settings & About"
SETTINGS_APP: Final = "App"
SETTINGS_BEHAVIOUR: Final = "Behaviour"
SETTINGS_STORAGE: Final = "Storage"
SETTINGS_CLOSE: Final = "Close"
SETTINGS_VERSION_LABEL: Final = "Version"
SETTINGS_VERSION_VALUE: Final = "{app} {version}"
SETTINGS_UNKNOWN_VERSION: Final = "unknown"
SETTINGS_LICENCE_DOCUMENT: Final = "StemForge licence"
SETTINGS_ONE_FILE: Final = "{count} file"
SETTINGS_MANY_FILES: Final = "{count} files"
SETTINGS_LICENCE_LABEL: Final = "Licence"
SETTINGS_NOTICES_LABEL: Final = "Third-party notices"
SETTINGS_SOURCE_LABEL: Final = "Corresponding source"
SETTINGS_LICENCES_LABEL: Final = "Licence files"
SETTINGS_OPEN_LICENCES: Final = "Open licences folder"
SETTINGS_VIEW: Final = "View"
SETTINGS_CACHE_LABEL: Final = "Cached audio"
SETTINGS_CACHE_CLEAR: Final = "Clear cached audio"
SETTINGS_LOG_LABEL: Final = "Log folder"
SETTINGS_LOG_HINT: Final = "One file per Job; the last 20 Jobs and 7 days are kept."
SETTINGS_OPEN_LOG_FOLDER: Final = "Open log folder"
SETTINGS_BEHAVIOUR_CLOSE: Final = (
    "Closing the window stops the active job and clears the session track list. "
    "Cached audio and recent files stay, so re-importing is instant. There is no close prompt."
)
SETTINGS_BEHAVIOUR_PARTIAL: Final = (
    "If StemForge is force-quit during an export, a file ending in .partial may be "
    "left beside the destination."
)
SETTINGS_YOUTUBE_NOTE: Final = (
    "YouTube import uses yt-dlp, yt-dlp-ejs, and Deno locally. It needs no account, "
    "sends no telemetry, and uses no proxy. Age-restricted and private videos are not "
    "supported in this version."
)
SETTINGS_BUNDLED_TOOLS: Final = "Bundled versions"
SETTINGS_YTDLP_LABEL: Final = "yt-dlp"
SETTINGS_YTDLP_EJS_LABEL: Final = "yt-dlp-ejs"
SETTINGS_DENO_LABEL: Final = "Deno"
SETTINGS_RECENT_CLEAR: Final = "Clear recent files"
SETTINGS_RECENT_CLEARED: Final = "Recent files cleared."
SETTINGS_CACHE_CLEARED: Final = "Cleared {size} of cached audio. Live tracks are kept."
SETTINGS_WEIGHTS_NOTE: Final = (
    "htdemucs weights are downloaded from Hugging Face at runtime and are not "
    "covered by StemForge's licence."
)
YOUTUBE_CACHE_LABEL: Final = "YouTube cache"
YOUTUBE_CACHE_HINT: Final = (
    "yt-dlp's temporary download cache. Cached audio, the model, and recent files are not affected."
)
YOUTUBE_CACHE_CLEAR: Final = "Clear YouTube cache"
YOUTUBE_CACHE_CLEARED: Final = "YouTube cache cleared ({size})."
EMPTY_LEDE: Final = "Drop a local file. Everything runs on this machine."
EMPTY_LEDE_YOUTUBE: Final = (
    "Drop a local file or import from YouTube. Everything runs on this machine."
)
EMPTY_TITLE: Final = "Import a track"
TRACKS_LABEL: Final = "Tracks {count}"
TRACKS_EMPTY: Final = "No tracks yet"
RECENT_TITLE: Final = "Recent"
RECENT_MISSING: Final = "missing"
RECENT_SHOW_MORE: Final = "Show all"
RECENT_SHOW_LESS: Final = "Show less"
CACHE_LABEL: Final = "Cache"
DROP_TITLE: Final = "Drop audio files here"
DROP_SUBTITLE: Final = "WAV, MP3, FLAC, M4A — or choose files…"
OR_LABEL: Final = "or"
REMOVE_TRACK_TOOLTIP: Final = "Remove track and cached audio"
REMOVE_TRACK_TITLE: Final = "Remove {title}?"
REMOVE_TRACK_BODY: Final = (
    "This removes the track and deletes its cached audio. The file on disk is not touched."
)

# The multichannel decode note and empty-state card.
DOWNMIX_NOTE: Final = "Multichannel source downmixed to stereo."
EMPTY_MODEL_CARD: Final = (
    "Separation model not installed yet · 84 MB — you can import and preview now; "
    "Separation asks for the download the first time you use it."
)

# The Outputs card, transport, and signal path.
OUTPUTS_TITLE: Final = "Outputs"
OUTPUTS_SOURCE_BADGE: Final = "source"
OUTPUTS_STEM_BADGE: Final = "stem"
OUTPUTS_SHIFTED_MASTER: Final = "Shifted master"
OUTPUTS_SHOW_MORE: Final = "Show 3 more stems"
OUTPUTS_SHOW_LESS: Final = "Show fewer stems"
OUTPUTS_PLACEHOLDER: Final = "Vocals & Instrumental appear here after Separation."
OUTPUTS_PLACEHOLDER_RUNNING: Final = "Vocals & Instrumental appear here when Separation finishes."
OUTPUTS_SEPARATE: Final = "Separate"
OUTPUTS_NO_PREVIEW: Final = "No cached preview"
OUTPUTS_UNAVAILABLE: Final = "Preview unavailable."
OUTPUTS_EXPORT_TOOLTIP: Final = "Export this artifact"
OUTPUTS_REPAIR_IMPORT: Final = "Re-import the file to rebuild it."
OUTPUTS_REPAIR_IMPORT_ACTION: Final = "Re-import"
OUTPUTS_REPAIR_SEPARATE: Final = "Separate again to rebuild these stems."
OUTPUTS_REPAIR_SEPARATE_ACTION: Final = "Separate again"
OUTPUTS_REPAIR_RENDER: Final = "Render this set again to rebuild it."
OUTPUTS_REPAIR_RENDER_ACTION: Final = "Render again"
TRANSPORT_EXPORT: Final = "Export"
TRANSPORT_PREVIEW_UNAVAILABLE: Final = "Preview unavailable"
AB_LABEL: Final = "A/B"
AB_ORIGINAL: Final = "Original"
AB_LATEST_FALLBACK: Final = "Latest render"
SIGNAL_SOURCE: Final = "Source"
SIGNAL_SOURCE_LOCAL: Final = "local"
SIGNAL_SOURCE_YOUTUBE: Final = "YouTube"
SIGNAL_SEPARATION: Final = "Separation"
SIGNAL_PITCH: Final = "Pitch"
SIGNAL_SEPARATION_DONE: Final = "4 stems"
SIGNAL_NOT_RUN: Final = "not run"
SIGNAL_NOT_RENDERED: Final = "not rendered"
SIGNAL_RENDERED: Final = "{label} rendered"
TRACK_STATUS_NO_STEMS: Final = "No stems yet"
TRACK_STATUS_SEPARATING: Final = "Separating…"
TRACK_STATUS_FAILED: Final = "Separation failed"
TRACK_STATUS_STEMS: Final = "4 stems"
TRACK_STATUS_RENDERED: Final = "{label} rendered"
TRACK_STATUS_STEMS_RENDERED: Final = "{stems} · {label} rendered"

# The shared Details disclosure and the copy-only report.
DETAILS_LABEL: Final = "Details"
OPEN_LOG_LABEL: Final = "Open log"
COPY_DIAGNOSTICS_LABEL: Final = "Copy diagnostics"
DIAGNOSTICS_TITLE: Final = "Diagnostics"
DIAGNOSTICS_NOTICE: Final = "Contains local file paths. Review before sharing."
ERROR_CODE_WITH_JOB: Final = "{code} · job {job_id}"
DIAGNOSTICS_COPY: Final = "Copy"
DIAGNOSTICS_CLOSE: Final = "Close"
DIAGNOSTICS_COPIED: Final = "Copied."
DIAGNOSTICS_COPY_FAILED: Final = "Copy failed. Select the text and copy it manually."
DIAGNOSTICS_UNAVAILABLE: Final = "Diagnostics aren't available in this window."
RESOURCE_WARNING_DISMISS: Final = "Dismiss"
RECOVERY_HEADLINE: Final = (
    "StemForge closed unexpectedly last time. Unfinished work was discarded; "
    "cached audio and completed renders are intact."
)
RECOVERY_DISMISS: Final = "Dismiss"
REMOVING: Final = "Removing…"
RETRY: Final = "Retry"
CANCEL: Final = "Cancel"
REMOVE: Final = "Remove"
PLAYBACK_UNAVAILABLE_TOOLTIP: Final = "Playback unavailable"
JOB_STAGE: Final = "{label} · {stage}"
JOB_STAGE_INLINE: Final = "{label} {stage}"
JOB_STARTING: Final = "Starting"
JOB_WORKING: Final = "Working"
JOB_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        JobKind.IMPORT_LOCAL.value: "Importing",
        JobKind.IMPORT_YOUTUBE.value: "Importing",
        JobKind.SEPARATE.value: "Separating",
        JobKind.RENDER_PITCH.value: "Rendering",
        JobKind.EXPORT.value: "Exporting",
        JobKind.EXPORT_BATCH.value: "Exporting",
        JobKind.SAVE_ORIGINAL.value: "Saving original",
        JobKind.MODEL_INSTALL.value: "Downloading model",
    }
)

# The Pitch card's exact working copy.
PITCH_TITLE: Final = "Pitch"
PITCH_ORIGINAL: Final = "Original"
PITCH_SEMITONES_UNIT: Final = "semitones"
PITCH_RENDER: Final = "Render"
PITCH_RENDER_ACTION: Final = "Render {label}"
PITCH_OPEN: Final = "Open"
PITCH_OPEN_VERSION: Final = "Open {label} version"
PITCH_NEUTRAL_HINT: Final = "Set semitones to render a shifted set."
PITCH_VIEWING: Final = "Viewing this render. Step semitones to render another set."
PITCH_VIEWING_STATUS: Final = "{label} rendered · A/B compares it with Original."
PITCH_CACHED_HINT: Final = "{label} already cached — open it instead of re-rendering."
PITCH_NOT_RENDERED: Final = "Not rendered at {label} yet."
PITCH_TEMPO_NOTE: Final = (
    "Tempo preserved (Rubber Band R3 + formant). Renders never modify the original."
)
PITCH_EARLIER_SEPARATION: Final = "based on an earlier separation"

# The Separation card's copy.
SEPARATION_TITLE: Final = "Separation"
SEPARATION_NOT_SEPARATED: Final = "Not separated · htdemucs · CPU"
SEPARATION_NOT_SEPARATED_HINT: Final = "Separate this track into four stems and an Instrumental."
SEPARATION_ACTION: Final = "Separate vocals"
SEPARATION_AGAIN: Final = "Separate again"
SEPARATION_DONE: Final = "4 stems ready · htdemucs · CPU"
SEPARATION_PRIMARY_NOTE: Final = (
    "Vocals and Instrumental are primary. Drums, bass, and other stay behind Show 3 more stems."
)
SEPARATION_RUNNING: Final = "Separating · {percent}%"
SEPARATION_WORKER_NOTE: Final = "Runs in a worker process · safe to cancel"
SEPARATION_STALL: Final = "No progress for {seconds} seconds. Separation may still be working."
SEPARATION_KEEP_WAITING: Final = "Keep waiting"
SEPARATION_CANCEL: Final = "Cancel"
SEPARATION_LAST_SUCCESS: Final = "Stems from the last successful Separation are still available."
SEPARATION_MODEL_CONSENT: Final = "Press Download model to accept this notice."
SEPARATION_MODEL_DOWNLOADING: Final = "Downloading the model…"
SEPARATION_MODEL_DOWNLOAD_FAILED: Final = "Download failed"
SEPARATION_MODEL_RETRY: Final = "Retry download"
SEPARATION_MODEL_CANCELLED: Final = "Model download cancelled."
SEPARATION_CANCELLED: Final = "Separation cancelled."
EXPORT_CANCELLED: Final = "Export cancelled."
EXPORT_TITLE: Final = "Export"
EXPORT_FORMAT_LABEL: Final = "Format"
EXPORT_SOURCE_LABEL: Final = "Source"
EXPORT_DEFAULT_NAME_LABEL: Final = "File name"
EXPORT_DESTINATION_LABEL: Final = "Save to"
EXPORT_DESTINATION_DEFAULT: Final = "Your system's default location"
EXPORT_CONFIRM: Final = "Export"
EXPORT_DISMISS: Final = "Dismiss"
EXPORT_CHOOSE_FOLDER: Final = "Choose another folder…"
EXPORT_CHOOSE_FOLDER_ACTION: Final = "Choose folder…"
EXPORT_NO_TARGETS: Final = "No exportable audio is available for this Track."
EXPORT_ESTIMATE: Final = "about {size} MB"
EXPORTED_TO: Final = "Exported to {path}"
EXPORT_EXTENSION_CHANGED: Final = "Extension changed to {suffix} for {label}."
EXPORT_FORMAT_WAV24: Final = "WAV 24-bit"
EXPORT_FORMAT_FLAC24: Final = "FLAC 24-bit"
EXPORT_FORMAT_MP3_320: Final = "MP3 320"

# The single-mode sheet's Imported file block and the Save original Job.
IMPORTED_FILE_LABEL: Final = "Imported file"
IMPORTED_FILE_UNCHANGED: Final = "The file you imported, unchanged."
IMPORTED_FILE_META: Final = "{format} · {size}"
SAVE_ORIGINAL_ACTION: Final = "Save original file…"
SAVE_ORIGINAL_CANCELLED: Final = "Save original cancelled."
SAVE_ORIGINAL_SAVED_TO: Final = "Original file saved to {path}"
SAVE_ORIGINAL_EXTENSION_CHANGED: Final = "Extension changed to {suffix} for the imported file."
SAVE_ORIGINAL_CHOOSE_LOCATION: Final = "Choose another location…"

# The one batch Export entry point, sheet, results bar, and recovery actions.
OUTPUTS_EXPORT_ALL: Final = "Export all outputs"
EXPORT_BATCH_TITLE: Final = "Export all outputs"
EXPORT_BATCH_CONFIRM: Final = "Export {count} files"
EXPORT_BATCH_CONFIRM_ONE: Final = "Export 1 file"
EXPORT_BATCH_SUCCESS: Final = "Exported {count} files to {path}"
EXPORT_BATCH_SUCCESS_ONE: Final = "Exported 1 file to {path}"
EXPORT_BATCH_PARTIAL_FAILED: Final = "{written} of {total} files exported; {count} failed"
EXPORT_BATCH_PARTIAL_STOPPED: Final = "{written} of {total} files exported; {count} remaining"
EXPORT_BATCH_CANCELLED: Final = "Export cancelled — {written} of {total} files written"
EXPORT_BATCH_NONE: Final = "No files were exported"
EXPORT_BATCH_FOLDER_REQUIRED: Final = "Choose a folder to export."
EXPORT_NOT_EXPORTED: Final = "Not exported"
EXPORT_BATCH_REMAINING: Final = "Export remaining {count}"
SEPARATION_PRIMARY_BADGE: Final = "primary"
SEPARATION_SUM_BADGE: Final = "sum"
SEPARATION_ROLE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "vocals": "Vocals",
        "instrumental": "Instrumental",
        "drums": "Drums",
        "bass": "Bass",
        "other": "Other",
    }
)


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """One sentence of plain English and exactly one recovery action."""

    headline: str
    action: str


@dataclass(frozen=True, slots=True)
class ModelLicenceCopy:
    """The model gate's user-facing prose."""

    title: str
    summary: str
    identity: str
    notice: str
    accept_label: str
    details_label: str
    decline_label: str


COPY_BY_CODE: Final[Mapping[str, ErrorCopy]] = MappingProxyType(
    {
        errors.IMPORT_NO_AUDIO_STREAM: ErrorCopy(
            "This file has no audio track.",
            "Choose a file with audio.",
        ),
        errors.IMPORT_UNSUPPORTED_SOURCE: ErrorCopy(
            "Only YouTube video links are supported.",
            "Paste a YouTube video link.",
        ),
        errors.IMPORT_DOWNLOAD_FAILED: ErrorCopy(
            "The download failed. Retry, or open the job log for details.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.IMPORT_YOUTUBE_UNAVAILABLE: ErrorCopy(
            "This video isn't available. It may be private, removed, "
            "region-locked, or members-only.",
            "Try another video.",
        ),
        errors.IMPORT_YOUTUBE_AGE_RESTRICTED: ErrorCopy(
            "This video is age-restricted. StemForge doesn't sign in to YouTube.",
            "Choose a video without an age gate.",
        ),
        errors.IMPORT_FILE_UNAVAILABLE: ErrorCopy(
            "Can't open that file. It may have moved, been deleted, or be unreadable.",
            "Choose a file you can open.",
        ),
        errors.IMPORT_TOO_LONG: ErrorCopy(
            "This track is too long. The limit is 30:00.",
            "Trim it and import again.",
        ),
        errors.IMPORT_YOUTUBE_URL_INVALID: ErrorCopy(
            "That doesn't look like a YouTube link.",
            "Paste a YouTube video link.",
        ),
        errors.IMPORT_YOUTUBE_PLAYLIST: ErrorCopy(
            "That's a playlist or channel. Paste a single video link.",
            "Paste a single video link.",
        ),
        errors.IMPORT_YOUTUBE_TOO_LONG: ErrorCopy(
            "This video is longer than the 15-minute limit.",
            "Choose a shorter video.",
        ),
        errors.IMPORT_YOUTUBE_LIVE: ErrorCopy(
            "Live and upcoming streams can't be imported.",
            "Choose a finished video.",
        ),
        errors.IMPORT_YOUTUBE_RATE_LIMITED: ErrorCopy(
            "YouTube is limiting downloads from this machine.",
            "Wait a few minutes, then retry.",
        ),
        errors.IMPORT_YOUTUBE_EXTRACTOR_OUTDATED: ErrorCopy(
            "YouTube changed and this StemForge version can't read the video.",
            "Update StemForge.",
        ),
        errors.IMPORT_NETWORK: ErrorCopy(
            "No connection.",
            "Check your network and retry.",
        ),
        errors.DECODE_FAILED: ErrorCopy(
            "StemForge couldn't decode this file.",
            "Try another copy of the file.",
        ),
        errors.SEPARATION_MODEL_MISSING: ErrorCopy(
            "Separation needs the htdemucs model first.",
            "Download the model.",
        ),
        errors.SEPARATION_MODEL_DOWNLOAD_FAILED: ErrorCopy(
            "The model download failed.",
            "Check your network and try again.",
        ),
        errors.SEPARATION_WORKER_CRASHED: ErrorCopy(
            "Separation stopped unexpectedly.",
            "Try again; the details are in the job log.",
        ),
        errors.SEPARATION_ENGINE_ERROR: ErrorCopy(
            "Separation failed while processing this track.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.SEPARATION_PROTOCOL_MISMATCH: ErrorCopy(
            "This version of StemForge can't read the separation worker's output.",
            "Reinstall StemForge or update to a newer version.",
        ),
        errors.SEPARATION_OUTPUT_INVALID: ErrorCopy(
            "Separation finished, but its output didn't pass StemForge's checks.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.PITCH_ENGINE_MISSING: ErrorCopy(
            "The pitch-shift engine is missing.",
            "Reinstall StemForge.",
        ),
        errors.PITCH_RENDER_FAILED: ErrorCopy(
            "The pitch render failed.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.PITCH_OUTPUT_INVALID: ErrorCopy(
            "The pitch render finished, but its output didn't pass StemForge's checks.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.MEDIA_PROBE_FAILED: ErrorCopy(
            "StemForge can't read this file. It may be damaged or in a format it doesn't support.",
            "Try WAV, MP3, FLAC, or M4A.",
        ),
        errors.MEDIA_ENCODE_FAILED: ErrorCopy(
            "Couldn't encode the file.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.MEDIA_VALIDATION_FAILED: ErrorCopy(
            "The generated audio didn't pass validation.",
            "Try again; if it keeps failing, open the job log.",
        ),
        errors.EXPORT_WRITE_FAILED: ErrorCopy(
            "Couldn't save the file.",
            "Try another folder; the details are in the job log.",
        ),
        errors.EXPORT_ORIGINAL_UNAVAILABLE: ErrorCopy(
            "The imported file is no longer available.",
            "Import the Track again.",
        ),
        errors.STORAGE_DISK_FULL: ErrorCopy(
            "Not enough disk space.",
            "Clear cached audio to make room.",
        ),
        errors.STORAGE_CACHE_FULL: ErrorCopy(
            "The audio cache is full (8 GiB), and everything in it is in use.",
            "Remove a track or clear cached audio.",
        ),
        errors.STORAGE_PERMISSION_DENIED: ErrorCopy(
            "StemForge can't write to its data folder.",
            "Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder.",
        ),
        errors.INTERNAL_UNEXPECTED: ErrorCopy(
            "Something went wrong.",
            "Open the log for details, then try again.",
        ),
        errors.PREFLIGHT_PLATFORM_UNSUPPORTED: ErrorCopy(
            "StemForge runs on 64-bit Linux and 64-bit Windows.",
            "Linux needs glibc 2.35 or newer; Windows needs Windows 10 22H2 or Windows 11, x64.",
        ),
        errors.PREFLIGHT_WINDOW_UNAVAILABLE: ErrorCopy(
            "StemForge needs GTK 3 and WebKitGTK 4.1 to open its window.",
            "Debian/Ubuntu: `sudo apt install libgtk-3-0 libwebkit2gtk-4.1-0 "
            "gir1.2-webkit2-4.1` · Fedora: `sudo dnf install gtk3 webkit2gtk4.1` · "
            "Arch: `sudo pacman -S gtk3 webkit2gtk-4.1`",
        ),
        errors.PREFLIGHT_WEBVIEW2_MISSING: ErrorCopy(
            "StemForge needs the Microsoft Edge WebView2 Runtime.",
            "Install the Evergreen Bootstrapper from "
            "https://developer.microsoft.com/microsoft-edge/webview2/ and start StemForge again.",
        ),
        errors.PREFLIGHT_BUNDLE_DAMAGED: ErrorCopy(
            "StemForge is missing files from its installation.",
            "Reinstall StemForge, or if you use the portable build, extract the archive again.",
        ),
        errors.PREFLIGHT_STORAGE_UNWRITABLE: ErrorCopy(
            "StemForge can't write to its data folder.",
            "Check the folder's permissions, or set `STEMFORGE_HOME` to a writable folder.",
        ),
        errors.PREFLIGHT_PREVIEW_UNAVAILABLE: ErrorCopy(
            "Playback isn't available on this system.",
            "WebKitGTK needs GStreamer's good plugins: `sudo apt install "
            "gstreamer1.0-plugins-good` (Fedora `gstreamer1-plugins-good`, Arch "
            "`gst-plugins-good`). Separation, pitch, and export still work.",
        ),
        errors.PLAYBACK_UNAVAILABLE: ErrorCopy(
            "Couldn't play this file. It may have been cleared from the cache.",
            "Re-import, re-separate, or re-render the file to rebuild it.",
        ),
        errors.LICENSING_FILES_MISSING: ErrorCopy(
            "The licence files are missing from this installation.",
            "Reinstall StemForge, or open the notices in the repository.",
        ),
        errors.LICENSING_OPEN_FAILED: ErrorCopy(
            "Couldn't open the licences folder.",
            "Open the LICENSES folder in your file manager from the StemForge folder.",
        ),
        errors.LOGGING_OPEN_FAILED: ErrorCopy(
            "Couldn't open the log folder.",
            "Open the logs folder in your file manager from StemForge's data folder.",
        ),
        errors.CANCELLED: ErrorCopy(
            "Cancelled.",
            "No action needed.",
        ),
    }
)

REASON_COPY: Final[Mapping[tuple[str, str], ErrorCopy]] = MappingProxyType(
    {
        (errors.IMPORT_UNSUPPORTED_SOURCE, "extension"): ErrorCopy(
            "That file type isn't supported.",
            "Choose a WAV, MP3, FLAC, or M4A file.",
        ),
        (errors.SEPARATION_MODEL_MISSING, "absent"): ErrorCopy(
            "Separation needs the htdemucs model first.",
            "Download the model.",
        ),
        (errors.SEPARATION_MODEL_MISSING, "checksum"): ErrorCopy(
            "The separation model needs to be downloaded again.",
            "The cached copy didn't match its checksum. Download the model.",
        ),
        (errors.SEPARATION_MODEL_DOWNLOAD_FAILED, "offline"): ErrorCopy(
            "The model download failed.",
            "No connection. Check your network and try again.",
        ),
        (errors.SEPARATION_MODEL_DOWNLOAD_FAILED, "http"): ErrorCopy(
            "The model download failed.",
            "Hugging Face didn't respond as expected. Try again later.",
        ),
        (errors.EXPORT_WRITE_FAILED, "permission"): ErrorCopy(
            "Couldn't save the file.",
            "Choose another folder, or check its permissions.",
        ),
        (errors.EXPORT_WRITE_FAILED, "in_use"): ErrorCopy(
            "Couldn't save the file.",
            "Close the file in other apps and try again.",
        ),
        (errors.EXPORT_WRITE_FAILED, "name_too_long"): ErrorCopy(
            "Couldn't save the file.",
            "Choose a shorter name or folder.",
        ),
        (errors.EXPORT_WRITE_FAILED, "device_gone"): ErrorCopy(
            "Couldn't save the file.",
            "Reconnect the drive and try again.",
        ),
        (errors.EXPORT_WRITE_FAILED, "other"): ErrorCopy(
            "Couldn't save the file.",
            "Try another folder; the details are in the job log.",
        ),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "missing"): ErrorCopy(
            "The imported file is no longer in StemForge's cache.",
            "Import the Track again.",
        ),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "changed"): ErrorCopy(
            "The imported file has changed since it was imported.",
            "Import the Track again to make a fresh copy.",
        ),
        (errors.EXPORT_ORIGINAL_UNAVAILABLE, "not_local"): ErrorCopy(
            "This Track came from YouTube. There's no imported file to save.",
            "Choose a local Track.",
        ),
        (errors.LICENSING_OPEN_FAILED, "log"): ErrorCopy(
            "Couldn't open the log file.",
            "Open it from the logs folder.",
        ),
        # Export owns this reason so its disk-full action is "choose another
        # location" rather than the cache's "clear cached audio".
        (errors.STORAGE_DISK_FULL, "export"): ErrorCopy(
            "Not enough disk space.",
            "Choose another location.",
        ),
    }
)


def model_licence_copy(spec: ModelSpec) -> ModelLicenceCopy:
    """Return the model-gate copy for one pinned model record.

    The licence prose lives here, not in the lock or the pipeline, so the UI is
    the only module with user-facing sentences.
    """
    megabytes = f"{spec.size_bytes / 1_000_000:.0f} MB"
    return ModelLicenceCopy(
        title=MODEL_LICENCE_TITLE,
        summary=f"{MODEL_LICENCE_SUMMARY[:-1]} ({megabytes}).",
        identity=(f"{spec.repo_id} · revision {spec.revision[:8]}… · {spec.size_bytes:,} bytes"),
        notice=MODEL_LICENCE_NOTICE,
        accept_label=MODEL_LICENCE_ACCEPT_LABEL,
        details_label=MODEL_LICENCE_DETAILS_LABEL,
        decline_label=MODEL_LICENCE_DECLINE_LABEL,
    )


def copy_for(
    code: str,
    reason: str | None = None,
    *,
    context: Mapping[str, object] | None = None,
    origin: OriginKind | None = None,
) -> ErrorCopy:
    """Return the copy for ``(code, reason)``, falling back to ``internal.unexpected``.

    ``context`` carries the same machine values the raising code logged — a
    duration, a destination folder, or needed/free bytes — so the sentence can
    name them without parsing log text. ``origin`` selects the YouTube wording
    for a shared import code. Any interpolated headline that would exceed the
    details limit falls back to the static sentence rather than breaking the
    cap.
    """
    base = _base_copy(code, reason)
    result = base
    if code == errors.IMPORT_NO_AUDIO_STREAM and origin is OriginKind.YOUTUBE:
        result = ErrorCopy("This video has no audio track.", "Choose a video with audio.")
    elif code in {errors.IMPORT_TOO_LONG, errors.IMPORT_YOUTUBE_TOO_LONG}:
        seconds = _number(context, "duration_s")
        if seconds is not None:
            duration = format_duration(seconds)
            if code == errors.IMPORT_TOO_LONG:
                result = ErrorCopy(
                    f"This track is {duration} long. The limit is 30:00.",
                    base.action,
                )
            else:
                result = ErrorCopy(
                    f"This video is {duration} — longer than the 15-minute limit.",
                    base.action,
                )
    elif code == errors.EXPORT_WRITE_FAILED:
        folder = _folder_name(context)
        if folder is not None:
            result = ErrorCopy(f"Couldn't save the file to {folder}.", base.action)
    elif code == errors.STORAGE_DISK_FULL:
        folder = _folder_name(context)
        required = _number(context, "required")
        free = _number(context, "free")
        if reason == "export" and folder is not None and required is not None:
            result = ErrorCopy(
                f"Not enough space at {folder}. This file needs about "
                f"{format_bytes(round(required))}.",
                base.action,
            )
        elif required is not None and free is not None:
            result = ErrorCopy(
                "Not enough disk space. This import needs about "
                f"{format_bytes(round(required))} and only "
                f"{format_bytes(round(free))} is free.",
                base.action,
            )
    elif code == errors.STORAGE_PERMISSION_DENIED:
        path = _path_label(context)
        if path is not None:
            result = ErrorCopy(f"StemForge can't write to {path}.", base.action)
    if len(result.headline) > MAX_HEADLINE_LENGTH:
        return base
    return result


def _base_copy(code: str, reason: str | None) -> ErrorCopy:
    if reason is not None:
        specific = REASON_COPY.get((code, reason))
        if specific is not None:
            return specific
    return COPY_BY_CODE.get(code, COPY_BY_CODE[errors.INTERNAL_UNEXPECTED])


def _number(context: Mapping[str, object] | None, key: str) -> float | None:
    value = (context or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _folder_name(context: Mapping[str, object] | None) -> str | None:
    """The last path component for a sentence; never the full path."""
    raw = (context or {}).get("path")
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw)
    if not text:
        return None
    name = Path(text).name or text
    if len(name) > 28:
        name = f"{name[:27]}…"
    return name


def _path_label(
    context: Mapping[str, object] | None,
    *,
    length: int = 60,
) -> str | None:
    raw = (context or {}).get("path")
    if not isinstance(raw, (str, Path)):
        return None
    text = str(raw)
    if not text:
        return None
    if len(text) <= length:
        return text
    return f"…{text[-(length - 1) :]}"


def reason_from_detail(detail: str | None) -> str | None:
    """Pick the stable copy reason out of a log-only detail string.

    Details are log text, not copy: prefer the explicit ``reason=`` token. A
    single-token detail is already a stable classifier and is used as-is; a
    sentence is not a reason.
    """
    text = (detail or "").strip()
    if not text:
        return None
    for part in text.replace("\n", " ").split():
        if part.startswith("reason="):
            return part.removeprefix("reason=")
    return text if " " not in text else None


def fatal_copy(code: str, *, path: str | None = None) -> ErrorCopy:
    """Return the exact preflight fatal sentence and fix line.

    The storage sentence names the unwritable path; every other fatal code uses
    its static sentence.
    """
    copy = copy_for(code)
    if path is not None and code in {
        errors.PREFLIGHT_STORAGE_UNWRITABLE,
        errors.STORAGE_PERMISSION_DENIED,
    }:
        return ErrorCopy(f"StemForge can't write to {path}.", copy.action)
    return copy


def format_duration(seconds: float | None) -> str:
    """Format a duration as ``m:ss`` (or ``h:mm:ss``); an unknown value is an em dash."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "—"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_semitones(semitones: int) -> str:
    """Format a signed shift the way version tabs and the stepper do."""
    if semitones == 0:
        return "0"
    return f"+{semitones}" if semitones > 0 else f"\u2212{abs(semitones)}"


def level_adjusted_label(gain_db: float) -> str | None:
    """The quiet level-adjusted badge, or ``None`` when no attenuation was applied."""
    if gain_db == 0:
        return None
    return i18n.t("page.level_adjusted", gain=abs(gain_db))


def format_sample_rate(rate: int | None) -> str | None:
    """Format a sample rate (44.1 kHz, 48 kHz)."""
    if rate is None or rate <= 0:
        return None
    value = rate / 1000
    if value.is_integer():
        return f"{int(value)} kHz"
    return f"{value:.1f} kHz"


def format_channels(channels: int | None) -> str | None:
    """Name the channel layout when it is conventional, otherwise count it."""
    if channels is None or channels <= 0:
        return None
    if channels == 1:
        return i18n.t("page.channels_mono")
    if channels == 2:
        return i18n.t("page.channels_stereo")
    return i18n.t("page.channels_count", count=channels)


def origin_label(kind: OriginKind) -> str:
    """Name a Source origin the way the rail and detail header do."""
    if kind is OriginKind.YOUTUBE:
        return i18n.t("page.origin_youtube")
    return i18n.t("page.origin_local")


def track_icon(kind: OriginKind) -> str:
    """The rail's music/video type icon for a Source origin."""
    return "movie" if kind is OriginKind.YOUTUBE else "music_note"


def source_reference(origin: str, kind: OriginKind) -> str:
    """The local filename or the YouTube URL shown in the detail header."""
    return origin if kind is OriginKind.YOUTUBE else Path(origin).name


def detail_meta(track: Track) -> list[str]:
    """Origin · filename or URL · duration · 44.1 kHz stereo."""
    source = track.source
    meta = [origin_label(source.origin_kind)]
    reference = source_reference(source.origin, source.origin_kind)
    if reference:
        meta.append(reference)
    meta.append(format_duration(track.duration_s))
    sample_rate = format_sample_rate(source.metadata.sample_rate)
    if sample_rate:
        meta.append(sample_rate)
    channels = format_channels(source.metadata.channels)
    if channels:
        meta.append(channels)
    return meta


def format_bytes(size: int) -> str:
    """Format a cache size with one decimal, MB below 1 GiB and GB above."""
    if size <= 0:
        return "0.0 MB"
    if size < _GIB:
        return f"{size / _MIB:.1f} MB"
    return f"{size / _GIB:.1f} GB"


def cache_cleared_message(freed_bytes: int) -> str:
    """The one sentence both Clear cached audio surfaces show."""
    return SETTINGS_CACHE_CLEARED.format(size=format_bytes(freed_bytes))


def imported_file_meta(suffix: str, size_bytes: int) -> str:
    """The Imported file block's container and size, e.g. ``FLAC · 12.3 MB``."""
    return IMPORTED_FILE_META.format(
        format=suffix.lstrip(".").upper(),
        size=format_bytes(size_bytes),
    )


def file_count_label(count: int) -> str:
    """Name a count of recent files, singular only at one."""
    template = SETTINGS_ONE_FILE if count == 1 else SETTINGS_MANY_FILES
    return template.format(count=count)


def export_batch_confirm(count: int) -> str:
    """The batch sheet's confirm label, singular only at one."""
    template = EXPORT_BATCH_CONFIRM_ONE if count == 1 else EXPORT_BATCH_CONFIRM
    return template.format(count=count)


def export_batch_remaining(count: int) -> str:
    """The results bar's recovery action for the items that were not written."""
    return EXPORT_BATCH_REMAINING.format(count=count)


def export_batch_headline(
    *,
    written: int,
    failed: int,
    not_exported: int,
    cancelled: bool,
    destination: Path | None = None,
) -> str:
    """The one results-bar sentence for a batch's terminal outcome.

    The counts come from the per-item record, never from the Job's state: a
    failed batch may have written files, and a cancelled one usually has.
    """
    total = written + failed + not_exported
    if cancelled:
        return EXPORT_BATCH_CANCELLED.format(written=written, total=total)
    if total and written == total and destination is not None:
        template = EXPORT_BATCH_SUCCESS_ONE if written == 1 else EXPORT_BATCH_SUCCESS
        return template.format(count=written, path=destination)
    if total == 0:
        return EXPORT_BATCH_NONE
    if not_exported:
        return EXPORT_BATCH_PARTIAL_STOPPED.format(
            written=written,
            total=total,
            count=failed + not_exported,
        )
    return EXPORT_BATCH_PARTIAL_FAILED.format(count=failed, written=written, total=total)


def cache_percent(used: int, cap: int) -> int:
    """The footer meter's width: 0 to 100, never above a full bar."""
    if cap <= 0 or used <= 0:
        return 0
    return min(100, max(0, round(used * 100 / cap)))


def format_relative_time(when: datetime, *, now: datetime | None = None) -> str:
    """Short, local, human recency labels for the recent-files list."""
    current = now if now is not None else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = max(0, int((current - when).total_seconds()))
    if seconds < 60:
        return i18n.t("page.recent_just_now")
    minutes = seconds // 60
    if minutes < 60:
        return i18n.t("page.recent_minutes_ago", count=minutes)
    hours = minutes // 60
    if hours < 24:
        return i18n.t("page.recent_hours_ago", count=hours)
    return i18n.t("page.recent_days_ago", count=hours // 24)
