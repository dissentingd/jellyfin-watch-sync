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
