# MovieSort Pro V3 — Collections and missing-movie reports

## Default behaviour

- Automatically group franchise movies only when at least **2 distinct films** were found in selected source folders.
- Destination: `<Destination>/Collections/<Collection Name>/<Film Title (Year)>.<ext>` (using existing rename/extra-folder settings).
- Other movies continue following the current folder sorting rules.
- Preview all destinations before transfer. Copy remains the default; repeated transfers do not overwrite identical files.
- Open **Collections / Missing Movies** after scanning to see owned, missing-released, and upcoming/date-unconfirmed films. Use **Export missing list** for a CSV or TXT report.

## Metadata & limitations

- Local NFO: `<set><name>Collection Name</name><tmdbid>1234</tmdbid></set>` plus `<uniqueid type="tmdb">5678</uniqueid>` enables grouping and catalogue checking. Collection names without an ID still allow offline grouping, but the missing report cannot be verified via TMDB.
- CSV or JSON catalogue: specify `collection`, optional `collection_id`, optional `tmdb_id`, and optional integer `collection_order` (also accepts `series_order`).
- TMDB: enables lookups to `movie/{id}` and `collection/{id}`. Network lookups use the TMDB token set in the app's Metadata Sources panel, never shipped in the repository. Collection API responses are locally cached for 30 days.
- When two films are owned, only released entries absent from **all scanned folders** are marked missing. Films with unknown release dates are listed separately rather than declared missing.
- The dashboard order is explicit user-supplied collection_order first; otherwise TMDB release-date chronology, not an unsupported claim of official narrative order.
- Missing report is read-only. It does **not** fetch copyrighted movies or alter any files, and exporting is opt-in.
- Changing the minimum owned-title threshold requires re-scanning to refresh collections; re-scanning after correcting a movie match is also required.

## Check the build

```console
python -m unittest discover -v
```

On Windows run `Run-MovieSortPro.bat` to start from source. The Windows EXE requires a completed GitHub Actions build and still needs binary validation and approved provider logo work before general release.
