"""User-facing labels and the ``(code, reason)`` error copy table.

This is the only module with headlines, remediation actions, and user-facing
prose; ``errors.py`` stays copy-free (ticket 14 §9). Every code has an entry so a
newer worker's unknown code falls back to ``internal.unexpected``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from stemforge import errors

if TYPE_CHECKING:
    from stemforge.models.spec import ModelSpec

MAX_HEADLINE_LENGTH: Final = 90
_MIB: Final = 1024 * 1024
_GIB: Final = 1024 * _MIB

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

# Ticket 09 §7: the shipped YouTube copy, exact and in one place.
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
YOUTUBE_IMPORT_LABEL: Final = "Import"
YOUTUBE_PENDING: Final = "Valid YouTube link. Import isn't available in this build yet."
YOUTUBE_DECLINED: Final = "YouTube import is off until you accept the notice."
YOUTUBE_UNAVAILABLE: Final = "YouTube import isn't available in this build yet."
YOUTUBE_OFF: Final = "YouTube import is off."
YOUTUBE_SETTING_LABEL: Final = "YouTube import"
YOUTUBE_SETTING_HINT: Final = (
    "Off hides the YouTube row. Local files, tracks, playback, and exports are unaffected."
)
SETTINGS_TITLE: Final = "Settings & About"
SETTINGS_BEHAVIOUR: Final = "Behaviour"
SETTINGS_CLOSE: Final = "Close"
EMPTY_LEDE: Final = "Drop a local file. Everything runs on this machine."
EMPTY_LEDE_YOUTUBE: Final = (
    "Drop a local file or pull audio from YouTube. Everything runs on this machine."
)


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """One sentence of plain English and exactly one recovery action."""

    headline: str
    action: str


@dataclass(frozen=True, slots=True)
class ModelLicenceCopy:
    """The model gate's user-facing prose (ticket 13 §7, ticket 08 §10)."""

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
    }
)


def model_licence_copy(spec: ModelSpec) -> ModelLicenceCopy:
    """Return the model-gate copy for one pinned model record.

    The licence prose lives here, not in the lock or the pipeline, so the UI is
    the only module with user-facing sentences (ticket 14 §9).
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


def copy_for(code: str, reason: str | None = None) -> ErrorCopy:
    """Return the copy for ``(code, reason)``, falling back to ``internal.unexpected``."""
    if reason is not None:
        specific = REASON_COPY.get((code, reason))
        if specific is not None:
            return specific
    return COPY_BY_CODE.get(code, COPY_BY_CODE[errors.INTERNAL_UNEXPECTED])


def fatal_copy(code: str, *, path: str | None = None) -> ErrorCopy:
    """Return the exact preflight fatal sentence and fix line.

    The storage sentence names the unwritable path; every other fatal code uses
    its static ticket 14 §3 sentence.
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


def format_sample_rate(rate: int | None) -> str | None:
    """Format a sample rate the way the prototype does (44.1 kHz, 48 kHz)."""
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
        return "mono"
    if channels == 2:
        return "stereo"
    return f"{channels} ch"


def format_bytes(size: int) -> str:
    """Format a cache size with one decimal, MB below 1 GiB and GB above."""
    if size <= 0:
        return "0.0 MB"
    if size < _GIB:
        return f"{size / _MIB:.1f} MB"
    return f"{size / _GIB:.1f} GB"


def format_relative_time(when: datetime, *, now: datetime | None = None) -> str:
    """Short, local, human recency labels for the recent-files list."""
    current = now if now is not None else datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    seconds = max(0, int((current - when).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h ago"
    return f"{hours // 24} d ago"
