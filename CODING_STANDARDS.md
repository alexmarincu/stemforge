# Coding standards

Judgement calls only. Formatting, typing, and import direction are enforced by
`pyproject.toml`; domain decisions live in `GLOSSARY.md` and `docs/adr/`.

## Copy

- The English catalogue at `src/stemforge/i18n/catalogs/en.json` is the only
  source of user-visible copy. Use flat semantic message IDs and named
  placeholders; the page shell, components, preflight dialog chrome, and fatal
  message resolve keys at render time with `i18n.t`. No inline prose and no
  parallel English constant table.
- `src/stemforge/i18n/catalogs/ro.json` translates every English key with the
  same named placeholders and the Romanian plural shape. English is the source
  locale and the per-key fallback.
- `src/stemforge/ui/formatting.py` owns formatting helpers, the `(code, reason)`
  to catalogue-key mapping, sentence composition, and display-key realisation; it
  owns no prose. Views carry a stable `LabelRef` (`key` plus `context`), never a
  pre-rendered label. Job snapshots carry `CodedText` (`text`, `code`, `context`),
  so a stage or detail stays plain text for logs while the UI resolves its code.
- `tests/unit/test_copy_policy.py` fails on a prose literal at a copy sink, on
  an `i18n.t` key missing from either catalogue, on a formatting copy constant, on
  a glossary synonym, on a cedilla diacritic, or on an avoided Romanian
  rendering from `docs/i18n/ro/terms.md`. It covers the page shell, components,
  preflight chrome, and the `__main__` fatal and already-running copy.
  `tests/unit/test_romanian_catalog.py` fails on a missing or orphaned key,
  placeholder drift, or a wrong plural shape.
- The Interface language setting is `system`/`en`/`ro`; an unknown persisted
  value falls back to `system` without corrupt-setting recovery.
- Logs, the diagnostics report body, preflight report messages, CLI output
  (including `--preflight`), exported filenames and audio tags, native
  file-dialog filter descriptions, and licence documents stay English. Preflight
  and fatal dialog chrome is translated. `pipeline/export_names.ROLE_LABELS` stays English so a Romanian
  interface still writes `Song - Vocals.flac`.
- `GLOSSARY.md` is normative. Use **Import**, never Load, Add, or Ingest.
- An Instrumental is not a Stem. `OUTPUT_ROLES` includes it; `STEM_ROLES`
  does not.

## Tests

- Assert the module that owns the value, not a re-export. For token values,
  read `theme.TOKENS_CSS`, not `page.SHELL_CSS`.
- Every ADR or glossary invariant you touch gets a regression test.

## Docs

- Commands, paths, and option names match the source and packaging docs.
- Prose uses British spelling.

## Comments

Follow `AGENTS.md` ("Code conventions"), ADR 0014, and
`tests/unit/test_comment_policy.py`; this file does not restate the policy.
