# Ticket 18 — Export UI and native save dialog verification

**Date:** 2026-11-17
**Host:** Linux development container without GTK 3/WebKitGTK 4.1 (`gi` is not
importable) and without a display. Native pywebview dialogs cannot be shown
here, and there is no Windows host. The headless suite injects the save-dialog
backend; the real Linux/Windows dialog matrix is listed below for a qualified
host.

## Verified headlessly

- `tests/ui/test_export_flow.py` (12 passed, NiceGUI user simulation):
  - the sheet lists WAV 24-bit (default), FLAC 24-bit, and MP3 320 with
    per-minute size estimates, plus a read-only Source row for the artifact the
    entry point selected;
  - the sheet has no source picker: an Outputs row's export icon and the
    transport Export fix the target before the sheet opens (ticket 08 §9);
  - there is no "Save original", no "Export all", no batch control, and exactly
    one Export confirmation per sheet;
  - changing format updates the seeded filename and sends the matching filter
    and default name to the save seam;
  - a typed known audio extension that differs from the chosen format is
    replaced; the chosen format never changes. Evidence: WAV chosen, dialog
    returns `Chosen Mix.flac`, the committed file is `Chosen Mix.wav`, the Job
    result format is `wav24`, and the sheet shows
    `Extension changed to .wav for WAV 24-bit.` both as an immediate toast and
    in the success bar;
  - a success bar shows the full committed path, the preferred export folder is
    persisted for the next sheet, and the Job bar shows the one Export Job;
  - cancelling the native dialog starts no Job, leaves the sheet usable, and
    re-enables Export;
  - the transport Export uses the current listening target (a render tab's
    first row selects that render member), an Outputs-row Export opens the
    sheet on that row's target, and a stem row's export icon opens the sheet on
    that stem;
  - playback, seek, and A/B stay enabled during an Export Job; only second-Job
    actions (Add audio, Export) disable (ticket 08 §11);
  - failures render in the Job-bar area with the coded error and Retry, keeping
    the chosen format/source. Retry re-submits without reopening the sheet or
    save dialog: `export.write_failed` (permission and other),
    `media.encode_failed`, `media.validation_failed`, and
    `storage.disk_full` ("Choose another location.").
- `tests/unit/test_save_dialog.py` (27 passed): the pywebview 6.2.1 filter
  grammar for all three formats, filter lookup, default-name sanitisation
  (illegal characters, control characters, `CON`, trailing dots/spaces, 120/255
  caps), known-audio extension replacement, matching-extension case
  normalisation, missing-extension append, unknown-dot-name preservation,
  trusted absolute paths only, fake `C:\fakepath`/`file://`/relative rejection,
  missing-directory omission, and cancel.
- `tests/unit/test_export_names.py` and `tests/unit/test_export_service.py`
  cover the dialog-chosen base name, Windows-safe sanitisation, the Job
  identity carrying the chosen name, and `preview_export` unchanged.
- `tests/integration/test_export_job.py` (15 passed, real FFmpeg) additionally
  commits a dialog-chosen name (`My Mix: <final>?.WAV` →
  `My Mix_ _final__.wav`) and keeps the existing round-trip, collision,
  destination-failure, and cleanup assertions.

### Failure cleanup

Every handled failure below was exercised through the real Export Job in
`tests/ui/test_export_flow.py`. Each asserts the destination directory is empty
(no final file, no `*.partial`) and, where staging is created, that
`ArtifactStore.staging_dir` is empty.

| Trigger | Code / reason | Inline Retry | Destination after |
|---|---|---|---|
| final-rename `PermissionError` | `export.write_failed · permission` | yes | empty |
| final-rename `EIO` | `export.write_failed · other` | yes | empty (integration) |
| encoder raises | `media.encode_failed` | yes | empty |
| tag validation mismatch | `media.validation_failed` | yes | empty |
| destination free space under the WAV-24 worst case | `storage.disk_full` | yes | empty, nothing written |

## Native save dialog and filter table

pywebview validates the filter before opening the dialog. Descriptions must be
`[\w ]+` (no hyphen, comma, or slash); patterns are `*.ext`, with no space after
`;` (research 01 §4.2). These are the exact strings in
`integration/dialogs.py`:

| Format | Suffix | Filter (`file_types` tuple) | Description grammar | Linux GTK3 | Windows WinForms |
|---|---|---|---|---|---|
| WAV 24-bit | `.wav` | `WAV 24 bit (*.wav)` | `WAV 24 bit ` + `*.wav` | returns typed name; app appends/corrects `.wav` | `AddExtension` uses the selected filter; app corrects anyway |
| FLAC 24-bit | `.flac` | `FLAC 24 bit (*.flac)` | `FLAC 24 bit ` + `*.flac` | returns typed name; app appends/corrects `.flac` | same |
| MP3 320 | `.mp3` | `MP3 320 kbps (*.mp3)` | `MP3 320 kbps ` + `*.mp3` | returns typed name; app appends/corrects `.mp3` | same |

