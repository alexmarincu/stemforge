StemForge for Windows
=====================

StemForge splits a song into stems, shifts pitch, and exports the result. It runs
locally on your PC: no account, no telemetry, and no audio leaves the computer.

This folder is a portable build. There is no installer and no uninstall entry.


Run it
------

Double-click StemForge.exe.

The first launch may take a few seconds. Model weights are not included: the
first Separation downloads the pinned htdemucs weights after you accept the
licence notice. You can also prepare them ahead of time from a Command Prompt:

    StemForge.exe --install-model --accept-model-licence

StemForge needs Microsoft Edge WebView2 Runtime, which is present on current
Windows 10 and Windows 11 installations. If it is missing, StemForge shows a
dialog with the download link and exits instead of opening a blank window. The
portable ZIP also includes MicrosoftEdgeWebview2Setup.exe so you can install it
offline:

    MicrosoftEdgeWebview2Setup.exe /silent /install

Windows 10 22H2 (build 19045) or newer, 64-bit, is supported. Windows 11 is the
primary target.


Unsigned build and SmartScreen
------------------------------

StemForge 0.1.0 is unsigned. Windows SmartScreen may show "Windows protected
your PC" or "Unknown publisher" the first time you run the download. That is
expected for an unsigned build.

Check the download before you run it:

    certutil -hashfile StemForge-0.1.0-win64-portable.zip SHA256

Compare the result with SHA256SUMS from the same release page. If the hashes
match, choose "More info" and "Run anyway" only if you trust the release page.
Do not disable SmartScreen globally.


Where your data lives
---------------------

    %LOCALAPPDATA%\StemForge\data       models, workspace, webview state
    %LOCALAPPDATA%\StemForge\cache      previews and temporary files
    %LOCALAPPDATA%\StemForge\logs       stemforge.log
    %LOCALAPPDATA%\StemForge\config     settings

Set STEMFORGE_HOME to one directory to keep all of it together, for example:

    set STEMFORGE_HOME=D:\StemForgeData
    StemForge.exe

Uninstalling the Inno Setup build removes the program files but never this
folder; delete it yourself if you also want to remove the model, cache, logs,
and settings.


Diagnostics
-----------

These commands exit without opening a window. Exit codes are authoritative; add
--report <path> to write JSON.

    StemForge.exe --preflight
    StemForge.exe --self-test
    StemForge.exe --smoke-test
    StemForge.exe --smoke-separation

--preflight exit 5 means WebView2 or .NET Framework 4.8 is missing. Exit 6 means
the data folder is not writable. Exit 8 means a bundled tool is missing or
damaged; re-extract the ZIP or reinstall.


Licence and source
------------------

StemForge is GPL-3.0-only. See LICENSE for the full text, THIRD_PARTY_NOTICES.md
for the bundled components, SOURCE_OFFER.md for the written offer, and LICENSES/
for the licence texts. The release page carries the corresponding source.

    https://github.com/alexmarincu/stemforge


Troubleshooting
---------------

- "WebView2 Runtime is not installed": run MicrosoftEdgeWebview2Setup.exe, or
  install the Evergreen Runtime from Microsoft, then start StemForge again.
- A blank window or "Vue requires Chrome": the Edge Chromium backend did not
  start. Run StemForge.exe --preflight and read the report; StemForge refuses
  the legacy engine rather than showing a broken page.
- "Bundle damaged": a file under vendor\ is missing. Re-extract the ZIP or
  reinstall; do not copy individual files between builds.
- Antivirus quarantine: the build is unsigned. Restore only after the SHA-256
  matches SHA256SUMS.
