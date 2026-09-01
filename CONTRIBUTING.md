# Contributing

Thanks for looking at this. The architecture is deliberately built to be
extended — adding support for another tracker, export format, or media
server should mean writing one new file, not touching existing code.

## Dev setup

```bash
git clone https://github.com/dissentingd/jellyfin-watch-restore
cd jellyfin-watch-restore
python -m venv .venv
./.venv/bin/python -m pip install -e ".[dev,yamtrack-db]"   # Linux/Mac
./.venv/Scripts/python.exe -m pip install -e ".[dev,yamtrack-db]"  # Windows

pytest
ruff check .
```

Both must pass before a PR is reviewed. CI runs both across Python 3.11–3.13.

## Adding a new watch-history source

A source reads watch history from *somewhere* and yields `WatchRecord`s. To
add one (say, a Trakt export):

1. Create `src/jellyfin_watch_restore/sources/trakt.py`.
2. Implement the `Source` interface (`sources/base.py`):
   ```python
   from .base import Source
   from ..models import WatchRecord

   class TraktSource(Source):
       def __init__(self, path: str) -> None:
           self.path = path

       def describe(self) -> str:
           return f"Trakt export: {self.path}"

       def records(self):  # -> Iterator[WatchRecord]
           # parse self.path, yield WatchRecord(...) per watched item
           ...
   ```
3. Export it from `sources/__init__.py`.
4. Wire a `--source-type` value for it in `cli.py`'s `SourceType` enum and
   `_build_source()`.
5. Write tests. Look at `tests/test_yamtrack_csv_source.py` or
   `tests/test_generic_csv_source.py` for the shape — parse a handful of
   representative rows, assert the resulting `WatchRecord` fields.

**`WatchRecord` is the whole contract.** A source's only job is turning its
input into a stream of these — it never talks to Jellyfin, never writes
anything, never needs to know what target it'll end up feeding. See
`models.py` for the exact fields and validation rules (movies vs. episodes,
season/episode requirements).

If your source reads from a *live* system rather than a file/DB dump,
`sources/jellyfin_live.py` (`JellyfinLiveSource`) is the reference — it's
what `backup` uses to read current watch state straight out of Jellyfin,
as opposed to `jellyfin_backup.py`'s file-based read of the same data.

## Adding a new target (e.g. Plex, Emby)

A target is the other end — it takes `WatchRecord`s and knows how to restore
them somewhere. This is a bigger lift than a source, but the shape is fixed
by `targets/base.py`'s two-phase contract:

```python
class Target(ABC):
    def plan(self, records, *, force=False) -> RestorePlan: ...
    def apply(self, plan: RestorePlan) -> RestorePlan: ...
```

**`plan()` must never write anything.** It matches records against the
target's current state and returns a `RestorePlan` describing what *would*
happen (matched / skipped-already-current / unmatched) — see `plan.py`.
`apply()` executes a plan's matched actions and records each one's outcome.
This split exists so the CLI's dry-run-by-default behavior isn't something
each target has to remember to implement — it's structural. A target that
skips `plan()` and writes straight from `apply()`-like logic won't be
accepted.

If you're adding a new *kind* of Jellyfin-writing target (as opposed to a
whole new media server), look at `targets/jellyfin_index.py`
(`JellyfinLibraryIndex`) first — it's the shared movie/series/episode
TMDB-id index both the watch-restore and collections-restore targets use,
so the library only gets crawled once per run. Don't build a second,
separate library-fetching path if this one already covers what you need.

Two other targets are worth reading as references before writing your own:
`targets/generic_csv.py` (`GenericCsvTarget`) is the simplest possible
target — no existing state to check, every record always matched — and
`targets/yamtrack_history.py` (`YamtrackHistoryTarget`) is the most
involved, since a single episode write touches four distinct tables
(`app_item`, used three times over, plus `app_tv`, `app_season`, and
`app_episode`) with its own find-or-create chain and a bulk-preloaded
idempotency check rather than one query per record — see its module
docstring for why the query is batched.

## Adding a new source/target for Collections

Same shape, separate ABC pair (`collections/base.py`:
`CollectionSource`/`CollectionTarget`), because it's typed around
`CollectionRecord` (a named group of movies/whole series) rather than
`WatchRecord`. See `collections/sources.py` and `collections/targets.py` for
the existing implementations as a template. `YamtrackListsTarget` is a good
reference for "writing directly to a database with no ORM/API available" —
its raw SQL was verified against YAMTrack's actual live schema (`\d
app_item`, etc.) before being written, not assumed from the Django models;
if you're touching that file, re-verify against a real instance rather than
trusting the existing queries to still be accurate on a newer YAMTrack.

## Testing conventions

- Mock the Jellyfin HTTP layer with `pytest-httpx` (`httpx_mock` fixture) —
  see `tests/test_jellyfin_target.py` / `tests/test_jellyfin_collections_target.py`
  for the pattern (a callback branching on query params, not a fixed
  response sequence, since real calls don't always arrive in a fixed order).
- For anything hitting YAMTrack's database, prefer a small in-memory fake of
  the actual tables over mocking `psycopg` directly — see
  `tests/test_yamtrack_lists_target.py`'s `FakeDb`/`FakeCursor` (3 tables) or
  `tests/test_yamtrack_history_target.py`'s (5 tables, the multi-level
  movie/tv/season/episode case) for the pattern. This caught a real bug once
  (a `startswith()` table-name collision) that a scripted mock wouldn't have.
- Every new `Target` needs at least one test proving `plan()` doesn't write
  anything and `apply()` correctly reports per-item outcomes (applied vs.
  failed), matching the existing tests' structure.

## Reporting a version-compatibility issue

This ecosystem moves fast (see the README's "Version compatibility"
section for two examples already found the hard way). If something breaks
against your Jellyfin or YAMTrack version, please include both versions in
the issue — that's the concrete, useful signal for widening what's
supported, more useful than "please fix."
