# Ticket 21 — Windows bundles

**Status: implemented and statically verified on Linux; Windows runner evidence pending.**
There is no Windows host, MSVC toolchain, Inno Setup compiler, or installable
desktop in this environment. Nothing below claims a Windows build, install, or
launch ran here. The licence tree from ticket 22 is now on `integration/v1`, so
the build no longer needs its non-release escape hatch. The exact runner and
manual commands that produce Windows evidence are recorded in §6–§7; they feed
ticket 23's release checklist.

## 1. What was built

| Path | Purpose |
|---|---|
| `packaging/pyinstaller/stemforge_windows.spec` | Windowed onedir spec; `console=False`, icon, generated version resource, Edge Chromium hidden import, legacy engine excluded, torch trim. |
| `packaging/windows/version_info.py` | Generates the `VSVersionInfo` text from `stemforge.__version__`; build.ps1 passes it through `STEMFORGE_VERSION_FILE`. |
| `packaging/windows/build.ps1` | Vendor gate, CPU torch assert, PyInstaller, Bundle-root assembly (vendor, licence tree, README, exe), `verify_bundle.py`. |
| `packaging/windows/verify_bundle.py` | Pure-stdlib bundle verifier: budgets, weights, torch trims, vendor lock, licence tree (curated notices + Python dependency rows), PE subsystem/version resource, WebView2 pin. |
| `packaging/windows/package.ps1` | Portable ZIP from the same tree, Inno setup, `SHA256SUMS`, `UNSIGNED.txt`, artifact budgets. |
| `packaging/windows/stemforge.iss` | Per-user, no-UAC, Windows 10 22H2 floor, x64, WebView2 bootstrapper, data-preserving uninstall. |
| `packaging/windows/smoke-test.ps1` | Frozen `--self-test`, `--smoke-test`, `--preflight`, optional `--smoke-separation` with temporary `STEMFORGE_HOME`, wall/RSS reporting. |
| `packaging/windows/webview2.lock.json` | Evergreen Bootstrapper URL, resolved URL, size, SHA-256, provenance. |
| `packaging/windows/README.txt` | Portable ZIP readme: run, WebView2, SmartScreen, data paths, diagnostics, licence. |
| `packaging/windows/sha256sums.py` | GNU-format `SHA256SUMS` writer/checker used by package.ps1 and tests. |
| `scripts/vendor-fetch.ps1` | Windows vendor fetch now builds libsndfile 1.2.2 (CMake/MSVC) and Rubber Band 4.0.0 (Meson/MSVC) from the pinned source archives; `-RubberBandPrebuilt` remains the fallback. |
| `.github/workflows/windows-bundle.yml` | Manual `workflow_dispatch` Windows 2022 job: vendor build, bundle, smoke, package, evidence upload; 35-minute timeout; no PR trigger. |
| `tests/unit/test_windows_packaging.py` | 42 Linux-runnable assertions for the installer, spec, PowerShell scripts, verifier, version resource, and SHA-256 manifest. |
| `src/stemforge/integration/window.py` | Windows `app.native.start_args["gui"] = "edgechromium"` assertion, matching the startup preflight that exits 5 when WebView2 is absent. |

## 2. Inno Setup directives

`packaging/windows/stemforge.iss` is compiled by `package.ps1` with
`/DVersion=<stemforge.__version__>` and `/DWebView2Installer=<verified path>`.

