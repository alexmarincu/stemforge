---
status: accepted
---

# The native window's localhost server is trusted and unauthenticated

NiceGUI native mode serves the real UI from `http://127.0.0.1:<random port>`, and adding an authentication handshake to that initial load is not worth the complexity for a single-user desktop app. v1 binds explicitly to localhost, disables API docs and endpoint documentation, and relies on the OS user boundary instead of app-level auth. One media route mounts the cache root; NiceGUI confines requests to that root with `is_relative_to`, and only MP3 previews are referenced by the UI. Closing the window ends the app immediately because NiceGUI cannot veto a close.

**Consequences:** any local process can reach the app and read cached audio while it runs; there is no external listener and no telemetry; tightening the boundary later means a token-aware middleware or an MP3-only range route, not a UI redesign.
