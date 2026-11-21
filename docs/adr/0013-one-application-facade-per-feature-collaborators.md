---
status: accepted
---

# The UI keeps one application facade; feature collaborators stay internal

`WorkspaceService` is the only application object the UI may call. Its 79-method surface had become a Divergent Change hotspot, so the implementation now delegates to per-feature collaborators (`ImportService`, `YouTubeService`, `PitchService`, `SeparationService`, `ExportService`, `ModelService`, `CacheService`, `RecentService`, `PreviewService`) under `stemforge.application.collaborators`. The facade remains the composition root: it wires the shared `WorkspaceState`, `JobLedger`, stores, engines, and settings, and exposes the same methods and view types as before. Collaborators receive explicit dependencies; none holds the facade.

Calling feature services directly from the UI was rejected: the UI would then depend on nine seams, have to know which one owns each action, and the shared job/workspace state would leak. Keeping one facade also keeps the import-linter contract meaningful.

**Consequences:** the UI never imports a collaborator; import-linter enforces that. A new feature adds a collaborator and a delegation instead of another cluster of methods on the facade. Cross-feature operations such as `remove_track` and `open_recent` stay at the composition point rather than being pushed into an ill-fitting feature. The cost is more files and more constructor wiring.
