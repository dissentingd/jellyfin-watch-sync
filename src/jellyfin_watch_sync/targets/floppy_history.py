"""Writes WatchRecords into Floppy through its REST API -- the Floppy
counterpart to YamtrackHistoryTarget, used by `backup --target-type
floppy-api`. See floppy_client.py for why this goes through the API rather
than Floppy's database.

Floppy keeps every play as its own row, and its watch endpoints *append* a
play rather than overwrite a date. Two consequences shape this target:

- Planning reads the user's existing Floppy history once (one paged crawl,
  not a query per record -- same build-once shape as JellyfinLibraryIndex)
  and skips any record Floppy already has a watch for at the same or a later
  date. That's this tool's usual "never regress" rule, and here it also keeps
  a play that Floppy's own Jellyfin sync already recorded from being added a
  second time. `--force` writes anyway.
- "The same date" is deliberately loose: a Floppy watch covers any record
  less than a day after it. Watches that came into Floppy from a tracker
  import often kept only the date (stored as local midnight), so the exact
  same viewing shows up in Jellyfin as a few hours *later* the same day.
  Found against a real migrated library, where a strict comparison would
  have added a second play for ~all of 4,000+ movies. The cost is that a
  genuine rewatch within 24 hours of an earlier one isn't added.
- Every write carries a deterministic `external_id` derived from the record
  (TMDB id, season/episode, watch time). Floppy treats a repeated
  external_id as the same play, so re-running a backup, or an apply that
  died halfway, never stacks up duplicate plays -- even with `--force`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from ..models import MediaType, WatchRecord
from ..plan import ActionOutcome, MatchedAction, RestorePlan, SkippedAlreadyCurrent
from .base import Target
from .floppy_client import FloppyClient

EXTERNAL_ID_PREFIX = "jellyfin-watch-sync"
# A Floppy watch counts as the same viewing as a record up to this much later
# (see the module docstring: imported dates often lost their time of day).
SAME_WATCH_WINDOW = timedelta(days=1)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def external_id_for(record: WatchRecord) -> str:
    when = _as_aware(record.watched_at).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    if record.media_type is MediaType.EPISODE:
        return f"{EXTERNAL_ID_PREFIX}:tmdb:{record.tmdb_id}:s{record.season}e{record.episode}:{when}"
    return f"{EXTERNAL_ID_PREFIX}:tmdb:{record.tmdb_id}:{when}"


class FloppyHistoryTarget(Target):
    def __init__(self, client: FloppyClient) -> None:
        self.client = client

    def describe(self) -> str:
        return f"Floppy ({self.client.base_url})"

    def plan(self, records: Iterable[WatchRecord], *, force: bool = False) -> RestorePlan:
        latest: dict[tuple, datetime] = {}
        for entry in self.client.history():
            if entry.media_type == "episode":
                key = (MediaType.EPISODE, entry.tmdb_id, entry.season, entry.episode)
            else:
                key = (MediaType.MOVIE, entry.tmdb_id)
            when = _as_aware(entry.watched_at)
            if key not in latest or when > latest[key]:
                latest[key] = when

        plan = RestorePlan(source_description="", target_description=self.describe())
        seen: set[tuple] = set()
        for record in records:
            if record.key in seen:
                continue
            seen.add(record.key)

            target_id = external_id_for(record)
            existing = latest.get(record.key)
            if not force and existing is not None and existing + SAME_WATCH_WINDOW > _as_aware(record.watched_at):
                plan.skipped_already_current.append(
                    SkippedAlreadyCurrent(record=record, target_item_id=target_id, current_watched_at=str(existing))
                )
                continue
            plan.matched.append(MatchedAction(record=record, target_item_id=target_id, target_title=record.title))
        return plan

    def apply(self, plan: RestorePlan) -> RestorePlan:
        for action in plan.matched:
            if action.outcome is not ActionOutcome.PENDING:
                continue
            record = action.record
            try:
                if record.media_type is MediaType.MOVIE:
                    self.client.watch_movie(record.tmdb_id, record.watched_at, action.target_item_id)
                else:
                    self.client.watch_episode(
                        record.tmdb_id, record.season, record.episode, record.watched_at, action.target_item_id,
                    )
                action.outcome = ActionOutcome.APPLIED
            except Exception as exc:  # noqa: BLE001 - report per-record, keep going
                action.outcome = ActionOutcome.FAILED
                action.error = str(exc)
        return plan
