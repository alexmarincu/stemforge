---
status: accepted
---

# User-visible copy resolves through one locale catalog

StemForge shipped English-only: roughly 220 message constants and 60 coded
error-copy rows in the UI copy module, plus display labels in the application and
pipeline layers, Job stage and detail strings, preflight dialog chrome, and native
dialog filters. The product now ships Romanian first, with English remaining the
source and fallback. NiceGUI 4 localises its own Quasar controls and the
`<html lang>` attribute, but it does not own application copy. A translation layer
had to choose between gettext, English-as-msgid catalogs, and semantic message
IDs, and decide which surfaces stay English.

**Decision.** All user-visible copy resolves at render time through one neutral
i18n module backed by per-locale JSON catalogs. Catalogs use flat semantic message
IDs (`export.save_button`), English is the source locale and the per-key fallback,
and `en` and `ro` are the only shipped catalogs. A catalog entry is either a
string or a plural map (`one`/`few`/`other`); interpolation uses named
placeholders. A process-wide translator is set at startup from the persisted
Interface language or the OS locale and swapped only on the UI thread; changing
the language reloads the page so no screen can mix languages. Job stage and detail
codes, application display keys, and preflight dialog chrome resolve through the
same catalog. Logs, diagnostics, internal identifiers, exported filenames, audio
tags, and licence documents remain English. Consent notices are translated, but
the English licence text governs and only an English-source change bumps the
YouTube notice version. The language setting is an additive
`system`/`en`/`ro` value in the settings document; an unknown value falls back to
`system` without corrupting the rest of the document. NiceGUI's framework
language is set alongside the catalog so Quasar-owned strings and `<html lang>`
agree.

**Considered options:** gettext with `.po`/`.mo` (rejected: a compile step in both
PyInstaller builds, fuzzy and stale state, and msgid churn when English is edited,
for one target locale); English source strings as message keys (rejected: copy
edits invalidate translations, homonyms collide, and a semantic name is the more
stable key); Python dict catalogs (rejected: translators edit Python and merge
conflicts are ordinary); an injected translator through every page and component
(rejected: dozens of constructors and formatting helpers for no testability gain,
while preflight and native dialogs need a process-wide default anyway); per-locale
headline caps (rejected: identical data would behave differently per language, so
one layout cap is audited against both locales); translating native dialog filter
descriptions (rejected: the only prose is "Audio" and technical format names, and
platform rendering is a risk for near-zero gain); a source hash per translation
entry (rejected: churn for one locale, while structural CI catches the real
failure, an orphaned or missing key).

**Consequences:** every user-visible string must resolve through a catalog ID; the
copy-policy test moves its source from the UI copy module to the English catalog
and requires catalog keys at call sites. The UI copy module keeps formatting and
code-to-message mapping, not prose. Job snapshots gain stage and detail codes plus
a detail context alongside the English strings that logs and the headless CLI
keep. Application and pipeline layers return stable role and kind keys; filename
role labels stay English so a Romanian interface still writes
`Song - Vocals.flac`. Tests pin English explicitly rather than reading the host
locale; a Romanian page-render smoke test and catalog-parity tests guard the
translation. `GLOSSARY.md` gains **Interface language**, and
`docs/i18n/ro/terms.md` records the approved Romanian renderings and the forms the
copy policy rejects. Translator credits live in `TRANSLATORS.md` and one About
line. The implementation landed as a wide refactor in expand-contract order:
foundation, copy-migration batches, Job and display codes, then the Romanian
locale and language setting.
