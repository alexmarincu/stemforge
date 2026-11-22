---
status: accepted
---

# Comments state their reason in place; a citation is not a reason

Five hundred and twelve comments and docstrings cited implementation work items by number and section. Those work items lived in `.scratch/stemforge/issues/`, which was deleted when the decision-record scaffolding left the product repository. A checkout could not resolve a single one of them: the pointer outlived its target, and the reason it stood for was gone with it. The same habit escaped the comment layer, into `--help` text, error messages, a marker file written into a Windows bundle, workflow-dispatch descriptions, an import-linter contract name, and the generated licence tree.

A comment earns its place by stating something the code cannot. A pointer does not: if the reason lives elsewhere, the reader is sent away from the line that needs it, and the line rots alone when the target moves.

**Decision.** Every comment states its reason where it is read.

- Comments explain why. Never what, never when.
- A docstring states the contract and its invariants. Never history: no "we used to", no "per spec", no work-item number.
- The facade owns the public contract. A collaborator's docstring states only what its mechanism adds; repeating the facade's text creates a second copy that drifts.
- Output text, configuration values, and workflow metadata state the condition, not the work item that introduced it.
- A comment survives only if it encodes an external constraint (operating system, library, file format, licence), a hazard, or a rejected alternative that looks correct.
- An ADR states its decision and the evidence for it. It may reference another ADR, because ADRs are versioned in this repository and move with the code; it may not appeal to a document that lives outside it.
- A section sign names a licence clause (``GPL-3.0 §6(b)``) or it is a work-item section and does not belong.

**Consequences:** `tests/unit/test_comment_policy.py` fails on a reintroduced work-item pointer across source, tests, scripts, packaging, workflows, configuration, and ADRs. `LICENSES/` is exempt because it is generated and already compared byte-for-byte against `packaging/*.lock.json`; the test asserts that exemption still matches what the generator writes, so it cannot survive the generator. `docs/agents/` is exempt because it describes the agent tooling, where "decision record" is the subject rather than a pointer. Removing a citation does not remove the rule it encoded: the rule is written out in place, and a rule that is architectural enough to need more than a sentence belongs in a new ADR.
