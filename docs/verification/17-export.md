# Ticket 17 — Export engine verification

Measured on the ticket/17 worktree, Linux x86_64, Python 3.13.18, system
FFmpeg/ffprobe `n9.0.2`. The repository commits no audio fixtures: every file
below was generated at test time, and every cache write stayed under a
`STEMFORGE_HOME` in `tmp_path`.

## Round trip

Source: stereo 44.1 kHz float32 WAV, 2.000 s, 440 Hz sine at a −6.02 dBFS sample
peak, import record with all eleven tag fields and a generated 64×64 PNG cover.
Export target: `master` (`sources/<source_id>/canonical.wav`). Presentation gain
0.0 dB.

| Format | Codec | Depth / rate | Duration | Δ duration | Cover | Tags checked | Peak in | Peak out | Δ peak | Gain recorded |
|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|
| WAV 24 | `pcm_s24le` | 24-bit PCM | 2.000 s | 0.0 ms | no (correct) | 8 INFO fields | −6.02 dBFS | −6.02 dBFS | 0.00 dB | 0.0 dB |
| FLAC 24 | `flac` | 24-bit, `s32` raw | 2.000 s | 0.0 ms | yes | 11 | −6.02 dBFS | −6.02 dBFS | 0.00 dB | 0.0 dB |
| MP3 320 | `mp3` (`libmp3lame`) | 320 kbps CBR | 2.000 s | 0.0 ms | yes | 11 | −6.02 dBFS | −6.02 dBFS | 0.00 dB | 0.0 dB |

Tags checked: WAV `title`, `artist`, `album`, `track`, `date`, `genre`,
`comment`, `copyright`; MP3/FLAC the same plus `album_artist`, `disc`, and
`composer`. No expected tag was missing in any format. File sizes for this
2-second fixture: WAV 529,520 B; FLAC 114,590 B; MP3 83,079 B.

Assertion tolerances: duration ±50 ms, peak ±0.1 dB, bit depth 24, MP3 bitrate
320,000 bit/s. The integration suite checks the same properties through
`tests/integration/test_export_job.py`.

## Shared gain, never normalization

The same quiet source exported with no gain stayed at −6.02 dBFS (not lifted
toward full scale). A deliberately hot source (sample peak +6.02 dBFS) carried a
recorded set gain of −7.0 dB:

| Export | Set gain | Source peak | Output peak | Measured Δ | Recorded `gain_db` |
|---|---:|---:|---:|---:|---:|
| Master WAV 24 | −7.0 dB | +6.02 dBFS | −0.98 dBFS | −7.00 dB | −7.0 |
| Master WAV 24 (quiet) | 0.0 dB | −6.02 dBFS | −6.02 dBFS | 0.00 dB | 0.0 |
| Pitch +3st FLAC 24 | −1.0 dB | −6.02 dBFS | −7.02 dBFS | −1.00 dB | −1.0 |
| Instrumental MP3 320 | −2.0 dB | −6.02 dBFS | −8.02 dBFS | −2.00 dB | −2.0 |

The Job never measures a peak and never derives a gain of its own: it reads the
chosen set's committed `gain_db` and passes it once to the encoder. The export
manifest records `"gain_db"` and `"normalization": "none"`.

## Target enumeration

For a source with a committed separation and two render sets, the service listed:

```text
master
stem:vocals, stem:drums, stem:bass, stem:other
instrumental
render:verify-render:original
```

Stem and Instrumental targets carry the separation set's gain; render members
carry the render set's gain and semitone value. A deleted file or invalid
manifest removes that target instead of offering a failing action.

## Failure and filename checks

Covered by `tests/integration/test_export_job.py` with real FFmpeg:

- Injected final-rename failure (`EIO`): `export.write_failed` (`reason=other`),
  the destination directory is empty, the `.partial` is gone, and the staging
  directory is removed.
- Full destination: `storage.disk_full`, retryable, before any destination file
  is created. The estimate is the ticket 07 WAV-24 worst case (1.2 headroom +
  64 MiB) with `floor=0`, so a small export can use a small free space.
- Read-only destination (POSIX, non-root): `export.write_failed` with
  `reason=permission`.
- Tag verification failure: `media.validation_failed` with `detail="tags"`, no
  final file, no staging residue.
- Reserved name `CON` → `_CON.wav`; `track: <bad> / name? * .` →
  `track_ _bad_ _ name_ _.wav`; a 300-character title is capped at 120 base
  characters and the final UTF-8 filename is ≤255 bytes.
- Repeating the same export against one directory produces `Song.wav`,
  `Song (2).wav`, `Song (3).wav`; the first file's bytes are unchanged.

Unit coverage in `tests/unit/test_export_names.py` adds every illegal character,
control characters, DEL, trailing dots/spaces, all `CON`/`PRN`/`AUX`/`NUL`/
`COM1-9`/`LPT1-9` roots, multibyte truncation, collision numbering, and the
space estimate. `tests/unit/test_export_service.py` covers target enumeration,
submission identity, foreign-target refusal, the missing-media guard,
`preview_export`, and `export_result`.

## Manual checks and limits

- Native save dialog, format sheet, and the toast/Retry surface belong to #18 and
  were not implemented here; the service seams they call are
  `export_targets`, `preview_export`, `submit_export`, and `export_result`.
- Windows filesystem behaviour was exercised only through the pure naming rules
  and the injected OSError mapping on Linux; the Windows OS matrix remains a
  release/hardening check.
- FFmpeg here is the system build (`n9.0.2`). The vendored Bundle build is used
  by the smoke/frozen gates, not by this worktree's integration layer.
- Decision-record note: ticket 05 §9 says "WAV comment tags are not written",
  while FFmpeg's WAV muxer writes the `comment` field as RIFF `ICMT` and the
  pre-existing media export test (and this Job's WAV tag verification) expects
  it. The implementation follows the existing adapter and test; the §9 sentence
  is recorded as unresolved rather than silently changing the media adapter.
