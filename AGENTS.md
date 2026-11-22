# StemForge

## Agent skills

### Issue tracker

Implementation tickets live as GitHub issues in `alexmarincu/stemforge`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Code conventions

### Design

Read `DESIGN.md` before changing `src/stemforge/ui/`. `src/stemforge/ui/theme.py`
is canonical; `tests/unit/test_design_doc.py` keeps the document's token table in
sync.

### Comments

Every comment states its reason in place; a citation is not a reason. A comment
explains why, never what, never when. Docstrings state the contract and its
invariants, not history. See `docs/adr/0014-comments-state-their-reason.md`;
`tests/unit/test_comment_policy.py` enforces it.
