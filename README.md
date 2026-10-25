# MovieSort Pro

**MovieSort Pro** is a free, open-source Windows movie library organiser. Point it at one or more folders containing movies, fetch available film metadata, choose how you want files grouped, review the proposed changes, and copy or move the files into a new folder structure.

> **Status:** v2.1 community preview. The automated unit tests pass, but Windows packaging, network metadata services and large-library edge cases still need community testing. Start with **Copy** and a small sample library; keep backups.

## Features

- Scan multiple source folders and their subfolders for videos (`.mkv`, `.mp4`, `.avi`, `.m4v`, `.wmv`, `.mov`, `.mpg`, `.mpeg`, `.webm`, `.ts`, `.m2ts`, `.iso`).
- Identify films using local filename clues and your selected metadata sources.
- Organise using up to **three folder levels** with choices including genre, studio, year, decade, lead or selected actor, director, country, film collection, title, A–P / Q–Z, and A–Z.
- Preview destination paths before changing anything.
- **Copy** (default) or **Move**, optionally renaming movie files and including matching subtitles, NFOs and artwork.
- Skip exact destination duplicates using SHA-256 checks. Different files are given distinct names, not overwritten.
- Reorganise an already-sorted folder, including using the same source and destination.
- Keep a CSV report and a best-effort *Undo Last Move* journal.
- Save UI preferences between launches; on Windows, optional API credentials are encrypted with the current user's Windows Data Protection API (DPAPI).

## Install and run on Windows

### Option A: Download an EXE when available

When GitHub Actions succeeds for this repository, download the `MovieSortPro-Windows.zip` build artifact from **Actions**, or the EXE ZIP from **Releases** for tagged versions. Extract it and launch `MovieSortPro.exe`.

The executable is not code-signed. Windows may display a SmartScreen warning; only run binaries from a source you trust. Build it yourself if preferred.

### Option B: Run from source (Python 3.10+)

1. Install [Python for Windows](https://www.python.org/downloads/windows/), including Tkinter (in normal Python installers).
2. Download and extract the project, or clone it with Git.
3. Double-click `Run-MovieSortPro.bat`.

No third-party Python packages are needed to **run the source**.

### Option C: Build your own EXE

Install Python 3.10+, then run `Build-Windows-EXE.bat`. This installs PyInstaller and writes `dist\MovieSortPro.exe`.

## Quick start

1. Select one or more **source folders** and a **destination folder**.
2. Open **Configure Metadata Sources**, enable providers, and set their priority. Enter TMDB/OMDb credentials only if you want those providers.
3. Select a sorting preset or configure up to 3 levels (for example, `Genre / Year / Movie Title`).
4. Keep the operation on **Copy** when testing.
5. Click **Scan & Identify**, review all proposed destinations, and then **Organise Movies**.
6. Open **Last Report** for successes, skips or errors.

**Sample paths:**

```text
D:\Movies\Action\2024\Example Movie (2024)\Example Movie (2024).mkv
D:\Movies\Universal Pictures\A-P\Another Film (2021).mp4
```

> Sorting again? With **Move**, the original source directory will usually be empty after the first run. Scan the destination folder for future reorganisations. With **Copy**, an identical second run should skip movies that already exist, not create duplicates.

## Metadata providers

| Provider | API credential | Notes |
| --- | --- | --- |
| Local NFO | No | Reads matching movie `.nfo` files and supported `movie.nfo` files. |
| Local CSV/JSON catalogue | No | Import your existing data; see `sample_catalogue.csv` and `sample_catalogue.json`. |
| [TMDB](https://www.themoviedb.org/) | Yes | Movie titles, genre, cast, studio, and other attributes. |
| [OMDb](https://www.omdbapi.com/) | Yes | Additional title and movie metadata via OMDb API. |
| [Wikidata](https://www.wikidata.org/) | No | Public metadata, which can be incomplete or slower. |

Pick provider priority to control which metadata wins. You can optionally merge missing fields from a later source when the movie match is compatible. Missing or ambiguous matches are marked for review instead of being silently assigned an arbitrary film.

**TMDB notice:** This product uses the TMDB API but is not endorsed or certified by TMDB. The app includes this notice in **About / Credits**. TMDB also requires its **approved logo** in apps that use the API; an approved logo is **not bundled yet**, so add one before publicly distributing a TMDB-enabled binary. See [TMDB attribution guidance](https://developer.themoviedb.org/docs/faq) and [approved logos](https://www.themoviedb.org/about/logos-attribution). Users are responsible for complying with each metadata provider's terms, limits and attribution requirements. The project does not bundle an API key.

## Files and privacy

The app stores local configuration and reports beneath:

```text
%LOCALAPPDATA%\MovieSortPro\
  preferences.json
  metadata_cache.db
  last_run.json
  last_run_report.csv
```

Movie titles and their parsed names may be sent to your enabled **online** metadata providers. The app does not upload video files to them. Local NFO / catalogue access does not require the internet.

Windows stores API credentials encrypted with the signed-in user's DPAPI. On platforms without DPAPI, credentials are not persisted. **Never commit** your own `preferences.json`, API tokens, local paths, personal NFOs, or scan logs.

## Limitations and safety

- Actual video contents are not analysed: lookup relies on filename and metadata clues.
- A film with multiple genres/studios/actors currently uses the selected first match for folder grouping. It does not make one copy per category.
- Metadata availability, rate limits and licences vary by provider.
- File moves, hashing and copying across network shares may take considerable time. Interrupted operations can need manual review.
- Undo only applies to some **Move** operations and is best-effort; it does not undo copies.
- A folder path or filesystem permission failure can stop an individual transfer. See the CSV report for details.

## Test and contribute

Unit tests:

```powershell
python -m unittest discover -v
```

`gui_reruns_test.py` and `smoke_gui.py` exercise GUI flows and require a usable graphical display.

Bug reports and contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) first. When reporting a bug, include Windows version, app version, what happened on the first and second attempts, and **redacted** report entries. Never attach private media filenames or API keys without removing them.

## Community roadmap

- More reliable rescan/resume logic and explicit dry-run export.
- Additional metadata provider adapters (subject to provider policies).
- Manual review and correction of ambiguous movie matches.
- Better handling of multiple genres, collections, and duplicate sets.
- Installer, digital signing, and broader Windows testing.

## Licence

[MIT](LICENSE). Contributions should be compatible with this licence.