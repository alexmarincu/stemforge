# StemForge

## Agent skills

### Issue tracker

Implementation tickets live as GitHub issues in `alexmarincu/stemforge`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: root `GLOSSARY.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Architecture

[`ARCHITECTURE.md`](ARCHITECTURE.md) describes the current system: the package graph, the
Job pipeline, the parent/worker boundary, deployment shapes, and crosscutting rules. The
ADRs in `docs/adr/` record why each decision was made.

## Code conventions

### Comments

Every comment states its reason in place; a citation is not a reason. A comment
explains why, never what, never when. Docstrings state the contract and its
invariants, not history. See `docs/adr/0014-comments-state-their-reason.md`;
`tests/unit/test_comment_policy.py` enforces it.
