# 08 — UI workflow and layout prototype

**Type:** prototype
**Status:** resolved
**Blocked by:** 01, 06

## Question

Produce a rough, concrete UI prototype (wireframe or NiceGUI stub) of the single-window Workspace and the end-to-end flow:

- Empty state: drag-and-drop zone plus YouTube URL field.
- Track list and selection; per-track detail with transport, semitone stepper, render action, separate action, progress/cancel, and Artifact list.
- First-run model-download state: licence notice, size, progress, retry, offline failure, and a clear "model not installed" state before Separation.
- Vocals and Instrumental as primary outputs, with drums/bass/other expandable; per-Stem Preview and Export. Do not assume `ui.audio` can synchronize multiple stems; v1 uses per-stem players.
- Export flow through the native save dialog, plus Job progress and error states.
- Window title, icon treatment, and About/attribution entry point. Note: NiceGUI does not bridge window-close vetoes; design for checkpointing rather than a close prompt.
- Responsive behavior at laptop and desktop sizes.

Link the prototype artifact from this ticket. The answer must settle the layout and interaction model; visual polish can wait.

## Prototype

[08-ui-layout.html](../prototypes/08-ui-layout.html) — three structurally different layouts (A rail & detail, B transport & rack, C pipeline stages) over eight seeded states: empty/first run, YouTube import, loaded, separating, separated, rendered +3, model missing, separation failed. Open the file directly in a browser; no build or server.

Switch variants with the bottom bar or `←`/`→`; switch states with the select or `1`–`8`; `?` opens the design questions. `?variant=B&state=rendered` deep-links.

## Answer

**Decision: variant A — Rail & detail.** A single-window master–detail Workspace: a 292 px left rail (Add audio, Tracks, Recent, cache and Settings footer) and a main pane for the selected track — header with a Signal path strip, transport, Pitch and Separation cards, and one Outputs card whose version tabs switch between artifact sets. Exactly one preview player exists; Jobs report in a global bottom bar; the separation model is an inline gate inside the Separation card. B (transport + tile rack) and C (pipeline lanes) were prototyped and rejected: B's tile rack has no payload without a waveform or mixer, and C's staged wizard misrepresents pitch and separation as a one-way process.

Prototype (primary source, all three variants): [`../prototypes/08-ui-layout.html`](../prototypes/08-ui-layout.html). A is the default and carries the two borrowings from B (signal-path strip, transport-level Export); B and C remain as the exploration record.

### 1. Shell and geometry

- One window, native OS frame, no in-app title bar. Default 1280×820; **minimum 1024×700**; no mobile layout.
- Grid `292px | 1fr`; the rail drops to 260 px below 1150 px. The main pane scrolls; the rail and Job bar do not.
- Main pane padding 24–28 px; card radius 12 px; the Outputs card is the only full-width card.
- Pitch and Separation sit two-up at ≥1150 px, stacked below.
- The Job bar (44 px) is pinned to the bottom of the main pane, not the window, so the rail footer never moves.

### 2. Empty state

- Main pane, vertically centred, 560 px max: heading "Bring in a track", one-line explanation ("Everything runs on this machine"), dropzone, "or", YouTube row, disclaimer.
- Dropzone: "Drop audio files here · WAV, MP3, FLAC, M4A — or choose files…"; the whole zone triggers the same import action as a click. **The native "Choose files…" dialog is the reliable path**: research 01 leaves GTK drop-path fidelity open, so wire a drop handler only where pywebview yields real paths; if a drop yields no paths, keep the dialog path and toast. Never make dropping the only way in.
- YouTube: one URL field plus Import (disabled while any Job runs); Enter submits; starts `import_youtube`.
- Disclaimer under the field, 11 px: "Only import content you have the right to use. YouTube's Terms of Service prohibit downloading." Exact text from ticket 09; placement fixed here.
- Model not installed → a compact informational card follows: "Separation model not installed yet · 84 MB — you can load and preview now; Separation asks for the download the first time you use it." No download button here; the gate lives where Separation is invoked (§10).
- An import started here reports in the Job bar; the form stays visible but disabled until the Job ends.

