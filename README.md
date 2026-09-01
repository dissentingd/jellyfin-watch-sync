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

### Docker

A published image is planned at `ghcr.io/dissentingd/jellyfin-watch-restore`
once this repo has its first tagged release; for now, build it locally from
a checkout:

```bash
docker build -t jellyfin-watch-restore .
```

The image bundles the `yamtrack-db` extra by default, runs as a non-root
user, and is invoked the same way as the installed CLI — `docker run
jellyfin-watch-restore --help` behaves the same as running the tool bare.

Credentials via `.env` file (recommended for Docker/compose over long
`--jellyfin-*` flag lists — see [Getting your Jellyfin
credentials](#getting-your-jellyfin-credentials) below for what goes in it):

```bash
docker run --rm \
  -v "$(pwd)/.env:/app/.env:ro" \
  -v "$(pwd)/yamtrack-export.csv:/app/history.csv:ro" \
  jellyfin-watch-restore restore \
  --source-type yamtrack-csv --source-path /app/history.csv --apply
```

The `.env` file must be mounted at **`/app/.env`** — that's the image's
`WORKDIR`, which is where the entry point looks for it. Environment
variables passed directly with `docker run -e` work too and take the usual
precedence over a `.env` file.

If your Jellyfin server is only reachable by its Docker network alias (e.g.
you're also running Jellyfin in Docker), add `--network <that network>` to
the `docker run` command so the container can resolve it.

## Usage

Every run computes and prints a plan first. **Nothing is written to Jellyfin
unless you pass `--apply`.**

### Getting your Jellyfin credentials

- **`JELLYFIN_URL`** — your server's base URL, e.g. `https://jellyfin.example.com`
  or `http://localhost:8096`. No trailing path or slash.
- **`JELLYFIN_API_KEY`** — Dashboard → **Advanced → API Keys** → the `+` button
  to create a new one. Any key with server access works; this tool doesn't
  need it scoped to a specific user.
- **`JELLYFIN_USER_ID`** — the GUID of the *user* whose watch history you're
  restoring (not necessarily the account the API key belongs to). Two ways
  to find it:
  - Dashboard → **Users** → click the user → the GUID is the last path
    segment of the page's URL (`.../userprofile.html?userId=<THIS PART>`).
  - Or query it directly: `curl -s https://jellyfin.example.com/Users \
    -H 'Authorization: MediaBrowser Token="<your API key>"' | jq '.[] | {Name, Id}'`
    — lists every user with their `Id`.

Prefer environment variables over the equivalent `--jellyfin-*` CLI flags for
these, especially the API key — CLI arguments are visible to other processes
on the same machine (`ps`) and land in your shell history; env vars don't.

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

## Version compatibility

Built and validated against **Jellyfin 10.11.11** and **YAMTrack** (the
`ghcr.io/fuzzygrim/yamtrack:latest` image as of 2026-08). This ecosystem
moves fast and this tool leans on specifics that have genuinely changed
between versions, confirmed firsthand rather than assumed from older
docs — if you're on a materially different version, expect to hit one of
these:

- **Jellyfin's `/Items` endpoint has no working `AnyProviderIdEquals`
  filter** in 10.11.x, despite other tools (and older Jellyfin docs)
  assuming it exists — confirmed missing from the live `/api-docs/openapi.json`
  parameter list. This tool never relies on it (see below), so it isn't
  affected by that gap either way, but it's worth knowing if you're
  comparing this tool's approach to another one's.
- **Jellyfin's Webhook plugin's manual "mark played" event is named
  `UserDataSaved`, not `MarkPlayed`** — relevant only if you're pairing
  this tool with a webhook-based live sync elsewhere, not to this tool's
  own operation, but a real, confusing gotcha in this same ecosystem worth
  flagging here since it tripped up this project's own earlier work.
- **YAMTrack's database schema** (`app_item`, `lists_customlist`,
  `lists_customlistitem`) is what `--source-type yamtrack-db` and the
  `*-collections` commands depend on directly, verified against a live
  install's actual `\d` output rather than assumed from YAMTrack's Django
  models alone (see `CLAUDE.md`'s Collections section for the specific
  constraints that mattered). YAMTrack has no versioned/stable public API
  for this data — a future schema migration could change column names or
  constraints without notice. If a `yamtrack-db` or `*-collections` command
  fails with a raw SQL error, check whether your YAMTrack version's schema
  still matches before assuming this tool is broken.

If you hit a version-specific failure, please open an issue with your
Jellyfin/YAMTrack versions — that's real, useful signal for widening
compatibility, not noise.

## Why TMDB-id matching, not Jellyfin's own item id

Jellyfin's `/Items` endpoint has no working "find by provider id" filter in
current versions (checked against the live OpenAPI spec — `AnyProviderIdEquals`
doesn't exist there, despite older tools/docs assuming it does). This tool
fetches the library with `Fields=ProviderIds` and matches client-side instead
— proven to work at real scale (500k+ item libraries).

## Collections (Jellyfin BoxSets)

Jellyfin Collections suffer the same file-relocation fragility as watch
history, so this tool also backs them up into YAMTrack's Lists feature and
restores them back — same TMDB-id matching, same dry-run-first discipline.
A collection groups movies and/or whole series (not individual episodes --
that's the granularity Jellyfin itself uses).

```bash
# Back up all your Jellyfin collections into YAMTrack Lists
jellyfin-watch-restore backup-collections \
  --yamtrack-dsn "postgresql://yamtrack:...@host:5432/yamtrack" \
  --yamtrack-user-id 4 --apply

# ... later, if Jellyfin's collections got damaged: restore them back
jellyfin-watch-restore restore-collections \
  --yamtrack-dsn "postgresql://yamtrack:...@host:5432/yamtrack" \
  --yamtrack-user-id 4 --apply
```

Restoring is never destructive: a collection with a matching name already in
Jellyfin is only ever added to (new members merged in), never replaced or
pruned. Requires the `yamtrack-db` extra (direct Postgres access -- YAMTrack
has no API/CSV path for Lists, same as it has none for watch history).

## Development

```bash
pip install -e ".[dev,yamtrack-db]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
