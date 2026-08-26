# jellyfin-watch-restore

Restore watch history into Jellyfin from another source — a
[YAMTrack](https://github.com/FuzzyGrim/Yamtrack) export or database, a generic
CSV, or an older Jellyfin backup.

Jellyfin doesn't have a way to do this itself. Watch state (`Played`,
`LastPlayedDate`) lives on a library item, and library items are tied to the
physical file they were scanned from — move, rename, or replace a file (a
quality upgrade, a re-organization, a `docker` volume rebuilt from scratch) and
Jellyfin can treat the "same" movie as a brand-new item with no watch history,
even though nothing about the movie's identity actually changed. There's an
[open, unanswered discussion](https://github.com/jellyfin/jellyfin/discussions/11842)
asking for exactly this, and Jellyfin 10.11 added
[a real mitigation](https://github.com/jellyfin/jellyfin/pull/14262) for the
simplest case (remove + re-add), but it doesn't cover everything — a file
swapped for a different edition/cut still gets treated as new.

This tool works around that from the outside: it reads watch history from
wherever it's still intact, matches it to your **current** Jellyfin library by
TMDB id (which survives file moves/renames — that's the whole point), and
restores the watched flag and the original date.

## Install

```bash
pip install jellyfin-watch-restore
# or, for the direct-database YAMTrack source:
pip install "jellyfin-watch-restore[yamtrack-db]"
```

## Usage

Every run computes and prints a plan first. **Nothing is written to Jellyfin
unless you pass `--apply`.**

```bash
export JELLYFIN_URL="https://jellyfin.example.com"
export JELLYFIN_API_KEY="..."
export JELLYFIN_USER_ID="..."   # the Jellyfin user GUID to restore watch state for

# Dry run — see what would happen
jellyfin-watch-restore restore \
  --source-type yamtrack-csv --source-path ./yamtrack-export.csv

# Looks right? Apply it for real.
jellyfin-watch-restore restore \
  --source-type yamtrack-csv --source-path ./yamtrack-export.csv --apply
```

### Sources

| `--source-type` | What it reads | Extra options |
|---|---|---|
| `yamtrack-csv` | YAMTrack's own CSV export (Settings → Export) | `--source-path` |
| `yamtrack-db` | YAMTrack's Postgres database directly | `--source-dsn`, `--yamtrack-user-id` (needs the `yamtrack-db` extra) |
| `jellyfin-backup` | An older Jellyfin native backup (`.zip` or already-extracted dir) — restore from *before* a relocation/reorg damaged live watch state | `--source-path`, `--username` |
| `generic-csv` | A simple hand-producible CSV — the escape hatch for any other tracker | `--source-path` |

The generic CSV format:

```csv
media_type,tmdb_id,season,episode,watched_at,play_count,title
movie,68737,,,2019-03-12T23:32:00,1,Seventh Son
episode,111111,1,1,2025-09-28,,Some Show S1E1
```

### Safety

- `--apply` is required to write anything; without it you only get the plan.
- A record is **skipped by default** if Jellyfin's current watched date for
  that item is already at least as recent as what you're restoring — this
  tool only fills gaps, it never regresses a legitimate newer watch. Pass
  `--force` to override.
- A record with no matching item currently in your Jellyfin library is
  reported as **unmatched**, not silently dropped. TMDB-id matching survives
  a file being moved or renamed; it can't survive the title being removed
  from the library outright.

## Why TMDB-id matching, not Jellyfin's own item id

Jellyfin's `/Items` endpoint has no working "find by provider id" filter in
current versions (checked against the live OpenAPI spec — `AnyProviderIdEquals`
doesn't exist there, despite older tools/docs assuming it does). This tool
fetches the library with `Fields=ProviderIds` and matches client-side instead
— proven to work at real scale (500k+ item libraries).

## Development

```bash
pip install -e ".[dev,yamtrack-db]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
