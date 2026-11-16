"""User-facing labels and the ``(code, reason)`` error copy table.

This is the only module with headlines, remediation actions, and user-facing
prose; ``errors.py`` stays copy-free (ticket 14 §9). Every code has an entry so a
newer worker's unknown code falls back to ``internal.unexpected``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from stemforge import errors

MAX_HEADLINE_LENGTH: Final = 90


@dataclass(frozen=True, slots=True)
class ErrorCopy:
    """One sentence of plain English and exactly one recovery action."""

    headline: str
    action: str


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
            "Check the folder's permissions, or set STEMFORGE_HOME to a writable folder.",
        ),
        errors.INTERNAL_UNEXPECTED: ErrorCopy(
            "Something went wrong.",
            "Open the log for details, then try again.",
        ),
        errors.PREFLIGHT_PLATFORM_UNSUPPORTED: ErrorCopy(
            "StemForge runs on 64-bit Linux and 64-bit Windows.",
            "Linux needs glibc 2.35 or newer; Windows needs Windows 10 22H2 or 11, x64.",
        ),
        errors.PREFLIGHT_WINDOW_UNAVAILABLE: ErrorCopy(
            "StemForge needs GTK 3 and WebKitGTK 4.1 to open its window.",
            "Install GTK 3 and WebKitGTK 4.1, then start StemForge again.",
        ),
        errors.PREFLIGHT_WEBVIEW2_MISSING: ErrorCopy(
            "StemForge needs the Microsoft Edge WebView2 Runtime.",
            "Install the Evergreen Bootstrapper from Microsoft, then start StemForge again.",
        ),
        errors.PREFLIGHT_BUNDLE_DAMAGED: ErrorCopy(
            "StemForge is missing files from its installation.",
            "Reinstall StemForge, or extract the portable archive again.",
        ),
        errors.PREFLIGHT_STORAGE_UNWRITABLE: ErrorCopy(
            "StemForge can't write to its data folder.",
            "Check the folder's permissions, or set STEMFORGE_HOME to a writable folder.",
        ),
        errors.PREFLIGHT_PREVIEW_UNAVAILABLE: ErrorCopy(
            "Playback isn't available on this system.",
            "Install gstreamer1.0-plugins-good; separation, pitch, and export still work.",
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


def copy_for(code: str, reason: str | None = None) -> ErrorCopy:
    """Return the copy for ``(code, reason)``, falling back to ``internal.unexpected``."""
    if reason is not None:
        specific = REASON_COPY.get((code, reason))
        if specific is not None:
            return specific
    return COPY_BY_CODE.get(code, COPY_BY_CODE[errors.INTERNAL_UNEXPECTED])