| Directive | Value | Ticket 21 requirement |
|---|---|---|
| `AppId` | `{{7E2F6C2A-6D1B-4A57-9B6D-2B0E4A2F6C21}` | Stable per-product identity. |
| `MinVersion` | `10.0.19045` | Windows 10 22H2 + ESU floor. |
| `PrivilegesRequired` | `lowest` | Per-user install, no UAC. |
| `DefaultDirName` | `{localappdata}\Programs\StemForge` | Per-user program files. |
| `ArchitecturesAllowed` | `x64compatible` | x64 only. |
| `ArchitecturesInstallIn64BitMode` | `x64compatible` | 64-bit install mode. |
| `SetupIconFile` | `..\..\src\stemforge\assets\stemforge.ico` | The ticket 17 icon. |
| `UninstallDisplayIcon` | `{app}\StemForge.exe` | Uninstall entry icon. |
| `OutputBaseFilename` | `StemForge-{#Version}-win64-setup` | Release asset naming. |
| `Compression` / `SolidCompression` | `lzma2/max` / `yes` | Size budget. |
| `CloseApplications` | `yes` | Upgrade/close behaviour. |
| `[Files]` bootstrapper | `Source: "{#WebView2Installer}"; DestDir: "{tmp}"; Flags: dontcopy` | Embedded, not installed. |
| `[Code]` backend | `StemForgeBackend = 'edgechromium'` | Only supported engine. |
| `[Code]` WebView2 | HKCU, HKLM, and `WOW6432Node` `pv` lookup; `ExtractTemporaryFile` + `/silent /install` only when absent | Bootstraps silently; no legacy engine. |
| `[Code]` .NET | `NDP\v4\Full\Release >= 394802` check and message | Matches preflight. |
| `[Icons]` | Start-menu shortcut; desktop task unchecked | Standard per-user install. |
| `[Run]` | Optional post-install launch | Standard. |
| `[UninstallDelete]` | **Absent** for `{localappdata}\StemForge` | User data survives. |
| `[Code]` uninstall | `CurUninstallStepChanged(usPostUninstall)` shows `{localappdata}\StemForge` | User can delete deliberately. |

The `.iss` contains no `mshtml` string. The PyInstaller spec excludes
`webview.platforms.mshtml` and the startup preflight exits 5 before the UI when
WebView2 is missing, so pywebview's silent legacy fallback cannot reach a user.

## 3. WebView2 pin

`packaging/windows/webview2.lock.json`:

| Field | Value |
|---|---|
| Stable URL | `https://go.microsoft.com/fwlink/p/?LinkId=2124703` |
| Resolved URL (2026-11-17) | `https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/068be74d-a831-494f-8f57-c173756d5a33/MicrosoftEdgeWebview2Setup.exe` |
| Size | `1,897,688` bytes |
| SHA-256 | `cf2f2445fef0c5c14e372045f84998771eeb8bb01d59be4afd00692fa160082e` |
| Licence | Microsoft proprietary redistributable; Microsoft terms, not StemForge's GPL. |

I downloaded that URL in this environment and computed the size and SHA-256 from
the bytes; the pin is not copied from a web page. `package.ps1` downloads through
the fwlink, verifies size and SHA-256, caches under `build/redist/`, and fails
closed with a "refresh the pin deliberately" message if Microsoft rotates the
bootstrapper. The bootstrapper is unmodified, embedded in the installer as a
`dontcopy` file, and copied into the portable ZIP root.

## 4. Vendor build on the runner

`scripts/vendor-fetch.ps1` now prefers building the pair from the pinned source
archives recorded in `packaging/vendor.lock.json`:

1. **libsndfile 1.2.2** — `cmake -S <source> -B build -A x64 -DCMAKE_BUILD_TYPE=Release
   -DBUILD_SHARED_LIBS=ON -DBUILD_PROGRAMS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF
   -DENABLE_EXTERNAL_LIBS=OFF -DENABLE_MPEG=OFF -DENABLE_CPACK=OFF
   -DINSTALL_PKGCONFIG_MODULE=ON -DINSTALL_MANPAGES=OFF`; `cmake --build ... --config Release`;
   produces `sndfile.dll` and `sndfile.lib`.
