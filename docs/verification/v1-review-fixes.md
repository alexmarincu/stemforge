# v1 review — functional fixes

Fix branch `fix/v1-review-functional`, based on `integration/v1` (`248e15d`).
Commit `1fcc6e8` closes the functional findings from the final two-axis review;
this document records each finding, its fix, and the tests that pin it.

The normative sources are ticket 07 §2 and §8, ticket 05 §1 and §9, ticket 08 §2,
ticket 14 §9–§10, and ticket 11 §6. The accepted-debt list at the end records
structural smells the review allowed to remain.

## 1. The cache cap was dead code

**Finding.** `ArtifactStore.enforce_cap` was called only from tests. Startup ran
only `sweep_stale()`, and no Job reserved cache room, so the 8 GiB cap was never
enforced, `storage.cache_full` was unreachable, and the footer meter could exceed
100%.

**Fix.** `enforce_cap(needed_bytes: int = 0)` now plans eviction against
`usage + needed_bytes <= cap` (ticket 07 §2), evicting whole non-live source
subtrees and render sets oldest-first. Live Tracks remain protected by the
existing retained-key set and live-keys callback. `staging/` and
`<data>/models` are not cache entries, so they are never eviction candidates.
`storage.cache_full` is raised when even the remaining live sets cannot admit the
reservation.

Wiring:

- `WorkspaceService.enforce_cache_cap()` delegates the startup pass.
- `__main__._run_ui` calls it after `service.sweep_stale()` and logs-and-continues
  on failure, because nothing is live at startup.
- Import, YouTube, Separation, and Pitch call it with the ticket-07 estimate
  before staging. Export writes to the destination volume and model install lives
  under `<data>/models`, so neither reserves cache room.
- The footer percentage is computed by `formatting.cache_percent`, which clamps
  to 100.

**Tests.**

- `tests/unit/test_cache_policy.py::test_reserved_room_is_evicted_even_when_usage_is_under_the_cap`
- `tests/unit/test_cache_policy.py::test_reserved_room_with_everything_live_reports_over_cap`
- `tests/unit/test_artifact_store.py::test_enforce_cap_evicts_room_for_the_next_job_estimate`
- `tests/unit/test_artifact_store.py::test_cache_full_counts_the_reservation_against_live_sets`
- `tests/integration/test_import_job.py::test_an_import_reserves_room_evicts_a_stale_set_and_protects_the_live_track`
- `tests/unit/test_run_ui.py::test_run_ui_enforces_the_cache_cap_after_the_sweep`
- `tests/unit/test_run_ui.py::test_run_ui_survives_a_cache_cap_failure`
- `tests/unit/test_ui_formatting.py::test_cache_meter_never_exceeds_a_full_bar`

The integration test exercises the real store and FFmpeg: the stale set is
evicted, the live Track survives, and a second import fails with
`storage.cache_full` rather than evicting the live source.

## 2. Pitch render had no disk-space gate

**Finding.** `estimate_render_bytes` had no production caller, and `run_pitch_job`
shifted immediately, so a full volume surfaced as `pitch.render_failed`.

**Fix.** Before staging, `run_pitch_job` probes the canonical source for its
duration, computes `estimate_render_bytes(duration, outputs=1)` for a master or
`outputs=5` for the four shifted Stems plus Instrumental, calls
`require_free_space(artifacts.root, free_space_requirement(estimate))`, and
reserves the same estimate with `enforce_cap`. The check happens before any
staging directory exists.

**Test.** `tests/unit/test_pitch_job.py::test_a_full_cache_volume_fails_before_any_render_is_staged`
forces a low `shutil.disk_usage`, asserts `storage.disk_full`, and checks that no
render set, staging directory, or Track mutation was committed.

## 3. The downmix note was missing

**Finding.** `DecodeResult.downmixed` was computed but dropped. Local imports
recorded only the decoded two channels, and the UI never said that a
multichannel source was downmixed.

**Fix.** The source manifest records `downmixed` for local and YouTube imports
(including the cached-native YouTube repair). `Source` carries the flag, and old
manifests read as `false` because the field is absent. The track's Source facts
render the exact note **“Multichannel source downmixed to stereo.”** when the
flag is true. Mono dual-mono and stereo imports leave it false and render
nothing.

**Tests.**

- `tests/integration/test_import_job.py::test_a_six_channel_source_records_the_downmix_and_keeps_stereo_canonical`
- `tests/integration/test_youtube_job.py::test_a_six_channel_youtube_source_records_the_downmix`
- `tests/ui/test_load_flow.py::test_a_multichannel_import_shows_the_downmix_note`
- The stereo side is pinned by
  `tests/ui/test_load_flow.py::test_a_selected_file_becomes_a_track_with_audio_facts`
  (no note) and the `downmixed is False` assertions in both import integration
  tests.

## 4. The empty-state model card was missing

**Finding.** Ticket 08 §2 asks for a compact informational card when the model is
absent. The empty state had no such card.

**Fix.** `formatting.EMPTY_MODEL_CARD` holds the exact copy:

> Separation model not installed yet · 84 MB — you can load and preview now;
> Separation asks for the download the first time you use it.

The card is rendered with the stable marker `empty-model-card` only when
`model_status().installed` is false. It has no download button; the gate remains
inside the Separation card.

