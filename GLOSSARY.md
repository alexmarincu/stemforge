# StemForge Glossary

The shared vocabulary for StemForge. Definitions are conceptual; implementation details belong in ADRs and tickets.

## Artifact

A rendered audio output derived from a Track: a pitch-shifted version, a Stem, or an export candidate. An Artifact never overwrites the Track's original audio.

## Artifact set

The group of Artifacts one Job produces, sharing provenance and one presentation gain: a Source's canonical audio and preview, a Separation's Stems, Instrumental, and previews, or a Render's shifted masters and previews.

## Batch export

An Export of every output in one Artifact set to one chosen folder, in a single action.

_Avoid_: Bulk export, mass export, export stems (the set also contains the Instrumental)

## Bundle

The complete, per-platform application package a user downloads: the frozen application, its Vendor binaries, and its licence files. A Bundle is replaced as a whole; nothing inside it is patched in place.

## Cache

The rebuildable on-disk store of imported Sources and derived Artifacts. It is shared across Workspaces and survives restarts, but is not a project library: its contents may be discarded and rebuilt from the Source.

## Corresponding Source

The source code, build scripts, and materials needed to rebuild a distributed binary, as defined by the GPL. StemForge ships it with each release rather than relying on a repository link alone.

## Degraded mode

A way of running StemForge with one capability missing — currently, Preview — while the rest of the app still works. A degraded mode is announced, never silent.

## Diagnostics report

The local summary of versions, paths, settings, and recent log lines a user copies when asking for help. StemForge never uploads it, zips it, or writes it on its own.

## Export

Copying an Artifact out of StemForge to a location on disk the user chooses. An Export always writes a new file and never overwrites an existing one; exporting an Artifact set writes each of its outputs into one chosen folder. Saving a local Source's Native file is a separate operation, not an Export.

## Import

Loading a Track's audio from a Source into the Workspace and Cache. A local import reads a file; a YouTube import downloads it first.

_Avoid_: Load, add, ingest

## Instrumental

The non-vocal mix of a Track, stored as a derived Artifact (the sum of the non-vocal Stems) and presented as one of the two primary Separation outputs. It is not a Stem.

## Interface language

The language StemForge uses for user-visible text: window copy, error headlines and actions, native dialog prose, and launch-failure messages. It is a user preference with a system-derived default, not a property of a Track or Source. Logs, diagnostics, exported filenames, and audio tags remain English.

_Avoid_: UI language, language pack.

## Job

A long-running local operation with observable progress and cancellation: import, download, decode, pitch render, Separation, or Export.

## Model weights

The pretrained parameters of the separation model. They are a third-party download, not part of StemForge's licensed bundle, and are not covered by its GPL-3.0 licence.

## Native file

The untouched file an Import copies into the Cache before decoding to canonical audio. For a local Import it is the user's own file; for a YouTube Import it is the downloaded media.

_Avoid_: Raw file, source file; "original" alone, which names the Outputs card's canonical master.

## Original

The unshifted master Artifact of a Track, presented first in Outputs. It is the canonical audio, not the Native file.

_Avoid_: Source file, raw file.

## Partial

A file that is still being written and has not been promoted to an Artifact. Partials are never played, exported, or counted as cached audio, and they do not survive a restart.

## Pitch shift

A transformed version of a Track or Stem shifted by an integer number of semitones while preserving tempo.

## Portable build

The Windows Bundle distributed as an archive with no installer. "Portable" describes the absence of installation, not where StemForge keeps its data.

## Preflight

The startup checks that decide whether StemForge can open, and with which capabilities. A failed preflight either stops the app before its window exists or puts it into a degraded mode.

## Preview

In-app playback of a Track or Artifact before Export.

## Render

The explicit action that produces a Pitch shift Artifact set from a Track's original audio or from all of its Stems. A Render never modifies its inputs, does not imply a Separation, and leaves earlier sets cached as independent Artifacts.

## Renderer guard

A launch-time environment override that keeps the native window's web view on a renderer path known to paint correctly on a given host. It protects against a blank view; it is not a performance setting and does not change the page.

_Avoid_: workaround, fix

## Save original

Copying a local Source's Native file to a location the user chooses, byte for byte and with the Native file's extension. It never encodes, rewrites tags, applies gain, resamples, or downmixes, and it is not an Export because the Native file is not an Artifact.

_Avoid_: Passthrough export, native export, raw export.

## Separation

The local process that splits a Track into Stems using a separation model.

## Source

Where a Track came from: a local audio file or a YouTube URL.

## Source offer

The shipped statement that identifies where Corresponding Source can be obtained and how to request it. It accompanies every binary artifact, not just the repository.

## Stem

An isolated component of a Track produced by Separation. StemForge generates four (vocals, drums, bass, other) and presents Vocals and Instrumental as the primary outputs.

## Thin AppImage

The single-file Linux Bundle that relies on the host's GTK, WebKitGTK, and GStreamer for its native window and Preview instead of carrying them. It is not self-contained, and it is not the primary Linux artifact.

## Third-party notices

The shipped inventory of every third-party component, its version, licence, and source link.

## Track

A single audio item loaded into the Workspace, identified by its Source and title, with one original audio artifact.

## Vendor binary

A third-party executable shipped inside a Bundle rather than installed by the user — currently FFmpeg, ffprobe, Rubber Band, and Deno. Each keeps its own licence and notice.

## Workspace

The session-scoped collection of Tracks and their Artifacts. It is ephemeral and is not a persistent project library.

## YouTube import

Loading a Track's audio from a YouTube URL. It is the only operation in StemForge that uses the network, and StemForge accepts a single public, non-age-gated video per import — no playlists, channels, accounts, or cookies.