2. **Rubber Band 4.0.0** — `meson setup build <source> --buildtype=release
   --default-library=static -Dfft=builtin -Dresampler=builtin -Dvamp=disabled
   -Dlv2=disabled -Dladspa=disabled -Djni=disabled -Dcmdline=enabled -Dtests=disabled
   --vsenv`; `ninja -C build`. The command line needs `dependency('sndfile')`, so the
   script writes a temporary `sndfile.pc` whose `Libs` is the absolute
   `sndfile.lib` path (the generic `-lsndfile` form does not link with MSVC) and
   checks `pkg-config --libs sndfile` before configuring.
3. The script enters the VS 2022 developer environment through `vswhere` +
   `Microsoft.VisualStudio.DevShell.dll` when `cl.exe` is not already on PATH,
   copies `rubberband-program.exe` as `vendor/rubberband/rubberband.exe`, copies
   `sndfile.dll` beside it, and runs `rubberband.exe --version` expecting `4.0.0`.

If MSVC, CMake, Meson, Ninja, or pkg-config is absent, the job fails closed with
a message naming the missing tool and the `-RubberBandPrebuilt` alternative. No
public prebuilt Rubber Band archive is named by the decision record, so none is
invented. The CI job installs Meson/Ninja with `uv pip` and `pkgconfiglite` with
Chocolatey; Inno Setup is installed if the image lacks it.

## 5. Static verification run here (Linux only)

Commands and results from this worktree:

| Gate | Result |
|---|---|
| `uv run ruff format --check .` | 181 files already formatted. |
| `uv run ruff check .` | All checks passed. |
| `uv run mypy src/stemforge` | Success: no issues in 59 source files. |
| `uv run lint-imports` | 2 contracts kept, 0 broken. |
| `uv run pytest -m "unit or ui"` | 797 passed, 1 skipped, 137 deselected. |
| `uv run pytest -m "integration and not slow"` | 128 passed, 807 deselected. |
| `STEMFORGE_HOME=<tmp> uv run stemforge --self-test` | exit 0, 17 ok / 2 skipped (development checkout). |
| `STEMFORGE_HOME=<tmp> uv run stemforge --smoke-test` | exit 0, HTTP 200 in 0.519 s. |
| `pwsh` 7.5.4 parser over `scripts/vendor-fetch.ps1`, `build.ps1`, `package.ps1`, `smoke-test.ps1` | 0 syntax errors. Installed locally for this check only; not a Windows run. |
| `Invoke-ScriptAnalyzer` (Warning/Error) over the four scripts | 0 errors; 26 `Write-Host` style warnings, zero other warnings. |
| WebView2 pin | Download re-hashed to the recorded size and SHA-256. |
| `version_info.py` | Rendered text deserialised with PyInstaller's `VSVersionInfo` loader (with `pefile`/`win32api` stubbed on Linux) to `FileVersion`/`ProductVersion` `1.0.0`, `OriginalFilename` `StemForge.exe`. |
| `tests/unit/test_windows_packaging.py` | 42 passed (installer, spec, scripts, budgets, weights, vendor, licence, PE, SHA-256). |
| Merged #22 tree cross-check | `verify_bundle.py` passed 15 curated components and 14 native files using the merged `integration/v1` `LICENSE`, `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `LICENSES/`, and `packaging/licenses/native.toml` with a synthetic Windows bundle. |

The PowerShell parse and analyzer results are Linux-side static checks, not
evidence that the scripts execute on Windows. The Windows runner job in §6 is
the only such evidence.

## 6. Windows runner evidence (pending)

No Windows host is available here, so the workflow has not run. After this branch
is merged to `integration/v1`, dispatch:

