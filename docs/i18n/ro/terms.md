# Romanian term table

The approved Romanian renderings for every glossary term that appears in the
interface, plus the interface vocabulary that names actions and sections. This
file is the translation contract: the Romanian catalogue uses these renderings, and
the copy policy test rejects the avoided forms.

`GLOSSARY.md` remains the canonical English vocabulary. This table is its Romanian
projection, not a second glossary; if a rendering here conflicts with a glossary
definition, the definition wins and this table changes.

## Core terms

| English | Romanian | Notes |
|---|---|---|
| Artifact | **rezultat** | Never **artefact**, which in Romanian also names a spurious signal or defect. |
| Batch export | **export în lot** | The UI action is **Exportă toate rezultatele**; never **export masiv**. |
| Cache | **cache** | Kept. Write **cache-ul** with the hyphen. |
| Corresponding source | **Sursa corespunzătoare** | The GPL term. |
| Degraded mode | **mod degradat** | Only if a degraded capability is announced in the UI. |
| Diagnostics report | **diagnostic** | Singular. The report body stays English. |
| Export | **export** | Noun **export**, verb **Exportă**. |
| Import | **import** | Noun **import**, verb **Importă**. Never **încarcă**, **adaugă**, or **ingerează**. |
| Instrumental | **instrumental** | Kept. An Instrumental is not a Stem. |
| Interface language | **limba interfeței** | The setting label is **Limbă**. |
| Job | **sarcină** | UI only. Logs and the diagnostics report keep **job**. |
| Local file | **fișier local** | A Source origin, not a Track. |
| Native file | **fișier importat** | The UI calls it the Imported file; never **fișier brut** or **fișier sursă**. |
| Original | **original** | The unshifted master in Outputs and A/B. Not the Native file. |
| Outputs | **Rezultate** | The Outputs card. |
| Partial | **parțial** | Only if a partial file is named. |
| Pitch shift | **transpunere** | The Pitch card is **Transpunere**; the engine is **motor de transpunere**. |
| Playback | **redare** | |
| Preview | **previzualizare** | |
| Recent | **recente** | |
| Render | **randare** | Verb **a randa**, button **Randează**. |
| Save original | **Salvează fișierul importat…** | Translated by its object, never **Salvează originalul**, which collides with Original. |
| Semitone | **semiton** | Plural **seminotonuri**. |
| Separation | **separare** | Verb **Separă**. |
| Shifted master | **master transpus** | Audio jargon kept. |
| Source | **sursă** | A Track's origin or the signal-path input. |
| Stem | **stem** | Kept. Plural **stemuri**. Never **pistă**. |
| Track | **piesă** | A single audio item. Never **pistă**; **pistă audio** is an audio stream, not a Track. |
| Workspace | **spațiu de lucru** | Only if the session collection is named in the UI. |

## Stem roles

| English | Romanian |
|---|---|
| Vocals | **Voci** |
| Instrumental | **Instrumental** |
| Drums | **Tobe** |
| Bass | **Bas** |
| Other | **Altele** |

## Interface vocabulary

| English | Romanian |
|---|---|
| App | **Aplicație** |
| Behaviour | **Comportament** |
| Bundled versions | **Versiuni incluse** |
| Cancel | **Anulează** |
| Choose another folder… | **Alege alt folder…** |
| Choose folder… | **Alege folderul…** |
| Clear cached audio | **Golește cache-ul audio** |
| Clear recent files | **Șterge fișierele recente** |
| Clear YouTube cache | **Golește cache-ul YouTube** |
| Close | **Închide** |
| Copy diagnostics | **Copiază diagnosticul** |
| Details | **Detalii** |
| Dismiss | **Închide** |
| Drop audio files here | **Trage fișiere audio aici** |
| Export | **Exportă** |
| Export all outputs | **Exportă toate rezultatele** |
| File name | **Nume fișier** |
| Format | **Format** |
| Import a track | **Importă o piesă** |
| Import audio | **Importă audio** |
| Language | **Limbă** |
| Licence | **Licență** |
| Licence files | **Fișiere de licență** |
| Open licences folder | **Deschide folderul de licențe** |
| Open log | **Deschide jurnalul** |
| Open log folder | **Deschide folderul jurnalelor** |
| Outputs | **Rezultate** |
| Playlist | **listă de redare** |
| Preview unavailable | **Previzualizare indisponibilă** |
| Primary | **principal** |
| Recent files cleared | **Fișierele recente au fost șterse.** |
| Remove | **Elimină** |
| Render | **Randează** |
| Retry | **Reîncearcă** |
| Settings & About | **Setări și despre** |
| Show all | **Arată tot** |
| Show less | **Arată mai puțin** |
| Storage | **Stocare** |
| Sum | **sumă** |
| System | **Sistem** |
| Third-party notices | **Licențe terțe** |
| Version | **Versiune** |
| View | **Vezi** |
| Worker process | **proces separat** |

The YouTube error copy says **listă de redare**, not the English loanword
**playlist**. The separation worker is **procesul separat**, never
**procesul de lucru**. A size written out uses **octeți**; the symbols `MB` and
`GB` stay invariant.

## Job labels

| English | Romanian |
|---|---|
| Importing | **Se importă** |
| Separating | **Se separă** |
| Rendering | **Se randează** |
| Exporting | **Se exportă** |
| Saving original | **Se salvează fișierul importat** |
| Downloading model | **Se descarcă modelul** |
| Starting | **Se pornește** |
| Working | **Se lucrează** |

## Plurals

Romanian uses three CLDR forms: **one** for 1, **few** for 0, 2–19, and the
hundreds that end in 01–19 (`101–119`, `201–219`, …), and **other** for
everything else. The **other** form takes **de** before the noun.

| Count | Form | Example |
|---:|---|---|
| 1 | one | `1 fișier`, `1 stem`, `1 minut` |
| 0, 2–19, 101–119, 201–219, … | few | `2 fișiere`, `19 stemuri`, `101 stemuri` |
| 20–100, 120–199, … | other | `20 de fișiere`, `120 de stemuri`, `20 de minute` |

The English catalogue uses **one** and **other** only. A plural entry is an object
with the locale's forms, not a set of singular/plural constants.

## Diacritics and punctuation

Use comma-below **ș** (U+0219) and **ț** (U+021B), never the cedilla forms
**ş** and **ţ**. Use **ă**, **â**, and **î** normally. Romanian quotation marks
are **„…“**. Technical units and symbols keep their invariant form: `MB`, `GB`,
`kHz`, `dB`, `44.1`, `m:ss`, `+3`, `−3`.

## Stays English

Product and vendor names (**StemForge**, **yt-dlp**, **yt-dlp-ejs**, **Deno**,
**Demucs**, **htdemucs**, **Rubber Band**, **FFmpeg**, **WebKitGTK**, **GTK**,
**GStreamer**, **WebView2**, **YouTube**); audio formats and technical units
(**WAV**, **FLAC**, **MP3**, **M4A**, **CPU**, **glibc**); command lines and
environment variables; log and diagnostics text; exported filenames and audio
tags; licence documents and their file names.

## Avoided renderings

The copy policy may test for these forms in the Romanian catalogue:

- **pistă** for a Track or Stem; only **pistă audio** for an audio stream.
- **încarcă**, **adaugă**, **ingerează** for Import.
- **artefact** for an Artifact.
- **export masiv** for a Batch export.
- **Salvează originalul** for Save original.
- **job** in the interface for a Job.
- **fișier brut** or **fișier sursă** for a Native file.
