# jellyfin-watch-sync

**Reorganized your media library and now Jellyfin thinks you've never seen
any of it?** This tool fixes that — it syncs your watch history (and
Collections) between Jellyfin and [YAMTrack](https://github.com/FuzzyGrim/Yamtrack),
in either direction, so a "watched" movie stays watched even after you move,
rename, or re-encode the file.

You don't need to know Python, SQL, or how Jellyfin's API works to use this.
If you can run a Docker container and copy a couple of values off a
settings page, you can use this tool. (If you *do* know all that stuff,
there's plenty of technical detail further down for you too.)

**Prefer clicking buttons in a browser to typing commands?** See
[jellyfin-watch-sync-xyops](https://github.com/dissentingd/jellyfin-watch-sync-xyops) —
this same tool, pre-installed inside a web-based job runner with a login
and four ready-to-click jobs, no command line required.

## The problem this solves

Jellyfin remembers what you've watched by attaching that information to a
specific file on disk. That works fine — until you reorganize your library,
upgrade a movie to a better-quality version, or rebuild a Docker volume from
scratch. The moment the file changes, Jellyfin can decide it's looking at a
brand-new movie with no history, even though it's the exact same title you
watched last year. All your "watched" checkmarks just vanish.

This isn't a bug you did something wrong to cause — it's a real,
[still-unanswered gap](https://github.com/jellyfin/jellyfin/discussions/11842)
in how Jellyfin works. (Jellyfin 10.11 did add
[a partial fix](https://github.com/jellyfin/jellyfin/pull/14262) for the
simplest case, but plenty of real-world reorganizations still fall through
the cracks.)

**How this tool gets around it:** instead of relying on the file, it
identifies every movie and episode by its TMDB ID — a permanent ID from
[The Movie Database](https://www.themoviedb.org/) that never changes no
matter what you rename or move the file to. It reads your watch history
from wherever it's still intact (an older backup, a YAMTrack export,
YAMTrack's own database) and writes it back onto whatever your Jellyfin
library currently calls that same movie or episode — by TMDB ID, not by
filename.

## Is this safe to run?

Yes, by design:

- **It never touches anything on the first run.** Every command shows you
  exactly what it *would* do — which movies and episodes, dated when — and
  changes nothing until you explicitly add `--apply`.
- **It never overwrites a newer "watched" date with an older one.** If
  Jellyfin already shows a more recent watch than what you're restoring,
  that item is skipped automatically.
- **It never deletes anything.** Worst case if something goes wrong: a
  watch date doesn't get set. It can't make your library disappear or wipe
  out data that's already there.

## Quick start (Docker)

The fastest path if you just want to try it:

```bash
# 1. Build the image (a published one is coming with the first tagged release)
git clone https://github.com/dissentingd/jellyfin-watch-sync
cd jellyfin-watch-sync
docker build -t jellyfin-watch-sync .

# 2. Create a .env file with your Jellyfin details (see below for how to find these)
cat > .env <<EOF
JELLYFIN_URL=https://jellyfin.example.com
JELLYFIN_API_KEY=your-api-key-here
JELLYFIN_USER_ID=your-user-guid-here
EOF

# 3. See what a restore from a YAMTrack export would do (nothing is written yet)
docker run --rm \
  -v "$(pwd)/.env:/app/.env:ro" \
  -v "$(pwd)/yamtrack-export.csv:/app/history.csv:ro" \
  jellyfin-watch-sync restore \
  --source-type yamtrack-csv --source-path /app/history.csv
```

If the plan it prints looks right, re-run the same command with `--apply`
added at the end. That's the whole workflow — look, then confirm.

## Install

Prefer to install it directly rather than use Docker? It's a regular Python
package:

```bash
pip install jellyfin-watch-sync
# or, if you want to connect straight to YAMTrack's database instead of a CSV export:
pip install "jellyfin-watch-sync[yamtrack-db]"
```

### Docker, in more detail

```bash
docker build -t jellyfin-watch-sync .
```

The image runs as a non-root user and includes everything needed for the
direct-database YAMTrack option by default. `docker run jellyfin-watch-sync
--help` behaves the same as running the tool without Docker at all.

Credentials go in a `.env` file (shown in the quick start above) rather than
long command-line flags — the file must be mounted at **`/app/.env`**
specifically, since that's where the tool looks for it inside the
container. Passing values with `docker run -e` works too, and takes
priority over the `.env` file if both are present.

If your Jellyfin server also runs in Docker and isn't reachable at a normal
URL from other containers, add `--network <the network Jellyfin is on>` to
the `docker run` command.

## Usage

Every command computes and prints a plan first, no matter what. **Nothing
is written anywhere until you add `--apply`.**

### Getting your Jellyfin credentials

Three things, all found on your own Jellyfin server's admin pages:

- **`JELLYFIN_URL`** — your server's web address, e.g.
  `https://jellyfin.example.com` or `http://localhost:8096`. No trailing
  slash.
- **`JELLYFIN_API_KEY`** — a key that lets this tool talk to your server on
  your behalf. Create one at Dashboard → **Advanced → API Keys** → the `+`
  button. Any key works; it doesn't need to be tied to a specific user.
- **`JELLYFIN_USER_ID`** — identifies *which user's* watch history you're
  working with (Jellyfin servers can have multiple accounts). Two ways to
  find it:
  - Dashboard → **Users** → click your user → the ID is the last part of
    the page's web address (`.../userprofile.html?userId=<THIS PART>`).
  - Or, if you're comfortable with a terminal: `curl -s
    https://jellyfin.example.com/Users -H 'Authorization: MediaBrowser
    Token="<your API key>"' | jq '.[] | {Name, Id}'` lists every user on
    the server with their ID.

Once you have these three values, put them in a `.env` file (Docker) or
export them as environment variables (installed CLI) rather than typing
them as command-line flags each time — flags are visible to other programs
running on the same machine and get saved in your shell's command history,
which you don't want for an API key.

```bash
export JELLYFIN_URL="https://jellyfin.example.com"
export JELLYFIN_API_KEY="..."
export JELLYFIN_USER_ID="..."

# Dry run — see what would happen, nothing is written
jellyfin-watch-sync restore \
  --source-type yamtrack-csv --source-path ./yamtrack-export.csv

# Looks right? Add --apply to actually do it
jellyfin-watch-sync restore \
  --source-type yamtrack-csv --source-path ./yamtrack-export.csv --apply
```

### Where `restore` can read watch history from

| `--source-type` | What it reads | Extra options |
|---|---|---|
| `yamtrack-csv` | YAMTrack's own CSV export (Settings → Export) | `--source-path` |
| `yamtrack-db` | YAMTrack's Postgres database directly | `--source-dsn`, `--yamtrack-user-id` (needs the `yamtrack-db` extra) |
| `jellyfin-backup` | An older Jellyfin native backup (`.zip` or already-extracted folder) — useful when a reorganization damaged your *current* Jellyfin's watch state, but an older backup still has it intact | `--source-path`, `--username` |
| `generic-csv` | A simple, hand-editable CSV — the fallback if your tracker isn't one of the above | `--source-path` |

The generic CSV format, if you need to build one by hand or export it from
somewhere this tool doesn't support directly:

```csv
media_type,tmdb_id,season,episode,watched_at,play_count,title
movie,68737,,,2019-03-12T23:32:00,1,Seventh Son
episode,111111,1,1,2025-09-28,,Some Show S1E1
```

### Going the other way: backing watch history *out* of Jellyfin

`backup` is the mirror image of `restore` — instead of reading from
somewhere else and writing into Jellyfin, it reads your *current* Jellyfin
watch history and writes it out to YAMTrack or a CSV file. Same
look-then-confirm safety model.

```bash
# Save everything Jellyfin currently has marked watched into a CSV file
jellyfin-watch-sync backup \
  --target-type generic-csv --target-path ./jellyfin-watched.csv --apply

# Or write it straight into YAMTrack's database
jellyfin-watch-sync backup \
  --target-type yamtrack-db \
  --yamtrack-dsn "postgresql://yamtrack:...@host:5432/yamtrack" \
  --yamtrack-user-id 4 --apply
```

| `--target-type` | What it writes | Extra options |
|---|---|---|
| `generic-csv` | The same plain CSV format described above — readable by any tool, not just this one | `--target-path` |
| `yamtrack-db` | Directly into YAMTrack's Postgres database (needs the `yamtrack-db` extra) | `--yamtrack-dsn`, `--yamtrack-user-id` |

`yamtrack-db` has the same "don't overwrite something newer" protection
described below. `generic-csv` always writes a complete, fresh file each
time — think of it as a snapshot, not something that merges with a
previous export.

### Safety details

- `--apply` is required to write anything, always. Without it, you only
  ever get a printed plan.
- A record is **skipped automatically** if the target already shows a
  watched date that's the same age or newer than what you're about to
  write — this tool only ever fills in gaps, it never turns back the
  clock on a legitimate, more recent watch. Add `--force` if you really do
  want to overwrite it anyway. (This protection doesn't apply to
  `--target-type generic-csv`, which has no prior state to compare
  against.)
- For `restore`: if a record doesn't match anything currently in your
  Jellyfin library, it's reported as **unmatched** — you'll see it listed,
  it's never silently skipped. Matching by TMDB ID survives the file being
  moved or renamed, but not the title being removed from your library
  entirely. (`backup` doesn't have this problem — writing into YAMTrack or
  a CSV can always create whatever it needs.)

## Collections (Jellyfin BoxSets)

Collections — the groupings you build in Jellyfin, like "Marvel Cinematic
Universe" or "Christmas Movies" — suffer from the exact same
file-relocation fragility as watch history. So this tool backs those up
into YAMTrack's Lists feature too, and can restore them back the same way.
A collection groups movies and/or whole TV series (not individual episodes
— that's the level of detail Jellyfin itself uses for collections).

```bash
# Back up all your Jellyfin collections into YAMTrack Lists
jellyfin-watch-sync backup-collections \
  --yamtrack-dsn "postgresql://yamtrack:...@host:5432/yamtrack" \
  --yamtrack-user-id 4 --apply

# ... later, if Jellyfin's collections got damaged: restore them back
jellyfin-watch-sync restore-collections \
  --yamtrack-dsn "postgresql://yamtrack:...@host:5432/yamtrack" \
  --yamtrack-user-id 4 --apply
```

Restoring is never destructive: if a collection with the same name already
exists in Jellyfin, this only ever adds new members to it — it never
removes or replaces anything already there. Requires the `yamtrack-db`
extra either way, since YAMTrack has no CSV export for Lists (or for watch
history) the way it does for other data.

## If something isn't working: version compatibility

Built and tested against **Jellyfin 10.11.11** and **YAMTrack** (the
`ghcr.io/fuzzygrim/yamtrack:latest` image as of 2026-08). Both of these
projects move fast, and this tool depends on some specifics that have
genuinely changed between versions — confirmed firsthand, not assumed from
documentation that might be out of date. If you're on a noticeably
different version and something breaks, it's likely one of these known
trouble spots, not a sign the tool is broadly broken:

- **Jellyfin's `/Items` API has no working "find by provider ID" filter**
  in 10.11.x, even though some other tools and older docs assume it
  exists. This tool never relies on that filter in the first place (see
  the technical note below for why), so it isn't affected by the gap
  either way — but it's a real, confirmed difference worth knowing about
  if you're comparing tools.
- **Jellyfin's Webhook plugin calls its manual "mark played" event
  `UserDataSaved`, not `MarkPlayed`** — only relevant if you're also
  setting up a separate webhook-based sync elsewhere, not to this tool's
  own operation. Flagged here because it's a genuinely confusing gotcha in
  this ecosystem that tripped up this project's own earlier work.
- **YAMTrack's database structure** is what the `yamtrack-db` option and
  the `*-collections` commands rely on directly, checked against a real,
  live install rather than assumed from YAMTrack's source code alone.
  YAMTrack doesn't publish a stable, versioned API for this data, so a
  future update to it could change something without warning. If a
  `yamtrack-db` or `*-collections` command fails with a raw database
  error, check whether your YAMTrack version's structure still matches
  before assuming this tool itself is broken.

Hit a version-specific problem? Please open an issue with your Jellyfin and
YAMTrack versions — that's genuinely useful for widening what's supported,
not noise.

<details>
<summary>Technical note: why TMDB ID matching instead of Jellyfin's own internal item ID</summary>

Jellyfin's `/Items` endpoint has no working "find by provider ID" filter in
current versions (checked directly against the live OpenAPI spec —
`AnyProviderIdEquals` doesn't exist there, despite some older tools and
docs assuming it does). This tool fetches the whole library with
`Fields=ProviderIds` and matches against TMDB IDs client-side instead —
proven to work at real scale, on libraries with 500,000+ items.

</details>

## Development

Want to add support for another tracker, another export format, or another
media server? See [CONTRIBUTING.md](CONTRIBUTING.md) — the architecture is
deliberately built so that means writing one new file, not modifying
existing code.

```bash
pip install -e ".[dev,yamtrack-db]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