```sh
gh workflow run windows-bundle.yml --ref integration/v1 \
  -f allow_licence_pending=false \
  -f with_separation=true \
  -f upload_binaries=false
gh run watch "$(gh run list --workflow windows-bundle.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

If GitHub refuses the dispatch because the workflow is not yet on the default
branch, run it from the merge commit's branch once the workflow is present, or
run the same steps manually on a `windows-2022` host:

> Attempted from `ticket/21-windows-bundles` on 2026-11-17 after pushing the
> branch: `gh workflow run windows-bundle.yml --ref ticket/21-windows-bundles ...`
> returned **HTTP 404: workflow windows-bundle.yml not found on the default
> branch**. GitHub requires `workflow_dispatch` workflows to exist on the
> default branch. Once this file lands on `main` (via #23 / the integration
> merge), re-run the dispatch; no code change is needed.

```powershell
uv sync --frozen --group build
uv pip install --python .venv\Scripts\python.exe meson ninja
choco install pkgconfiglite innosetup -y --no-progress
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/vendor-fetch.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/vendor-fetch.ps1 -VerifyOnly
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -SkipVendorFetch
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows/smoke-test.ps1 -WithSeparation
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/windows/package.ps1
```

Expected evidence to attach to ticket 23:

- `build/reports/self-test.json`, `smoke-test.json`, `preflight.json`, `smoke-separation.json`;- `build/windows/verify-bundle.json`, `verify-artifacts.json`, `version_info.txt`, `vendor-PROVENANCE.txt`;
- `dist/SHA256SUMS`, `dist/UNSIGNED.txt`, `build/reports/artifact-sizes.txt`;
- the frozen `--smoke-separation` wall seconds and separation RSS from the report;
- `StemForge-<ver>-win64-portable.zip` and `StemForge-<ver>-win64-setup.exe` only
  when `upload_binaries=true` (ticket 23 owns release assets; Actions storage is
  not the release channel).

Per ticket 11 §3, `--preflight` exit 5 (WebView2 absent) is recorded in the JSON
and does not fail the smoke script; exits 6/7/8 still do. The manual matrix in §7
is where both WebView2-present and WebView2-absent behaviour is verified.

The workflow is off pull requests. It uses `actions/cache` for `vendor/` keyed on
`packaging/vendor.lock.json` and for PyInstaller's bootloader cache keyed on
`uv.lock` + the vendor lock; it never caches `build/` or `dist/`.

## 7. Manual release matrix (ticket 21 done-when; feeds #23)

Run on the draft release assets after `release.yml` builds them. Record the
Windows edition/build, whether WebView2 was present before install, the artifact
SHA-256, and pass/fail for each row.

### A. Windows 11 x64, fresh profile, WebView2 present

1. Verify `SHA256SUMS` with `certutil -hashfile StemForge-<ver>-win64-setup.exe SHA256`.
2. Run the setup. Confirm: no UAC prompt; default directory
   `%LOCALAPPDATA%\Programs\StemForge`; Start-menu shortcut; desktop icon task
   unchecked by default; icon in the wizard and Add/Remove Programs.
3. Launch from the shortcut. Confirm the native window opens with the StemForge
   title/taskbar icon, no console window, and About shows `1.0.0`.
4. Import, preview, pitch, separate (first-run model consent/download), export,
   remove, clear cache, open logs, open About/licences/source offer.
5. Uninstall from Settings. Confirm the uninstaller removes the program files and
   shows the kept-data path; confirm `%LOCALAPPDATA%\StemForge` still contains
   models/cache/logs/settings; relaunch after reinstall reuses them.
6. Delete `%LOCALAPPDATA%\StemForge` only in a disposable VM.

### B. Windows 10 22H2 + ESU, WebView2 absent, installer path

1. Remove/disable the Evergreen Runtime (or use a fresh image without it).
2. Run the setup. Confirm the bootstrapper runs silently, installs WebView2, and
   the app opens without a legacy-engine blank page or "Vue requires Chrome".
3. Confirm `--preflight` reports WebView2 after install; if the bootstrapper is
   blocked by policy, confirm the actionable message and exit 5, and that the app
   does not open a blank window.
4. Repeat the install/launch/uninstall/data-survival checks from A.

### C. Windows 10 22H2 + ESU, WebView2 absent, portable ZIP path

1. Extract `StemForge-<ver>-win64-portable.zip` to a normal folder.
2. Run `StemForge.exe`. Confirm the actionable WebView2 dialog with the download
   link, and exit 5; no blank window.
3. Run `MicrosoftEdgeWebview2Setup.exe /silent /install` from the extracted
   folder, then run `StemForge.exe` again and confirm the window opens.
4. Confirm the portable tree has `vendor\`, `LICENSES\`, `LICENSE`,
   `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, `README.txt`, and no installer.
