# StemForge

## Before changing code

Read `docs/agents/domain.md` first: `GLOSSARY.md` for the vocabulary of the area you
touch, and the ADRs under `docs/adr/` for the decisions that constrain it. An
implementation ticket does not close until its **Domain docs consulted** comment records
the glossary terms touched, the ADRs consulted (or "none apply" with a reason), and, for
UI or copy work, the copy pass against the glossary's avoided synonyms.

## Before pushing

Before pushing, run `scripts/check.sh`. Install the hooks once per clone with
`git config core.hooksPath .githooks`; a commit runs the fast checks and a push
runs the full gate.

## Agent skills

### Issue tracker

Implementation tickets live as GitHub issues in `alexmarincu/stemforge`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

## Architecture

[`ARCHITECTURE.md`](ARCHITECTURE.md) describes the current system: the package graph, the
Job pipeline, the parent/worker boundary, deployment shapes, and crosscutting rules. The
ADRs in `docs/adr/` record why each decision was made.

## Coding standards

See `CODING_STANDARDS.md` for copy ownership, vocabulary, tests, and docs.
Tooling-enforced rules stay in `pyproject.toml`.

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
