# Coding standards

Judgement calls only. Formatting, typing, and import direction are enforced by
`pyproject.toml`; domain decisions live in `GLOSSARY.md` and `docs/adr/`.

## Copy

- User-facing strings resolve through the English catalog at
  `src/stemforge/i18n/catalogs/en.json`, using flat semantic message IDs and
  named placeholders. The page shell renders catalog keys with `i18n.t`;
  components keep the legacy constants in `src/stemforge/ui/formatting.py` until
  their copy migration lands. The UI copy module keeps formatting, not prose.
- `tests/unit/test_copy_policy.py` fails on a prose literal at a page-shell copy
  sink, on an `i18n.t` key missing from `en.json`, and on an avoided Import
  synonym in the English catalog.
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