**Tests.** `tests/ui/test_load_flow.py::test_empty_state_explains_the_way_in`
asserts the marker and the exact copy; `test_the_empty_state_omits_the_model_card_once_installed`
asserts the card is absent when the model is installed.

## 5. `--install-model` ignored interactive consent

**Finding.** After the user typed “yes”, `_run_install_model` still passed
`accept_licence=args.accept_model_licence`, which was false without the flag, so
`install_model` re-raised `separation.model_missing`.

**Fix.** The command keeps an `accepted` flag initialised from
`--accept-model-licence`, sets it when the interactive answer is `yes`/`y`, and
passes it to `service.install_model`. That is the same path the non-interactive
flag uses, so `SettingsStore.accept_model_licence` persists the consent record
with the pinned notice version.

**Tests.**

- `tests/unit/test_model_cli.py::test_install_model_interactive_yes_records_consent_and_installs_once`
  patches `input` to return `yes`, asserts exactly one install attempt, no
  `model_missing`, and a persisted acknowledgment.
- `tests/unit/test_model_cli.py::test_install_model_interactive_no_declines_without_installing`
  asserts “no” declines without a Job and without consent.

## 6. Standards hard violations

- **Instrumental is not a Stem.** `application._STEM_LABELS` became
  `_SEPARATION_ROLE_LABELS`, with a comment tying the name to the GLOSSARY
  distinction. Existing Separation-preview tests cover the labels.
- **ADR 0004 accessor.** `ArtifactStore.read_source_manifest(source_id)` returns
  a validated `SourceManifest` even when derived files are missing;
  `_read_manifest`/`_parse_manifest_file` gained a `require_files=False` mode
  that still validates schema, identity, timestamps, and path confinement.
  `youtube_job._recover_from_native` now uses the accessor and no longer parses
  `source.json` or walks `manifest["files"]`. Recovery behaviour is unchanged:
  a hash mismatch deletes the subtree and returns `None`; a decode failure still
  returns `None`.
  Tests: `test_read_source_manifest_survives_missing_derived_files`,
  `test_read_source_manifest_refuses_unknown_ids_and_escaping_paths`, and
  `test_a_native_hash_mismatch_deletes_the_broken_cache_entry`.
- **One `_reason` helper.** `ui/formatting.reason_from_detail` is the single
  implementation; the copies in `page.py`, `pitch.py`, `separation.py`,
  `export.py`, and `youtube.py` were deleted. Test:
  `test_reason_from_detail_keeps_the_stable_classifier`.
- **One loudest-peak implementation.** `pipeline/gain.max_peak_dbfs` raises on
  NaN and returns `None` for an empty or all-`-inf` set;
  `presentation_gain`, `separation_job`, and `pitch_job` all use it. Tests:
  `test_max_peak_returns_none_for_missing_or_silent_sets` and
  `test_max_peak_rejects_a_nan_instead_of_skipping_it`.

## Accepted debt

These structural smells were called out by the review and deliberately left
unrefactored:

- **`SeparationSet` dead surface.** The pipeline return type carries masters and
  previews that the service does not consume. Removing it would widen a fix
  branch into a pipeline/UI contract change.
- **`engine_block_seconds(engine: object)` getattr fallbacks.** The engine
  Protocol does not expose the block-plan attributes; test fakes rely on the
  defaults. Typing the Protocol is a design change, not a behaviour fix.
- **Stringly-typed preview/export identities.** Keys such as `stem:vocals` and
  `render:<key>:<role>` cross the application/Job boundary and are already
  covered by export and preview tests. A typed identity model is a refactor.
- **Duplicated `_file_entry` / `_tail` / `_as_*` helpers.** Each copy is local to
  its pipeline module and keeps the dependency graph flat; consolidation is
  mechanical and would touch unrelated modules.
- **Three role-label maps.** `_SEPARATION_ROLE_LABELS`, `formatting.SEPARATION_ROLE_LABELS`,
  and `export_names.ROLE_LABELS` serve the service, UI copy, and export filename
  suggestion respectively. They currently agree; unifying them would couple UI
  prose to export naming.

## Gates

Run from the worktree after the fix commit (and re-run after merging
`origin/integration/v1`):

- `uv run ruff format --check .` — pass
- `uv run ruff check .` — pass
- `uv run mypy src/stemforge` — pass
- `uv run lint-imports` — 2 contracts kept
- `uv run pytest -m "unit or ui"` — pass; unit-only wall measured at ~44 s
  (ticket 11 §6 requires < 60 s)
- `uv run pytest -m "integration and not slow"` — 131 passed
- `uv run pytest -m "integration and slow"` — 9 passed after linking the
  verified vendor tree (`scripts/vendor-fetch.sh --verify-only`)
- `uv run pytest -m integration` — 140 passed with that vendor tree
- `scripts/check.sh` — pass (140 integration tests passed with the vendor tree)
- `uv run python scripts/generate_notices.py --check` — 201 files match
- `STEMFORGE_HOME=<tmp> uv run stemforge --self-test` — ok
- `STEMFORGE_HOME=<tmp> uv run stemforge --smoke-test` — HTTP 200

No `.github/workflows/*`, `tests/unit/test_release_pipeline.py`, licensing, or
generated files were touched.