### 3. Left rail

Order: brand block, primary "Add audio" button ("or drop files anywhere" hint), Tracks, Recent, footer.

- **Tracks**: one row per Track — 26 px type icon (music/video), title (one line, ellipsis), meta `duration · Local file|YouTube · status`, status dot. Click selects; hover/selected reveals a remove ✕.
- Status dot: grey = no stems, amber = separation running, green = stems cached, red = last separation failed. Status text mirrors the prototype ("4 stems", "Separating…", "Separation failed").
- **Recent sits below Tracks**: 3 visible rows plus "Show all" up to the stored 10. Each row: file/video icon, title, relative time; a missing entry is dimmed with "missing" and Remove-from-list. Clicking a valid entry opens or re-imports per ticket 07; nothing starts until the user acts.
- **Footer**: cache meter (`3.4 GB / 8 GiB` + bar), "Settings & About", version label.
- Remove confirms in a modal: "Remove <title> and delete its cached audio? … The file on disk is not touched." The confirmation must name the cache deletion (ticket 07 §3).

### 4. Track detail

Header: title (19 px), meta `Local file|YouTube · filename or URL · duration · 44.1 kHz stereo`, settings icon, trash icon.

- **Signal path strip** (from B): read-only chips — `Source (local/YouTube)` → `Separation (4 stems / not run)` → `Pitch (+3 rendered / not rendered)` — with check marks on completed steps. Status, not navigation; it makes separation-before-pitch legible.
- **Transport**: 42 px play/pause; now-playing `name · version`; elapsed/total; 5 px progress; A/B segmented control; secondary Export for the current listening target. Playback is independent of Jobs; Export is disabled while a Job runs.
- **Pitch** and **Separation** cards per §5–§6; **Outputs** per §7.

### 5. Pitch

- Integer semitones per Track, `−12…+12`, default 0; session state only (ticket 06 §5).
- The stepper is the **request**, not the view: changing it never switches the visible version set.
- Primary action:
  - `0` → disabled `Render`.
  - A set exists at N **for the current scope** (stems when stems are cached, otherwise master) → `Open +N` (selects that tab).
  - That set is already active → passive: "Viewing this render. Step semitones to render another set."
  - Otherwise → `Render +N`, disabled while a Job runs.
- A Render is a Job; it never modifies inputs and never feeds Separation. Scope: with stems cached it renders all four stems and the Instrumental from the shifted stems; without, it renders the original master. Each result is an immutable, content-addressed set (tickets 03/05).
- A **master render does not satisfy a stems render**: after Separation a track with only a master `+3` still offers `Render +3`, and both appear as tabs (`+3 master`, `+3 stems`).
- One set per (semitone, scope); the UI never offers a duplicate render, only the existing set.

### 6. Separation

Card states:

1. **Model missing** → inline model gate (§10) replaces the button.
2. **Not separated** → "Not separated · htdemucs · CPU", one-line explanation, primary `Separate vocals`.
3. **Running** → "Separating · stem 2 of 4", progress bar, "Runs in a worker process · safe to cancel", Cancel. Previously completed stems remain visible and playable throughout; job state is not artifact state.
4. **Done** → "4 stems ready · htdemucs · CPU", note that Vocals and Instrumental are primary, secondary `Separate again`.
5. **Failed** → inline error card: message, code (`separation.worker_crashed`), Retry, Open job log; if a previous successful run exists, add "Stems from the last successful Separation are still available."

No automatic Separation on load, ever. `Separate again` is always explicit. Retry re-submits the same spec; Cancel returns to the not-separated (or previously separated) state without a failure toast. Cancellation mechanics are ticket 06 §3.

### 7. Outputs, versions, A/B

