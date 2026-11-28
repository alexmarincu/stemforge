---
status: accepted
---

# Settings and About are dialogs, not routes

StemForge is a single-window NiceGUI app whose workspace holds session state owned by the client: the selected Track, the loaded preview target and playback position, and the page's maps of in-flight and finished Jobs. NiceGUI routes rebuild the client page. A `/settings` or `/about` route would therefore drop that state unless the route carried a full persistence layer for selection, playback, and Job routing — a cost out of proportion to two read-only or infrequently used surfaces.

**Decision.** Settings and About are `ui.dialog` cards inside the page's single dialog host, opened from the workspace footer, the per-Track detail gear for Settings, and the separation model gate for About. They are not routes. The host is cleared before either opens, so at most one of the two is open at a time. About's licence documents open as a second dialog over it, as before.

**Considered options:** NiceGUI routes (rejected: a client rebuild loses track selection, playback, and in-flight Job state, and the acceptance requires that state to survive); a single tabbed Settings & About modal (rejected: it keeps the mixed destination and title the split is meant to remove); a separate native window (rejected: the app is one window, and a native path adds platform-specific lifecycle code for no state or navigation gain).

**Consequences:** opening or closing either surface leaves the workspace intact. There are no deep links to Settings or About and no browser history for them. If routes are wanted later, the ticket must first define how selection, playback, and active Job references survive a client rebuild; the dialogs can then become route bodies without changing their contents.
