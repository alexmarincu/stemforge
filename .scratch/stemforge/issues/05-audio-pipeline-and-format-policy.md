# 05 — Audio pipeline and format policy

**Type:** grilling
**Status:** resolved
**Blocked by:** 01, 03

## Question

Settle the canonical audio pipeline and format policy:

- Decode: ffmpeg to 32-bit float WAV? Research 03 recommends stereo 44.1 kHz f32; confirm whether to preserve 48 kHz for pitch-only sessions. Channel handling for mono, stereo, and multichannel sources?
- Processing: where Pitch shift and Separation sit relative to decode; whether intermediate artifacts are always WAV on disk. Research 03 recommends decode → separate → pitch per stem, caching original stems; confirm.
- Preview: MP3 cache vs Opus/WAV; bitrate; cache invalidation; seeking behavior. Research 03 recommends MP3 192 kbps; NiceGUI's media route supports `Range: bytes=N-` but answers `HEAD` with 405 and suffix/multi ranges with 416; decide whether a HEAD route is needed.
- Export: WAV 24-bit, FLAC 24-bit, and MP3 320; metadata (title/artist/artwork) preservation; loudness normalization (none by default?); clipping/limiting policy. Research 03 recommends `-map_metadata 0`, no normalization by default, and a shared gain/limiter policy; settle cover art and clipping behavior.
- Temporary artifact naming and cleanup rules.

The answer must specify one policy the implementation can follow without further decisions.

## Answer

**One policy: every import is decoded once to stereo 44.1 kHz 32-bit float WAV; Separation reads only that original; Pitch reads the original or Stems; Instrumental is a stored float32 sum of the non-vocal stems; Preview is MP3 192; Export is WAV 24 / FLAC 24 / MP3 320 with record-driven tags and no normalization; a shared static presentation gain protects full scale and preserves stem balance.**

Full research: `../research/01-desktop-shell-and-audio-preview.md` §5, `../research/02-separation-engine.md` §3, `../research/03-pitch-shift-and-media-pipeline.md` §2, §9, §12.

### 1. Canonical audio and decode

- Canonical working audio is **stereo 44.1 kHz 32-bit float WAV (`pcm_f32le`)**, `-rf64 auto`, no dither, no clipping, no lossy step. FLAC and 16-bit are never intermediates.
- Every Source is decoded exactly once at import. The untouched native file is retained (ticket 07 owns retention); the canonical WAV is derived and never overwritten.
- Channels are normalized at import: **mono is duplicated to dual-mono; more than two channels is downmixed to stereo with FFmpeg's default matrix**. The UI states "multichannel source downmixed to stereo". Multichannel is not rejected. The native source is still kept, so nothing is lost from the session.
- Sample rate is normalized at import: 48/96 kHz sources are resampled to 44.1 kHz. An untouched export of a 48 kHz source is therefore 44.1 kHz; this is documented v0.1.0 behavior, not a bug.
- Decode to a `.partial.wav`, validate with ffprobe, then atomic rename:

```bash
# When the bundled FFmpeg has libsoxr (BtbN GPL builds)
ffmpeg -v error -y -i "$INPUT" -map 0:a:0 -vn -sn -dn \
  -af "aresample=resampler=soxr:precision=28" \
  -ac 2 -ar 44100 -c:a pcm_f32le -rf64 auto "$CANONICAL.partial.wav"

# Otherwise (gyan essentials on Windows): FFmpeg's default resampler
ffmpeg -v error -y -i "$INPUT" -map 0:a:0 -vn -sn -dn \
  -ac 2 -ar 44100 -c:a pcm_f32le -rf64 auto "$CANONICAL.partial.wav"
```

