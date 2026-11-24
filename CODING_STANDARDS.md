# Coding standards

Judgement calls only. Formatting, typing, and import direction are enforced by
`pyproject.toml`; domain decisions live in `GLOSSARY.md` and `docs/adr/`.

## Copy

- User-facing strings live in `src/stemforge/ui/formatting.py`; pages and
  components render constants or f-strings built from them and data.
  `tests/unit/test_copy_policy.py` fails on a literal passed to `ui.label`,
  `ui.button`, `ui.notify`, `toast`, a tooltip, or an input placeholder.
- `GLOSSARY.md` is normative. Use **Import**, never Load, Add, or Ingest.
- An Instrumental is an output, never a Stem; each Separation method declares its own
  Stems in `domain/methods.py`. `STEM_ROLES` and `OUTPUT_ROLES` are the Demucs and
  legacy vocabulary, not a global output list.

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
