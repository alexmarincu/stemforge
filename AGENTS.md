# StemForge

## Before changing code

Read `docs/agents/domain.md` first: `GLOSSARY.md` for the vocabulary of the area you
touch, and the ADRs under `docs/adr/` for the decisions that constrain it. An
implementation ticket does not close until its **Domain docs consulted** comment records
the glossary terms touched, the ADRs consulted (or "none apply" with a reason), and, for
UI or copy work, the copy pass against the glossary's avoided synonyms.

## Agent skills

### Issue tracker

Implementation tickets live as GitHub issues in `alexmarincu/stemforge`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Code conventions

### Comments

Every comment states its reason in place; a citation is not a reason. A comment
explains why, never what, never when. Docstrings state the contract and its
invariants, not history. See `docs/adr/0014-comments-state-their-reason.md`;
`tests/unit/test_comment_policy.py` enforces it.