- Header: `Outputs` plus version tabs on the right. Tabs exist only when a render exists; `Original` is first, renders follow in creation order, newest last.
- Tab label = signed semitones (`+3`, `−2`); when both scopes exist at one semitone, suffix `master` / `stems`. No timestamps, no "v2".
- Rows, in order: Original (`source`), Vocals, Instrumental, then Drums, Bass, Other behind `Show 3 more stems`. Vocals/Instrumental carry `primary`; Instrumental also `sum`; shifted rows carry their semitone badge.
- Each row: 27 px play/pause, name + badges, duration, export icon; ~40 px tall.
- Placeholders: with no stems, a dashed row "Vocals & Instrumental appear here after Separation" plus a separate button; while Separation runs, "…appear when Separation finishes" with no button. Never an empty card.
- **A/B** is a two-state segmented control (`Original` | latest render): a shortcut that switches the active version tab. B is disabled when no render exists. No mixing, no crossfade; switching resets playback position.
- The active version is per Track and client-local (`ui/page.py`); switching tracks restores that track's active version (default `Original`).

### 8. Playback

- **Exactly one player in the whole app.** One media element whose `src` is swapped to the chosen artifact's cached preview URL; selecting another row stops the current one, swaps the source, and starts at 0. No position carry-over, no simultaneous stems, no mixer.
- Per-artifact preview means any row can play; it does not mean concurrent playback. `ui.audio`'s single-track limitation is accepted, not worked around.
- Preview URLs come from the single cache-rooted `/media` route (ticket 06 §7); never `file://`.
- Playing state shows on the row (amber play button, faint tint) and in the transport (`name · version`).
- Do not preload every artifact; load on selection.

### 9. Export

- Entry points: the export icon on any Outputs row, and the transport Export for the current listening target. Both use the same flow.
- Flow: in-app **format sheet** → **native save dialog** → Job writes a temporary file at the destination and atomically renames.
- Format sheet: `WAV 24-bit` (default), `FLAC 24-bit`, `MP3 320`, each with a computed size estimate; prefilled filename `<track>-<artifact>[-<semi>]`; destination defaults to the preferred export directory from settings. The sheet is the authority on format.
- The native dialog opens with a matching filter (strict grammar, ticket 01) and default directory; the user may change directory and filename. Linux does not append extensions, so normalize: strip a known audio extension the user typed, append the chosen format's extension. Never switch format based on a typed extension.
- Export reads the cached master, never a preview. Tags/cover per ticket 05; no normalization.
- A failed export surfaces in the Job bar with the coded error; a partial file at the destination is the documented limitation (ticket 07 §7).
- No "Export all", no batch, no "Save original" in v1.

### 10. First-run model gate

- Lives where Separation is invoked: inside the Separation card. Never a modal or a blocking startup screen; load, preview and pitch work without the model.
- Content: "Separation model not installed" + `84 MB`; one line that htdemucs runs locally, the weights download once from Hugging Face and are verified by SHA-256; `Download model` primary; `Licence details` opens About (text from ticket 13); weight identity (`adefossez/HTDemucs · revision · bytes`).
- Downloading: progress with MB and percent, Cancel. Failed/offline: inline "Download failed — no connection. Check your network and retry." with `Retry download`.
- After a successful install, if the user had pressed Separate, **auto-submit that Separation once** (ticket 06 §3); otherwise return to the not-separated state.
- Verification failure before a run deletes the marker and shows the same gate with re-download (ticket 07 §4).

### 11. Jobs and errors

- One Job at a time. The Job bar shows kind, stage text, percent and Cancel; it exists only while a Job runs. No queue list, no toast-only progress, no modal progress.
- Stage text comes from the owning pipeline (`Loading model`, `stem 2 of 4`, `Shifting pitch · R3 + formant`, `Writing file`); no invented percentages.
- Failures move to the owning surface: Separation in the Separation card, import as a toast plus the rail/empty state, export in the Job bar then a toast. Each carries a code, a human message, and Retry where meaningful. `Open job log` reveals the per-Job log (ticket 07 §6).
- A failed or cancelled Job never deletes completed artifacts; a cancelled Job deletes only staging.
- Actions that would submit a second Job are disabled, not silently queued. A/B and playback stay enabled during Jobs.

### 12. Window chrome, identity, About

