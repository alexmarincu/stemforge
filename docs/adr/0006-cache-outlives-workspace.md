---
status: accepted
---

# The audio cache outlives the ephemeral Workspace

The Workspace is session-scoped and starts empty on every launch, but its expensive outputs — canonical audio, Stems, Instrumental, and pitch renders — persist in a per-user content-addressed cache under a size cap. A restart never deletes them; re-importing the same bytes (or YouTube video) reuses them. Deletion happens only on an explicit Remove (which names the deletion), LRU eviction under the cap, or a Clear cache action. Model weights live outside that cache in the data root and are never evicted.

**Consequences:** users get instant reuse after a crash or restart; Remove is a destructive action and needs confirmation; the cache is rebuildable and may be cleaned by the OS or the user without losing user-owned files; reference counts are a session concept, so startup must not treat zero live references as garbage.
