---
status: accepted
---

# YouTube import is a pinned, in-process, cookie-free adapter

StemForge v1 imports only public, non-age-gated YouTube videos: one video per import, no playlists, no accounts, and no cookies. `yt-dlp`, `yt-dlp-ejs`, and Deno are pinned to exact versions inside the frozen bundle (currently `2026.10.26` / `0.8.0` / `2.9.8`) with no runtime updater, no nightly channel, and no update check. yt-dlp runs in-process on the single JobRunner thread as a cancellable adapter, not as the standalone binary and not in a subprocess.

**Considered options:** an in-app yt-dlp updater (rejected — it would write into `_internal` and execute code fetched at runtime, breaking bundle reproducibility and the shipping notices), the official PyInstaller binary (rejected — ~40 MB more and a second Python runtime for crash isolation the Job model doesn't need), and opt-in browser cookies for age-restricted/private content (rejected — secrets handling, keyring portability, and account-ban support burden for a secondary case).

**Consequences:** YouTube changing its player breaks import until an app release ships a new pin; that is the accepted price of a static, auditable bundle, and the error taxonomy distinguishes extraction breakage from network, availability, and rate-limit failures so the remedy is clear. Revisit with an out-of-process adapter and a signed update channel if breakage becomes frequent.