- Probe `ffmpeg -filters` once at startup for `soxr`; use it when present, otherwise use the default resampler. Both are high-quality for 48→44.1 kHz; tests assert format and duration, not bit-exact samples.
- If the source is already 44.1 kHz stereo, omit `-af`, `-ac`, and `-ar` and decode straight to `pcm_f32le`.
- A source with no audio stream fails the import as `no_audio_stream` (message and recovery owned by ticket 14).
- Validate the canonical file: `codec_name=pcm_f32le`, `sample_rate=44100`, `channels=2`, duration within 50 ms of the source, readable by ffprobe. A failed validation deletes the partial and fails the Job.

### 2. Pipeline graph and ordering

```text
Source (local file | YouTube)
  └─ Import Job: decode → canonical.wav + original.mp3
       ├─ Pitch(original, N)  → renders/<render_key>/original.wav + .mp3
       └─ Separation Job: canonical.wav
            → stems/{vocals,drums,bass,other}.wav + .mp3 each
            → stems/instrumental.wav + .mp3
                 └─ Pitch(stems, N) → renders/<render_key>/{artifact}.wav + .mp3
```

- **Separation always consumes the Track's original canonical audio**, never a pitch render.
- **Pitch renders from the original Track (before Separation) or from every Stem of a separated Track.** When Stems exist, one render action renders all four Stems with the same semitone value and derives the shifted Instrumental from them (§4).
- Pitch and Separation are independent artifacts. Rendering Pitch never invalidates Stems, and re-running Separation never deletes or invalidates pitch renders: they remain independent artifacts, consistent with the standing decision that re-separating does not require re-pitching.
- Re-running Separation replaces the Stems and Instrumental for that Track. A render whose manifest records input hashes that no longer match the current Stems is labelled "based on an earlier separation" in the UI, not discarded; the manifest is the source of truth for what it was derived from.
- A/B pairs an artifact with the immediate source recorded in its manifest: Track original ↔ shifted original; Stem ↔ shifted Stem; Instrumental ↔ shifted Instrumental.
- There is no pitch-before-separation path and no in-place modification of any master.

### 3. Separation outputs

