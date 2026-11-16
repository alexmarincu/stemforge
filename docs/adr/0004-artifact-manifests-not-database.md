---
status: accepted
---

# Artifact metadata lives in filesystem manifests, not a database

Every artifact directory carries a schema-versioned `manifest.json`, written last and promoted by one atomic rename, with every path inside it relative to the manifest's own directory. `ArtifactStore` discovers state by scanning manifests and holds only the current Workspace's references in memory; filenames are never parsed to infer state. A database would be a second source of truth needing migrations, corruption repair, and reconciliation with a cache that is explicitly rebuildable from its Sources.

**Consequences:** startup and lookup cost grows with the number of cached manifests, so an index can be added later if scans become measurable; manifests stay inspectable and the cache stays movable; reference counts are derived from Track references rather than enforced by foreign keys.
