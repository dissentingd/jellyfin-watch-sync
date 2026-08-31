# jellyfin-watch-restore

Standalone open-source CLI: restores watch history into Jellyfin from a
YAMTrack export/database, a generic CSV, or an older Jellyfin backup —
matching by TMDB id so it survives files being relocated/renamed/re-organized
(the exact problem that motivated this: `music-work`'s storage-consolidation
project relocated a large share of the library, degrading Jellyfin's own
live watch-state — see `../music-work/WATCH-TRACKER-HANDOFF.md` §7/§10 for
the full backstory and how the restoration mechanism was validated before
this tool was built).

Spun out as its own public-facing project (2026-08-26) rather than a one-off
script, per Dave's explicit direction: reusable/"returnable to the community"
over a custom fix, since [no existing tool fills this gap](https://github.com/jellyfin/jellyfin/discussions/11842)
(checked before building, not assumed).

**Repo:** private for now (flip to public once proven against a real restore
run) — `gh repo view dissentingd/jellyfin-watch-restore`.

## Setup / run

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev,yamtrack-db]"   # Windows
./.venv/bin/python -m pip install -e ".[dev,yamtrack-db]"           # Linux/Mac
pytest
ruff check .
```

CLI entry point: `jellyfin-watch-restore` (see README.md for usage). Also
runnable unpackaged: `python -m jellyfin_watch_restore.cli --help`.

## Architecture

- `models.py` — `WatchRecord`, the source-agnostic seam every Source yields
  and every Target consumes.
- `sources/` — pluggable readers (YAMTrack CSV/DB, generic CSV, Jellyfin
  backup). Add a new tracker by adding one file here.
- `targets/jellyfin_client.py` — a lean `httpx` client, deliberately NOT built
  on `jellyfin-apiclient-python` (see its docstring for why) and deliberately
  NOT using `AnyProviderIdEquals` (doesn't exist in current Jellyfin — checked
  against the live OpenAPI spec). Matches by TMDB id client-side instead.
- `targets/jellyfin.py` — the matching + idempotency-safeguard logic (never
  regress an already-newer watch date; `--force` to override).
- `plan.py` — the dry-run-first data model; `cli.py`'s `restore` command
  always computes a plan and only writes with `--apply`.

## Collections (added 2026-08-26)

Same fragility, same fix, different data: Jellyfin Collections (BoxSets) also
lose integrity across file relocations, and YAMTrack's Lists feature has zero
existing import/export plumbing (confirmed by reading the actual export/import
code before building anything, same diligence as the watch-history feature).
Lives in `collections/` (yes, same name as the stdlib module — confirmed
empirically this doesn't shadow it; Python 3's absolute-import-by-default
means `jellyfin_watch_restore.collections` never collides with bare `import
collections`). Mirrors the watch-history Source/Target shape but as its own
parallel ABC pair (`CollectionSource`/`CollectionTarget`), since it's typed
around `CollectionRecord` not `WatchRecord`:

- `JellyfinCollectionsSource` / `YamtrackListsTarget` — backup direction.
- `YamtrackListsSource` / `JellyfinCollectionsTarget` — restore direction.
- Both Jellyfin-facing targets share `JellyfinLibraryIndex` (extracted from
  the original watch-restore `JellyfinTarget` during this work) so the
  library is only crawled once if both run in the same process.
- `YamtrackListsTarget`'s SQL was verified against the LIVE schema (`\d
  app_item` / `lists_customlist` / `lists_customlistitem`) before writing it
  -- in particular `app_item`'s uniqueness is three *partial* indexes keyed
  on season/episode nullness, and `lists_customlistitem`'s FKs have
  `confdeltype='a'` (NO ACTION, no cascade) -- matters if anything ever needs
  to delete a list programmatically.

Validated the same way as watch-restore: unit tests (including a from-scratch
in-memory fake of the 3 real tables for `YamtrackListsTarget`, since mocking
psycopg felt worse than just simulating the tables) + a real E2E round trip
against the live server (write a throwaway list -> read it back -> restore it
as a real Jellyfin collection -> verify -> delete both ends). Real backup
dry-run against production also confirmed the read/plan side at full scale:
370 real collections, 7,138/7,138 members resolved.

## Conventions

- Dry-run-then-confirm is load-bearing, not a suggestion — mirrors the
  discipline used throughout `music-work`'s watch-tracker work. Don't add a
  code path that writes to Jellyfin without going through `plan()` first.
- Retry-with-backoff on the Jellyfin client (`_request_with_retry`) is there
  because a real E2E dry-run against the live server hit a mid-crawl `502`
  with no retry and killed an otherwise-successful ~140k-item crawl — this
  wasn't a hypothetical, keep it when touching that file.
- `B` (a YAMTrack-side "push to media server" contribution) was scoped as a
  natural follow-on once this tool's core matching/push logic is proven — see
  the original brainstorm in `../music-work` session history if resuming that
  thread later.