5. Confirm no weights are in the ZIP before download; the temporary model goes to
   `%LOCALAPPDATA%\StemForge\data\models` after consent.

### D. SmartScreen and unsigned wording

1. Download both artifacts through a browser on a fresh profile.
2. Confirm the wording is "Windows protected your PC" / "Unknown publisher" (or
   the current equivalent), that `UNSIGNED.txt` and `README.txt` match it, and
   that `SHA256SUMS` is the stated integrity check.
3. Confirm no signature is present (`Get-AuthenticodeSignature` → `NotSigned`)
   and that release notes say so. Do not add a code-signing certificate for v1.

### E. Frozen diagnostics on real hardware

Run from an installed tree or the extracted ZIP, with a disposable
`STEMFORGE_HOME` for the separation run:

```powershell
$env:STEMFORGE_HOME = Join-Path $env:TEMP "stemforge-manual"
.\StemForge.exe --preflight
.\StemForge.exe --self-test --report .\self-test.json
.\StemForge.exe --smoke-test --report .\smoke-test.json
.\StemForge.exe --install-model --accept-model-licence
.\StemForge.exe --smoke-separation --report .\smoke-separation.json
```

Require exit 0 for all, `< 60 s` wall and `< 2 GB` peak RSS in the separation
report, and no writes outside `STEMFORGE_HOME`.

## 8. Deviations and follow-ups

- **Licence tree content gate.** Ticket 22's `LICENSES/`,
  `THIRD_PARTY_NOTICES.md`, `SOURCE_OFFER.md`, and `packaging/licenses/native.toml`
  are merged on `integration/v1`; the verifier passes against them (15 curated
  components, 14 native files in the synthetic check). `build.ps1` still fails
  closed by default. The explicit `-AllowLicencePending` switch remains only for
  a branch without that tree: it writes `LICENCE-GATE-PENDING.txt`, which
  `package.ps1` refuses, and the workflow skips packaging when the input is true.
  Release builds use the default path and package normally.
- **Native manifest schema.** `verify_bundle.py` reads ticket 22's
  `packaging/licenses/native.toml` `[[component]]` rows (`id`, `title`,
  `texts`, `buildinfo`), requires `LICENSES/<id>/NOTICE.md` for every curated
  component and `LICENSES/common/<SPDX>.txt` for every declared text, and maps
  each shipped native file to either a curated notice (vendor tools,
  `StemForge.exe`, PyTorch/NumPy/Python/LLVM/GCC/OpenSSL/OpenBLAS) or a row in
  `LICENSES/python/DEPENDENCIES.md` (wheel extension modules). An unclassified
  native file fails. The verifier was cross-checked against the merged
  `integration/v1` tree with a synthetic Windows bundle: 15 curated components
  and 14 native files passed. Do not hand-edit either manifest; the generator
  owns them.
- **Budget mode.** `verify_bundle.py` treats crossing the target (ZIP 450 MiB,
  setup 380 MiB) as an error and names the higher hard-fail threshold (600/500
  MiB) in the message. The decision record's table calls only the higher number a
  release failure; the stricter default is deliberate for a 1.0.0 first release.
- **Windows runner evidence is pending**, as described in §6. No claim is made
  that the build, Inno compile, install, launch, or uninstall ran here.
- **WebView2 pin freshness.** Microsoft can rotate the Evergreen Bootstrapper;
  the build fails closed and the pin must be refreshed deliberately. The resolved
  URL is provenance, not a second download source.