- `htdemucs` produces four Stems: `vocals`, `drums`, `bass`, `other`, all stereo 44.1 kHz float32.
- **Instrumental is a fifth stored Artifact, not a Stem**: `instrumental.wav` is the float32 sum `drums + bass + other` (Demucs' documented "add" method). The separation worker computes and writes it in the same Job while the stems are in memory; it is not re-derived with FFmpeg.
- Vocals and Instrumental are the primary UI outputs; drums/bass/other are expandable.
- All five outputs get preview MP3s in the same Job and share one presentation gain (§5).
- The worker writes to a staging directory; the Job promotes it atomically (§8).

### 4. Pitch render

- One render quality in v0.1.0: **Rubber Band 4.0.0 CLI, R3 engine, offline, formant preservation ON, `--ignore-clipping`**, integer semitones `N ∈ [-12, +12]`. No draft mode, no formant toggle; both are deferred post-v0.1.0.
- Command (final render):

```bash
rubberband -3 -F -p "$N" --ignore-clipping "$IN.wav" "$OUT.partial.wav"
```

- The output is float32 WAV at the input's rate and channel count. Validate with ffprobe: readable, `pcm_f32le`, 44100 Hz, stereo, duration within 10 ms of the input. A mismatch deletes the partial and fails the Job rather than snapping to 100%.
- **Shifted Instrumental is the sum of the shifted non-vocal stems** (`amix=inputs=3:normalize=0` in float32, or an equivalent in-process sum), never a direct Rubber Band run on the Instrumental. This keeps the shifted Instrumental consistent with the shifted Stems for any later mixing.
- Render set identity: `render_key = BLAKE2b(track source_id | sorted input artifact hashes | semitone N | engine version | sample rate | channels | formant=on | window=default)`. A render set is everything one Render action produces: the original, or all four Stems plus the derived Instrumental. Changing N, the input Stems, or the engine version creates a new set; earlier sets stay cached until eviction.
- Progress contract: pass 1 "Studying" 2–45%, "Calculating profile" 45–50%, pass 2 "Processing" 50–98%, preview + validation 98–100%. Per-stem renders aggregate as `(completed + current) / total`. Parse the CLI's `\r`-delimited percentages; a missing final percentage is a failure. Ticket 06 owns the Job transport.
- Cancellation kills the process group, waits 5 s, then hard-kills; the Job's partials/staging are removed. Ticket 14 owns messages.

### 5. Gain and clipping

- Every stage that produces an artifact set (**Import**, **Separation**, **Pitch render**) measures the set's sample peak after it is complete. If the loudest sample exceeds 0 dBFS, the set records one **presentation gain** bringing it to **−1.0 dBFS**; otherwise `gain_db = 0`.
- One gain is shared by every artifact in the set. This preserves inter-stem and original/Instrumental balance. Per-stem normalization is forbidden.
- The gain is **metadata, not a rewrite**: `gain_db` goes in the manifest/sidecar, and preview/export encoders apply `-af volume=<gain_db>dB`. Master float32 WAVs keep their headroom and are never attenuated in place.
- No loudness normalization, no dynamic limiter, no ReplayGain/EBU R128 in v0.1.0.
- The UI shows a quiet "level adjusted −X dB" badge when `gain_db != 0`.
- A/B level matching is a preview concern for ticket 08: if its audio component exposes per-player volume, apply each side's recorded gain; exports are never level-matched.

### 6. Preview

- **MP3 192 kbps CBR, stereo 44.1 kHz, `-id3v2_version 3`**, from the master WAV, with `gain_db` applied. One preview per playable artifact, stored beside it. Tags: title and artist only; no cover art in previews.
- Preview is never generated from another MP3; it is always encoded from the float32 master.
- All previews in a session use identical encoder settings so A/B compares the same lossy path.

```bash
ffmpeg -v error -y -i "$MASTER.wav" -map 0:a:0 -map_metadata -1 \
  -af "volume=${GAIN_DB}dB" \
  -c:a libmp3lame -b:a 192k -ar 44100 -ac 2 -id3v2_version 3 \
  -metadata title="$TITLE" -metadata artist="$ARTIST" \
  "$ARTIFACT.partial.mp3"
```

- Previews are served from **one mounted media route** over the session cache (ticket 06 owns the mount and path confinement). Never `file://`, never one route per file.
- Supported HTTP behavior is **GET + `Range: bytes=N-`**. No HEAD route in v0.1.0: `<audio>` does not issue HEAD, and NiceGUI answers HEAD with 405. Suffix/multi-range requests answer 416. These are accepted limitations; re-check against real GTK/WebView2 during the UI prototype (ticket 08).
- No Opus/WAV preview and no direct preview of AAC/M4A; unsupported codecs are transcoded to the canonical pipeline first.
- Preview invalidation is content-derived: regenerate when the MP3 is missing or ffprobe-invalid. No mtime or time-based invalidation.

### 7. Export

- Formats and settings:

| Format | Encoder | Settings |
|---|---|---|
| WAV 24-bit | `pcm_s24le` | `-rf64 auto`; INFO tags only; no cover |
| FLAC 24-bit | `flac` | `-sample_fmt s32 -bits_per_raw_sample 24 -compression_level 8`; tags + cover |
| MP3 320 | `libmp3lame` | `-b:a 320k -id3v2_version 3`; tags + cover |

- Export always transcodes from the highest-fidelity master WAV with `gain_db` applied, never from a preview MP3.
- No 16-bit WAV, AAC, or Opus in v0.1.0; no resampling during export (masters are already 44.1 kHz).
- **Metadata is record-driven.** At import, read source tags with ffprobe into a canonical record (`title, artist, album, album_artist, track, disc, date, genre, comment, composer, copyright, source_url, source_id, cover_path, duration, sample_rate, channels`) and extract embedded cover art to `sources/<source_id>/cover.<ext>` when present. For YouTube, seed the record from yt-dlp and set `comment` to the source URL.
- At export use `-map_metadata -1`, then write every field explicitly with `-metadata key=value`; the record is authoritative and the canonical WAV is tagless. `-map_chapters -1`.
- **Cover art:** MP3 and FLAC embed `cover_path` as an attached picture when one exists; WAV gets no cover. YouTube thumbnails are **not** embedded in v0.1.0.
- Suggested filename: `Title.ext` for the unshifted original; `Title - Vocals.ext` / `Title - Instrumental.ext` etc. for unshifted outputs; `Title - Vocals +3st.ext` for shifted outputs. Sanitize Windows-reserved characters, reserved device names, trailing dots/spaces; cap the base name at 120 characters; the native save dialog suggestion stays editable and the extension is normalized (ticket 01).
- Export writes `<name>.partial.<ext>` in the chosen directory, validates with ffprobe, then `os.replace`. A failed export leaves no final file; ticket 14 owns the message.

### 8. Artifact identity, atomicity, cleanup, and persistence

- **Source identity:** `source_id = BLAKE2b-256(native file bytes)`, computed once at import. YouTube sources use `source_id = "yt-" + video_id` and keep the native-file hash as an integrity check.
- **Render identity:** `render_key` as in §4; the manifest records every input artifact hash, output hash, stage, engine version, `gain_db`, and validation result. Filenames are for humans and are never parsed to infer state.
- **Layout** (root, disk cap, and retention owned by ticket 07):

```text
sources/<source_id>/native.<ext>
sources/<source_id>/canonical.wav
sources/<source_id>/original.mp3
sources/<source_id>/cover.<ext>
sources/<source_id>/source.json
sources/<source_id>/stems/vocals.wav       + vocals.mp3
sources/<source_id>/stems/drums.wav        + drums.mp3
sources/<source_id>/stems/bass.wav         + bass.mp3
sources/<source_id>/stems/other.wav        + other.mp3
sources/<source_id>/stems/instrumental.wav + instrumental.mp3
sources/<source_id>/stems/manifest.json
renders/<render_key>/<artifact>.wav
renders/<render_key>/<artifact>.mp3
renders/<render_key>/manifest.json
```

- **Atomicity:** single-output writes use `<name>.partial.<ext>` in the target directory; multi-output Jobs write into `staging/<job_id>/` and are promoted with one atomic directory rename. The Job manifest is written last, before promotion, and is the commit marker: outputs of a multi-output Job are not visible to the UI until its manifest and directory are committed.
- **Cleanup:** cancel or failure deletes only that Job's partials/staging. Completed artifacts from earlier Jobs survive. Startup sweeps `*.partial*` and `staging/*` older than 24 hours (crash residue). **Superseded by ticket 07:** with the single-instance lock, startup deletes all staging and partials; the 24-hour rule applies only if concurrent instances are ever allowed.
- **Persistence:** completed canonical/stem/render artifacts persist across app restarts under the ticket-07 cache cap and LRU policy; the Workspace itself is ephemeral, so a restart starts empty but re-importing the same file reuses cached work.
- **Sharing and deletion:** cached artifacts are immutable and shared by identity. Removing a Track releases its references; a source subtree or render set is deleted only when no remaining Track references it (ticket 07 owns refcounting).

### 9. Accepted v0.1.0 limitations

- 48 kHz sources are downsampled to 44.1 kHz, including untouched exports; a native passthrough ("Save original") is post-v0.1.0.
- Multichannel sources are downmixed to stereo.
- Preview is MP3 192 with no HEAD route; suffix/multi-range requests return 416.
- No loudness normalization; A/B may differ in level when a presentation gain is active unless ticket 08's player supports volume matching.
- No draft pitch mode or formant toggle.
- No 16-bit WAV, AAC, or Opus export.
- No cover art in WAV or preview; no YouTube thumbnail embedding.
- Previews and exports inherit the source's tag set only through the canonical metadata record; WAV comment tags are not written.
