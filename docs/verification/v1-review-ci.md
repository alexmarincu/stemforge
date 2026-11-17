# Review fix — CI topology, coverage, and nightly separation

Measured in the `fix/v1-review-ci` worktree on 2026-11-17, based on
`integration/v1` `248e15d` (tickets #1–#24 closed). The final two-axis review
found that ticket 11 §5–§6 were only partly present: `ci.yml` had five jobs
(`lint`, `licensing`, `unit-linux`, `unit-windows`, `frozen-linux`), so no job
ran the integration or UI layers, nothing produced coverage data, and there was
no nightly separation run. This branch adds the missing graph without touching
`release.yml` or product code.

## `ci.yml` job graph

| Job | Runner | Ceiling | Sync | Content |
|---|---|---:|---|---|
| `lint` | `ubuntu-24.04` | 5 | full | ruff format/check, mypy, import contracts, `uv lock --check`, shellcheck |
| `unit-linux` | `ubuntu-24.04` | 5 | lightweight | `pytest -m unit`, `.coverage.unit-linux` |
| `unit-windows` | `windows-2022` | 5 | lightweight | `pytest -m unit` |
| `integration-linux` | `ubuntu-22.04` | 12 | full (torch CPU) | vendor cache/build/verify, `pytest -m integration` including `slow`, `.coverage.integration-linux` |
| `integration-windows` | `windows-2022` | 25 | lightweight | PSScriptAnalyzer (`-Severity Error`), vendor cache/build/verify, `pytest -m "integration and not slow"`, `--preflight --json` schema check |
| `ui-linux` | `ubuntu-24.04` | 5 | lightweight | `pytest -m ui`, `.coverage.ui-linux` |
| `licensing` | `ubuntu-24.04` | 5 | full | `generate_notices.py --check`, `--check-release`, `pytest -m licensing` |
| `coverage` | `ubuntu-24.04` | 3 | lightweight | downloads the three coverage files, `coverage combine`, both §6 gates |
| `frozen-linux` | `ubuntu-22.04` | 15 | full + build group | GUI packages, vendor cache, `build.sh`, `smoke-test.sh`, reports on failure |

The lightweight sync is exactly ticket 11 §5:

```text
uv sync --frozen --no-install-package torch --no-install-package torchaudio --no-install-package demucs
```

Every later command in those jobs uses `uv run --frozen --no-sync` so the
exclusions are not undone. `integration-linux`, `licensing`, and `frozen-linux`
install the full lock. All jobs run in parallel; no xdist, no retries.

The licence-tree tests are a fourth layer marker, `licensing`. They need the
full installed dependency metadata: the generator reads each package's
`dist-info` (torch alone ships 70 licence files), which the lightweight sync
deliberately omits. They run in the full-sync `licensing` job with
`pytest -m licensing`; the unit and UI layers stay torch-free.

## Coverage

Coverage is combined, not per job. The three producer jobs set
`COVERAGE_FILE=.coverage.<job>` and upload a `coverage-<job>` artifact with
7-day retention; `coverage` downloads `coverage-*` with
`actions/download-artifact@v4` (`merge-multiple: true`), combines, and runs:

```text
coverage report --fail-under=85 --include="src/stemforge/domain/*,src/stemforge/jobs/*,src/stemforge/artifacts.py,src/stemforge/pipeline/*,src/stemforge/engines/*,src/stemforge/settings.py,src/stemforge/application.py,src/stemforge/worker_protocol.py,src/stemforge/errors.py"
coverage report --fail-under=70
```

`[tool.coverage.report]` already omits `ui/` and `workers/` from the overall
figure. Measured locally with `coverage run -m pytest -m "unit or ui"` followed
by `coverage run --append -m pytest -m integration`, after merging
`fix/v1-review-functional` so the numbers describe the final tree:

| Gate | Measured | Threshold |
|---|---:|---:|
| Core | **90%** (5266 statements, 545 missed) | ≥ 85% |
| Overall | **85%** (7932 statements, 1223 missed) | ≥ 70% |

No threshold was lowered and no test was added just to move the number.

## Nightly separation

`nightly.yml` runs at `0 3 * * *` UTC and on `workflow_dispatch`, never on a
push or pull request. It has exactly two jobs, both reusing the existing bundle
workflows through `workflow_call`:

| Job | Called workflow | Inputs | Ceiling |
|---|---|---|---:|
| `linux-frozen-separation` | `linux-bundle.yml` | `separation: true` | 35 |
| `windows-frozen-separation` | `windows-bundle.yml` | `with_separation: true`, `upload_binaries: false` | 50 |

Reuse is deliberate: `linux-bundle.yml` already performs the full build, the
pinned model install, `--smoke-separation`, `package.sh`, and the AppImage
`--appimage-extract-and-run --self-test`; `windows-bundle.yml` performs the
build and `smoke-test.ps1 -WithSeparation`. A job that calls a reusable workflow
cannot set `timeout-minutes` itself (GitHub's allowed-key set for a `uses` job
excludes it), so the ceiling is a `workflow_call` input and the called job
applies it: `timeout-minutes: ${{ inputs.timeout-minutes || 45 }}` (Linux) and
`|| 35` (Windows). The dispatch defaults stay 45/35. `actionlint 1.7.12`
validates all five workflows, including that wiring.

The nightly has `permissions: contents: read`. It uploads evidence only (Linux
reports/build logs, Windows evidence) and passes `upload_binaries: false`; it
never creates or modifies a GitHub Release. Release assets remain exclusively
`release.yml`'s.

`linux-bundle.yml` now records the ticket 11 §5 frozen preflight:

```sh
"$BUNDLE/StemForge" --preflight --json --report build/reports/preflight.json
```

Exit `0` is a clean host. Exit `3` (GTK/WebKitGTK) and `4` (GStreamer) are the
documented browser fallback in `packaging/linux/run.sh` and are recorded rather
than treated as a regression; any other exit fails the run.

## Verification

| Check | Result |
|---|---|
| `tests/unit/test_ci_topology.py` + `tests/unit/test_windows_packaging.py` | 70 passed |
| `ruff format --check` / `ruff check` on the changed test | clean |
| `actionlint .github/workflows/*.yml` (1.7.12) | clean |
| All workflow YAML parsed by the topology tests | pass |
| Lightweight `pytest -m unit` | 915 passed, 2 skipped in 28 s |
| Lightweight `pytest -m ui` | 81 passed in 36 s |
| Lightweight `pytest -m "integration and not slow"` | 131 passed in 66 s |
| Full `pytest -m "unit or ui"` | 996 passed, 2 skipped in 78 s |
| Full `pytest -m licensing` | 26 passed in 4 s |
| Full `pytest -m integration` | 133 passed, 7 skipped in 71 s |
| `scripts/check.sh` | pass (133 integration, 7 vendor skips) |
| `release.yml` digest | unchanged (`cd7ff89f…c2802`) |

The 7 skipped integration tests are `tests/integration/test_vendor_binaries.py`,
which skips without `vendor/`. CI fetches and verifies the locked vendor set
first, so they run there.

## Deviations and evidence still to come

- **`integration-linux` ceiling is 12 minutes**, as ticket 11 §5 specifies. The
  earlier review brief said 15; the ticket wins.
- **The licence-tree tests are a fourth layer marker.** They cannot run in the
  torch-free unit layer (the generator needs installed `dist-info`), so 26 tests
  moved from `unit` to `licensing` and run in the full-sync `licensing` job.
- **The `release.yml` pin is an LF digest.** Git checks out CRLF on the Windows
  runner, so the test normalizes `\r\n` to `\n` before hashing; the Linux and
  Windows jobs now compare the same bytes.
- **Ticket 11 says the nightly runs `fetch_models.py`.** No such script exists in
  this repository; the single model-download path is the bundle's
  `--install-model --accept-model-licence`, which `release.yml` also uses. The
  nightly therefore installs the same pinned, hashed weights through the bundle.
- **Ticket 11 says `smoke-test.sh --with-separation`.** The Linux workflow runs
  the same `--self-test` / `--smoke-test` checks and then an explicit
  `--smoke-separation` step against the same `STEMFORGE_HOME`. This is
  behaviourally equivalent and keeps the two reports separate; the topology test
  accepts either form. Windows uses the literal
  `smoke-test.ps1 -WithSeparation`.
- **Nightly is not run here** (the ticket forbids using it as a substitute for
  PR CI evidence, and it must exist on the default branch before `schedule`
  fires).
- **`integration-windows` and the nightly jobs cannot be exercised on this
  Linux host.** The first post-merge CI run is the Windows integration evidence;
  any failure there is fixed on a follow-up branch rather than claimed here.
