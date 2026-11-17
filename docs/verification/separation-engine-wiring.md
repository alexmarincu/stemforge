# Separation fails with `internal.unexpected`

Fixed on `fix/separation-engine-wiring`, based on `integration/v0.1.0` `7ae16de`
(2026-11-17). The Separation card showed "Something went wrong" with only
`internal.unexpected`; no separation Job reached the runner and no job log was
written.

## Root cause

`__main__._run_ui` built the production service with the media and pitch
engines only:

```python
engines = ServiceEngines(media=media, pitch=RubberBandEngine(media=media))
```

`WorkspaceService._submit_separation` checks the separation engine first and
raises `internal.unexpected` ("the separation engines are not configured") when
it is `None`. The UI's error box shows the code but not the detail, which is why
the report looked opaque.

The unit, integration, and UI suites all inject a fake separation engine into
`WorkspaceService`, so none of them exercised the production wiring. The real
worker path (`--smoke-separation`, ticket 14) constructs
`WorkerSeparationEngine` directly and also bypassed `_run_ui`.

## Fix

`_run_ui` now constructs the real engine and passes it through:

```python
from stemforge.engines.separation import WorkerSeparationEngine

engines = ServiceEngines(
    media=media,
    pitch=RubberBandEngine(media=media),
    separation=WorkerSeparationEngine(),
)
```

The default argv is `python -m stemforge.workers.separation` in a source
checkout and `StemForge --worker separation` in a frozen bundle, so the same
wiring works in both. YouTube still uses its lazy default importer; it does not
need an explicit engine.

## Regression test

`tests/unit/test_run_ui.py::test_run_ui_connects_the_separation_engine` runs
the real `_run_ui` (with `ui.run` mocked), asserts the captured service has a
`WorkerSeparationEngine`, and calls `service.separate()` on a Track. Before the
fix it fails at the engine assertion (`separation=None`); after the fix the only
refusal is the model gate (`separation.model_missing`), because the test home
has no weights.

## Verification

| Check | Result |
|---|---|
| New test before the fix | failed: `isinstance(None, WorkerSeparationEngine)` |
| New test after the fix | passed |
| `pytest tests/unit/test_run_ui.py` | 10 passed |
| `pytest -m "unit or ui"` | 1000 passed, 2 skipped |
| `pytest -m "integration and not slow"` | 131 passed |
| `ruff format --check` / `ruff check` | clean |
| `mypy src/stemforge` | clean |
| `lint-imports` | 2 contracts kept |

A real end-to-end run used the production service wiring, the installed
`htdemucs` weights, and a synthetic one-second 44.1 kHz stereo WAV:

```text
import: succeeded
tracks: 1
separation: succeeded progress=1.00
sets: 1
```

The committed set contained `vocals`, `drums`, `bass`, `other`, and
`instrumental` as both WAV and MP3 previews.

The running app from before the merge still has the old service in memory and
must be restarted. After restart, Separation submits a normal `separate` Job;
the model is already installed and verified, so it should proceed to the worker.
