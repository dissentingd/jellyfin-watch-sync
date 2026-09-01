# jellyfin-watch-restore

Standalone open-source CLI: syncs watch history between Jellyfin and another
tracker in either direction — `restore` (into Jellyfin, from a YAMTrack
export/database, a generic CSV, or an older Jellyfin backup) and `backup`
(out of a live Jellyfin server, into YAMTrack's database or that same
generic CSV) — matching by TMDB id so it survives files being
relocated/renamed/re-organized
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
- `plan.py` — the dry-run-first data model; `cli.py`'s `restore`/`backup`
  commands always compute a plan and only write with `--apply`.
- `targets/` also has the reverse direction's write targets
  (`generic_csv.py`, `yamtrack_history.py` — see "Backup" section below),
  read by `cli.py`'s `backup` command via `sources/jellyfin_live.py`.

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
as a real Jellyfin collection -> verify -> delete both ends).

**Real production backup run, 2026-08-26 — DONE, verified:** `backup-collections
--apply` against Dave's real `seed` YAMTrack account. Tool reported
`372 collections applied, 0 failed, 7,150/7,150 members resolved`. Independently
re-verified from a fresh DB query afterward (not just trusted the tool's own
report): `372` rows in `lists_customlist` for `owner_id=4` (exact match), sample
collections spot-checked exact ("Kids TV"=200, "The Complete Criterion
Collection"=583). One discrepancy chased down rather than waved off: DB showed
`7,112` distinct list-item links vs the reported `7,150` resolved -- checked
whether any list had duplicate raw rows (`raw_links != distinct_items` per
list) and found **zero** across all 372 lists, so the gap is fully explained
by a handful of collections listing the same TMDB-matched title twice in
Jellyfin's own membership data, which `ON CONFLICT DO NOTHING` correctly
deduped to one link -- not data loss, not a bug. Only 2 collections
(`Peacock Top 10 Shows`, `The Three Stooges copy`) skipped, both genuinely
empty in Jellyfin itself. Dave's real collection library is now backed up
independent of Jellyfin. The *restore* direction is proven correct
(mocked + a throwaway real round-trip) but has NOT been run for real against
the full 372 -- only do that deliberately, not as a "why not" afterthought,
since unlike backup it writes into live Jellyfin.

## Backup: Jellyfin -> tracker (added 2026-09-01, item 8)

The reverse direction of watch-history restore. Motivated by a reasoning
gap Dave caught directly: the *original* Jellyfin->YAMTrack watch-history
backfill (the work that predates this tool entirely — see
`../music-work/WATCH-TRACKER-HANDOFF.md`) was done with one-off scripts
(`../music-work/scripts/{jf_stream,offline_jf_backup_to_yamtrack_csv,
gap_fill_compute}.py`), while Collections already had both directions
generalized into this tool from day one. This closes that asymmetry and
retires those scripts' reason for existing.

- `sources/jellyfin_live.py` (`JellyfinLiveSource`) — reads current watch
  state directly from a *live* Jellyfin server via the existing
  `JellyfinClient`, as opposed to `jellyfin_backup.py`'s file-based read of
  the same kind of data. Same two-pass series-then-episodes design as the
  backup-file source, same reason (an episode needs its series' TMDB id
  resolved, and nothing guarantees series arrive before their episodes).
- `targets/generic_csv.py` (`GenericCsvTarget`) — writes the same neutral
  CSV schema `GenericCsvSource` already reads. Deliberately tracker-agnostic
  output, not YAMTrack-specific, despite YAMTrack being the motivating
  consumer — round-trip-tested against `GenericCsvSource` itself.
- `targets/yamtrack_history.py` (`YamtrackHistoryTarget`) — writes directly
  into YAMTrack's Postgres tables. More involved than `YamtrackListsTarget`
  (Collections' equivalent): a movie is one level (`app_item` -> `app_movie`),
  but an episode is three (`app_item`(tv) -> `app_tv` -> `app_item`(season)
  -> `app_season` -> `app_item`(episode) -> `app_episode`), since YAMTrack
  tracks a show, its seasons, and its episodes as separate rows. Schema
  verified against the LIVE database before writing this, same discipline as
  Collections — including an empirical spot-check (not assumed) that an
  episode's own `app_item.media_id`, its season's, and its show's are all the
  *same* value (the show's TMDB id). Idempotency check (skip/`--force`, same
  semantics as `JellyfinTarget`) is two bulk `SELECT`s preloaded into memory,
  not one query per record — matters at real scale (100k+ records), same
  reasoning as `JellyfinLibraryIndex`'s build-once-then-resolve shape.
  A freshly-created `app_tv`/`app_season` row defaults to status `'In
  progress'`, never `'Completed'` — one played episode doesn't mean the rest
  of the show/season was watched, and a Jellyfin crawl alone can't tell.
- `cli.py`'s `backup` command mirrors `restore`; both now share a
  `_finish_watch_command()` apply-and-report tail (extracted from `restore`
  during this work, behavior-preserving — full suite stayed green through
  the refactor).
- A real bug caught by the CLI-level test, not by inspection: `backup`
  originally validated `--target-type`/`--target-path`/`--yamtrack-dsn`
  *after* doing the full live Jellyfin crawl, so a bad target arg only
  surfaced at the very end. Fixed to validate first, matching `restore`
  (which already built its source, and would hit its own `BadParameter`,
  before opening any Jellyfin connection).

**Validated 2026-09-01:** unit tests (`test_jellyfin_live_source.py`,
`test_generic_csv_target.py` with a `GenericCsvSource` round trip,
`test_yamtrack_history_target.py` with a from-scratch in-memory fake of all
5 real tables, `test_cli_backup.py`) + real E2E dry runs against Dave's live
infrastructure: `backup --target-type generic-csv` against live Jellyfin
(real titles/dates read back correctly) and `backup --target-type
yamtrack-db` against the real `seed` YAMTrack account via Docker on
PlexBox's `saltbox` network (Postgres :5432 isn't reachable from outside
PlexBox, same as everything else in that stack) — correctly distinguished
1 already-current record from 19 needing an update against the real 4,141-row
`app_movie` table. **Deliberately NOT run with `--apply` against the real
account** — a read-only proof is a fundamentally different, reversible risk
than a real write of potentially thousands of history rows into Dave's live
tracking data; that's a separate ask, same as Collections' restore direction
was held back from a full-scale real run for the identical reason.

## Next steps for general (non-Dave) usage — SCOPED 2026-08-27, items 2/3/5/6/7 DONE

Confirmed clean first: `grep` across `src/` for any Dave-specific residue
(IPs, real API keys, "productiveholdings", "seed", etc.) found **zero
matches** — every real credential used during development/testing only ever
appeared in invocations, never baked into the code. The tool itself has
always been generic.

Dave chose to proceed with items **2, 3, 5, 6, 7** below, later. **Items 2,
3, 6, 7 done 2026-08-27; item 5 done 2026-09-01** (all verified, committed,
pushed). Skipped, still not selected: item 1 (flip repo public) and item 4
(publish to PyPI) — hold until further direction.

1. ~~Flip repo public~~ — not selected, hold.
2. ~~**README credential-discovery gap.**~~ DONE — "Getting your Jellyfin
   credentials" section added to README.md.
3. ~~**Friendlier error handling on common misconfigurations.**~~ DONE —
   `errors.py`'s `describe_error()` + `cli.py`'s `_friendly_errors` decorator,
   covers 401/403/404/5xx, connect/timeout, and duck-typed psycopg errors.
   Tested (`test_errors.py`, `test_cli_errors.py`).
4. ~~Publish to PyPI~~ — not selected, hold.
5. ~~**Docker image.**~~ DONE — `Dockerfile` (python:3.12-slim, bundles
   `yamtrack-db` by default, non-root, `ENTRYPOINT` is the console script),
   `.dockerignore`, `.github/workflows/release.yml` (tests+ruff then builds
   and pushes `ghcr.io/dissentingd/jellyfin-watch-restore` on a `v*.*.*` tag,
   cuts a GitHub Release), `.env`-file support via `main()` calling
   `load_dotenv(find_dotenv(usecwd=True))` before Typer runs. Verified for
   real: built on PlexBox (`10.10.4.50`, no local Docker available),
   confirmed `--help`, non-root user, `.env` loading, and a full functional
   dry-run against live Jellyfin from inside the container (the "+1"/tmdb
   176068 test record matched correctly, 0 unmatched). No image has been
   published to `ghcr.io` yet — that only happens on the first tagged
   release, not done as part of this item.
6. ~~**Explicit version-compatibility documentation.**~~ DONE — "Version
   compatibility" section in README.md.
7. ~~**`CONTRIBUTING.md`.**~~ DONE — dev setup, how to add a Source/Target/
   Collections source-target pair, testing conventions, version-mismatch
   issue-reporting guidance.

Also noted but not yet scoped as a numbered item: Plex/Emby target support
(the `Target` ABC already anticipates this) and a `CHANGELOG.md` (premature
before a first tagged release) — real roadmap items, lower priority than
1-7 above.

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