- Native OS frame. Title `StemForge — <Track title>` with a track selected, `StemForge` otherwise. No document-count suffix, no version in the title.
- Icon: amber circular waveform mark; the prototype's 4-bar glyph is the placeholder, final art is the installer icon set (ticket 10).
- `Settings & About` in the rail footer opens one modal:
  - **App**: version, GPL-3.0, link to `THIRD_PARTY_NOTICES`, corresponding-source offer (ticket 13).
  - **Storage**: cache meter, `Clear cached audio`, `Clear recent files`, `Open log folder` (ticket 07).
  - **Behaviour**: "Closing the window stops the active job and clears the session track list. Cached audio and recent files stay, so re-importing is instant. There is no close prompt."
- **No close veto, no close prompt.** Every completed artifact is already committed, settings save on change, and an in-flight Job is cancelled on shutdown (ticket 06 §3). Checkpointing is the artifact store, not a dialog.
- No telemetry, no sign-in, no update check in the UI.

### 13. Responsive behaviour

- ≥1150 px: two-up cards, 292 px rail, full table.
- 1024–1150 px: cards stack, rail 260 px, transport keeps A/B and Export on one line (now-playing truncates first).
- Below 1024 px is unsupported; the native window enforces 1024×700. No hamburger, no rail collapse, no mobile layout.
- At 1024×700 the Outputs card scrolls with the main pane; transport and Job bar stay visible.

### 14. Component map

Matches ticket 06's tree; no new modules.

| UI piece | Module |
|---|---|
| page shell, 400 ms poll, selection/version/A-B client state | `ui/page.py` |
| empty state, dropzone, YouTube field | `ui/components/empty_state.py` |
| rail, track rows, recents, cache meter | `ui/components/track_list.py` |
| header, signal path, transport, Outputs list | `ui/components/track_detail.py` |
| Pitch card + version tabs | `ui/components/pitch_panel.py` |
| Separation card + progress + inline error | `ui/components/separation_panel.py` |
| format sheet + native save wrapper | `ui/components/export_dialog.py` + `integration/dialogs.py` |
| Job bar | `ui/components/job_indicator.py` |
| model gate | `ui/components/first_run.py` |
| About/settings modal | `ui/components/about.py` |

All mutations go through `WorkspaceService`; the UI never touches engines, artifacts or files. The prototype's debug seam (`window.__stemforgePrototype`) is not part of the design.

### 15. What implementation must not add

- No waveform, stem mixer, volume/mute/solo, batch export, project library, keyboard shortcut set, themes, or drag-to-reorder.
- No synchronized multi-stem playback, seek-synced comparison, or crossfade A/B.
- No per-artifact volume or gain UI (presentation gain is automatic, ticket 05).
- No nested dialogs beyond the format sheet, confirmations and About; no modal onboarding tour.

**Acceptance for this ticket's question:** the page can be built from this answer without choosing a layout, playback model, version model, export flow, or first-run shape. Visual polish (spacing, exact palette, icon art) remains open.

## Comments

**2026-11-15 — additions from ticket 09.** The empty-state disclaimer (§2) uses 09's exact copy: *"Only import content you have the right to use. YouTube's Terms of Service prohibit downloading."* Settings & About (§12) gains two rows: **Behaviour → YouTube import** (default on; off hides the empty-state YouTube row and dims recent YouTube entries) and **Storage → Clear YouTube cache** (deletes `<cache>/yt-dlp/` only). The About modal gains one short "YouTube import" paragraph. The first YouTube import opens a one-time consent modal (09 §7); no other layout changes.

**2026-11-15 — progress copy correction from ticket 12.** Demucs does not report per-stem completion and Separation now runs in 8-minute blocks, so §6's running state should say `Separating · 45%` (percentage from block/segment progress), not `stem 2 of 4`. The progress bar, worker-process note, and Cancel behavior stand.

**2026-11-15 — failure-surface amendments from ticket 14.** The Pitch card gets the same inline error treatment as the Separation card (§6 state 5). A banner region above the main pane carries degraded mode (persistent), resource warnings, and the one-time crash-recovery notice; at most two stack, degraded first. About → Storage gains `Copy diagnostics`, and About → Behaviour adds the force-quit export note ("a file ending in `.partial` may be left beside the destination"). The playback row can enter an `unavailable` state with the repair action for its artifact kind. See [14](14-failure-modes-and-recovery.md).