The dialog is called as
`create_file_dialog(dialog_type=webview.FileDialog.SAVE, directory=…,
save_filename=…, file_types=(filter,))`. The wrapper awaits it directly; it does
not add another `run.io_bound`, matching the open dialog and ticket 01's
threading note. A preferred export directory is sent only when it still exists;
otherwise pywebview's system default is used. The button area is disabled while
the dialog is open.

## Manual matrix for a real Linux and Windows host

1. **Linux (GTK 3 + WebKitGTK 4.1):** load and separate a track, choose an
   Outputs-row Export. Confirm the sheet, then confirm the GTK save dialog opens
   with the computed default name, the preferred folder, and only the chosen
   format in the filter list.
2. **Linux extension repair:** delete the extension, save; confirm the file is
   written with the selected extension. Repeat, typing `.flac` while WAV is
   selected; confirm the toast, the `.wav` file, and that WAV stayed selected.
   Repeat with `.m4a` and no extension.
3. **Windows 10/11 + WebView2:** repeat step 1. Confirm the WinForms dialog
   opens on the GUI thread, the default name is selected, the filter shows the
   chosen format, and the native overwrite prompt appears when an existing file
   is chosen. Confirm Windows' `AddExtension` plus the app's correction never
   produces a double extension.
4. **Cancel:** cancel the native dialog; confirm no Job appears in the Job bar
   and no file is created.
5. **Failure:** choose a read-only folder on Linux and a protected folder or an
   open-in-another-app file on Windows; confirm `export.write_failed` with the
   permission/in-use action and Retry. Confirm no final file and no `.partial`.
6. **Disk full:** choose a nearly full removable volume; confirm
   `storage.disk_full` with "Choose another location" and Retry. Nothing is
   written.
7. **Long/reserved names:** type `CON.wav` and a very long name; confirm the
   committed names are `_CON.wav` and a ≤255-byte safe name.
8. **Success:** confirm the success bar shows the full path and the folder is
   remembered for the next sheet. Confirm playback/A-B remain usable while the
   Export Job runs and that Export/Add audio disable.
9. **Close during Export:** close the window mid-export; confirm the partial is
   removed on clean close and only a hard kill can leave `<name>.partial`
   (ticket 14 §4.6/§7).

## Gates

- `uv run ruff format --check .` — green.
- `uv run ruff check .` — green.
- `uv run mypy src/stemforge` — green.
- `uv run lint-imports` — 2 contracts kept; `ui` still imports no engines,
  workers, pipeline, or jobs.
- `uv run pytest -m "unit or ui"` — 691 passed.
- `uv run pytest -m "integration and not slow"` — 128 passed.
- `uv run python -m stemforge --self-test` — exit 0.
- `uv run python -m stemforge --smoke-test` — exit 0, HTTP 200 on `/`.

No unit or UI test spawns FFmpeg, Rubber Band, Demucs, or a real network
connection: the UI media adapter writes stand-ins under `tmp_path`, the
save-dialog backend is scripted, and no socket guard is patched globally.

## Deviations and notes

- **Chosen filename support:** ticket 17's `run_export_job`/`submit_export`
  accepted only a destination directory, so the save dialog's filename would
  have been discarded. They now take an optional `destination_name`; the
  existing directory-only behaviour and all ticket 17 tests are unchanged.
  The name is sanitised again in the Job (defence in depth) and always carries
  the selected format's suffix.
- **Collision handling:** the Job keeps ticket 17's collision-safe commit
  (`Song.wav`, `Song (2).wav`, …) and never overwrites. A native overwrite
  prompt therefore cannot be carried into the Job through the ticket 17 seam;
  the success bar reports the actual committed path. This is recorded rather
  than silently changing the established export-engine contract.
- **No Reveal action:** ticket 08 §9 specifies the success destination but no
  Reveal/open-folder action, so none was added. The full path is shown in the
  success bar.
- **Directory memory:** ticket 01 does not specify last-directory memory. The
  sheet seeds from `SettingsStore.preferred_export_dir` and records the
  successful destination's folder there; it does not keep a separate
  dialog-last-folder cache.
- **Retry flags:** the Export Job marks `media.encode_failed` and
  `media.validation_failed` retryable (ticket 14 §4.6) without changing the
  shared media adapter's flag elsewhere. `storage.disk_full` uses the reason key
  `export` so its action is "Choose another location." while imports keep
  "Clear cached audio".
- **Target selection:** ticket 08 §9 makes the entry point authoritative. The
  sheet shows the selected artifact as read-only Source; it never re-lists
  `WorkspaceService.export_targets`. The master target is labelled `Original`
  (the ticket 05 label); it is a transcoded export, not a passthrough, so there
  is no "Save original".
- **Manual native verification is deferred** to a qualified Linux/Windows host
  as listed above; this container has no GTK/WebKitGTK or display.
