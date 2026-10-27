# MovieSort Pro

**MovieSort Pro** is a free, open-source Windows movie library organiser. Point it at one or more folders containing movies, fetch available film metadata, choose how you want files grouped, review the proposed changes, and copy or move the files into a new folder structure.

> **Status:** v3.1 community preview. The automated unit tests pass, but Windows packaging, network metadata services and large-library edge cases still need community testing. Start with **Copy** and a small sample library; keep backups.

## Features

- Scan multiple source folders and their subfolders for videos (`.mkv`, `.mp4`, `.avi`, `.m4v`, `.wmv`, `.mov`, `.mpg`, `.mpeg`, `.webm`, `.ts`, `.m2ts`, `.iso`).
- Identify films using local filename clues and your selected metadata sources.
- Organise using up to **three folder levels** with choices including genre, studio, year, decade, lead or selected actor, director, country, film collection, title, A–P / Q–Z, and A–Z.
- Preview destination paths before changing anything.
- **Unmatched movies:** when a confident film match is unavailable (including ambiguous titles or metadata lookup failures), route the video to `unmatched/` under the output directory instead of guessing a genre or studio. Enabled by default, and can be disabled in the sorting settings. Original filenames are preserved so you can identify and retry them later.
- **Copy** (default) or **Move**, optionally renaming movie files and including matching subtitles, NFOs and artwork.
- Skip exact destination duplicates using SHA-256 checks. Different files are given distinct names, not overwritten.
- Reorganise an already-sorted folder, including using the same source and destination.
- **V3 Collections:** automatically group two or more distinct owned movies in `Collections/<Series Name>/`, while other movies stay on their normal sorting paths.
- **Missing sequels:** use TMDB collection catalogues to flag released movies not found in **all scanned source folders**, distinguish upcoming and undated titles, show a collection dashboard, and export missing-film lists to CSV/TXT.
- **Series ordering:** show films in manual `collection_order` order where provided, otherwise TMDB release-date order. TMDB does not guarantee narrative order. The files keep Plex-friendly names without injected numbering.
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
5. Leave **Automatically organise movie collections** enabled (default minimum: 2 films) to route qualifying sets to `Collections/Series Name/`. Click **Collections / Missing Movies** after scanning to review present, missing and upcoming titles. Collection groups can be detected offline using NFO/catalogue data; **missing-title verification requires an enabled TMDB source and a valid TMDB token**.
6. Click **Scan & Identify**, review all proposed destinations, and then **Organise Movies**.
7. Open **Last Report** for successes, skips or errors. Successful transfers to `unmatched` have their own `UNMATCHED` status and are counted in the summary.

**Sample paths:**

```text
D:\Movies\Action\2024\Example Movie (2024)\Example Movie (2024).mkv
D:\Movies\Collections\The Mighty Ducks Collection\D3_ The Mighty Ducks (1996).mkv
D:\Movies\unmatched\Unknown.Movie.2022.mkv
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

Pick provider priority to control which metadata wins. You can optionally merge missing fields from a later source when the movie match is compatible. Missing or ambiguous matches are marked for review and (by default) go to the `unmatched` folder, rather than being silently assigned an arbitrary film.

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

## Unmatched files (V3.1)

Once a scan is complete, MovieSort Pro checks each video for a confident metadata match. Films without one receive a proposed destination at `<output>/unmatched/<original filename>`, regardless of the normal genre/year rules. This includes offline filenames without an NFO/catalogue match, ambiguous matches and temporary metadata provider errors.

- **Check the preview first.** You can correct a match before organising, or leave it in `unmatched` to investigate later.
- The global **Copy** (default) or **Move** action applies to both matched and unmatched films. Move removes the source only after a successful transfer; Copy leaves originals intact. Nothing is forcibly moved while Copy is selected.
- Matching subtitles, NFO files and artwork follow the movie when **Bring matching subtitles and artwork** is enabled. Unmatched filenames are never automatically renamed.
- Repeated runs skip files already at their intended destination or with identical content. Collisions never overwrite files.
- For a future reattempt, select the existing `unmatched` directory or destination library as a source, then **Scan & Identify** again. Films with newly recognised metadata can be moved into their regular destination folders.
- Disabling **Send unidentified films to unmatched** restores the prior sorting-plan behaviour for unresolved files.
- Use the run report to distinguish `UNMATCHED` transfers from `OK`, `SKIPPED` and `ERROR`.

## Limitations and safety

- Actual video contents are not analysed: lookup relies on filename and metadata clues.
- The missing-films dashboard describes titles **not found in the selected scanned folders**, not a judgement about what you own elsewhere. Uncertain matches, API failures and groups without TMDB IDs are not labelled complete.
- Missing-title detection reports franchises only after **at least two different films** in that series are found locally. Duplicate encodes do not qualify.
- TMDB's collection membership and release chronology may differ from a film's narrative viewing order; supply `collection_order` through a CSV/JSON catalogue to override ordering in the dashboard.
- Online collection API calls may take time for a large library; cached collection details are used for 30 days. Existing collection reports need a rescan to refresh.

- A film with multiple genres/studios/actors currently uses the selected first match for folder grouping. It does not make one copy per category.
- Metadata availability, rate limits and licences vary by provider.
- File moves, hashing and copying across network shares may take considerable time. Interrupted operations can need manual review.
- Undo only applies to some **Move** operations and is best-effort; it does not undo copies.
- A folder path or filesystem permission failure can stop an individual transfer. See the CSV report for details.

## Test and contribute

Unit tests (including V3 collection inventory tests):

```powershell
python -m unittest discover -v
```

`gui_reruns_test.py` and `smoke_gui.py` exercise GUI flows and require a usable graphical display.

Bug reports and contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) first. When reporting a bug, include Windows version, app version, what happened on the first and second attempts, and **redacted** report entries. Never attach private media filenames or API keys without removing them.

For local desktop smoke tests: `python smoke_gui.py`, `python gui_collections_test.py`, and `python gui_reruns_test.py` (requires a desktop display / Xvfb on Linux).

## Community roadmap

- More reliable rescan/resume logic and explicit dry-run export.
- Additional metadata provider adapters (subject to provider policies).
- Manual review and correction of ambiguous movie matches.
- Further handling of multiple genres and duplicate sets.
- Optional per-collection `Missing Movies.txt` export into the destination folder (currently manual CSV/TXT only).
- MP4 remux / Plex optimisation as a separate future feature (not yet implemented).
- Installer, digital signing, and broader Windows testing.

## Licence

[MIT](LICENSE). Contributions should be compatible with this licence.
