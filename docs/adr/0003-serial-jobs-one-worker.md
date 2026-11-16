---
status: accepted
---

# Jobs run serially on one background thread; Separation alone gets a Python worker process

StemForge's engines are blocking external processes, the UI polls for progress, and asyncio subprocesses are a portability trap across Windows event-loop policies. So the app runs one `JobRunner` daemon thread with a FIFO queue and one active Job, exposing immutable snapshots that the NiceGUI page refreshes every 400 ms. Separation is the only Python worker: the frozen executable re-invokes itself with `--worker separation`, importing torch and Demucs in a crash-isolated process that shares the app's runtime. Pitch, FFmpeg/ffprobe, and yt-dlp are adapters called by the runner; the UI server never blocks on engine work.

**Consequences:** a fast export queues behind a long Separation; a segfaulting model or CLI fails one Job instead of the app; the UI needs no event bus or push channel; adding concurrency later means a second runner, not a UI rewrite.
