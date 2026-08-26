"""Restores WatchRecords into a live Jellyfin server."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ..models import MediaType, WatchRecord
from ..plan import ActionOutcome, MatchedAction, RestorePlan, SkippedAlreadyCurrent, UnmatchedRecord
from .base import Target
from .jellyfin_client import JellyfinClient, LibraryItem


def _parse_jellyfin_date(value: str | None) -> datetime | None:
    if not value:
        return None
    # Jellyfin emits e.g. "2019-03-12T23:32:00.0000000Z" -- trim to microsecond
    # precision (datetime.fromisoformat can't take 7 fractional digits) before parsing.
    if "." in value:
        head, _, tail = value.partition(".")
        frac = tail.rstrip("Z")[:6].ljust(6, "0")
        value = f"{head}.{frac}+00:00"
    else:
        value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class JellyfinTarget(Target):
    def __init__(self, client: JellyfinClient) -> None:
        self.client = client
        self._index_built = False
        self._movies_by_tmdb: dict[str, LibraryItem] = {}
        self._series_tmdb_to_item_id: dict[str, str] = {}
        self._episodes_by_key: dict[tuple[str, int, int], LibraryItem] = {}

    def describe(self) -> str:
        return "Jellyfin"

    def build_index(self) -> None:
        """Fetches the current library once. Safe to call repeatedly; only
        does the work the first time."""
        if self._index_built:
            return

        for item in self.client.movies():
            if item.tmdb_id:
                self._movies_by_tmdb[item.tmdb_id] = item

        for item in self.client.series():
            if item.tmdb_id:
                self._series_tmdb_to_item_id[item.tmdb_id] = item.item_id

        for item in self.client.episodes():
            if item.series_id is None or item.season_number is None or item.episode_number is None:
                continue
            self._episodes_by_key[(item.series_id, item.season_number, item.episode_number)] = item

        self._index_built = True

    def _resolve(self, record: WatchRecord) -> tuple[LibraryItem | None, str]:
        if record.media_type is MediaType.MOVIE:
            item = self._movies_by_tmdb.get(str(record.tmdb_id))
            reason = "no movie with this TMDB id currently in the Jellyfin library"
            return item, reason

        series_item_id = self._series_tmdb_to_item_id.get(str(record.tmdb_id))
        if series_item_id is None:
            return None, "no series with this TMDB id currently in the Jellyfin library"
        item = self._episodes_by_key.get((series_item_id, record.season, record.episode))
        reason = f"series found, but S{record.season}E{record.episode} isn't in the library"
        return item, reason

    def plan(self, records: Iterable[WatchRecord], *, force: bool = False) -> RestorePlan:
        self.build_index()
        plan = RestorePlan(source_description="", target_description=self.describe())

        seen: set[tuple] = set()
        for record in records:
            if record.key in seen:
                continue  # dedupe: a source might yield the same item more than once
            seen.add(record.key)

            item, reason = self._resolve(record)
            if item is None:
                plan.unmatched.append(UnmatchedRecord(record=record, reason=reason))
                continue

            if not force and item.played:
                current = _parse_jellyfin_date(item.last_played_date)
                if current is not None and current >= _as_aware(record.watched_at):
                    plan.skipped_already_current.append(
                        SkippedAlreadyCurrent(
                            record=record, target_item_id=item.item_id,
                            current_watched_at=item.last_played_date or "",
                        )
                    )
                    continue

            plan.matched.append(
                MatchedAction(record=record, target_item_id=item.item_id, target_title=item.name)
            )
        return plan

    def apply(self, plan: RestorePlan) -> RestorePlan:
        for action in plan.matched:
            if action.outcome is not ActionOutcome.PENDING:
                continue
            try:
                self.client.mark_played(action.target_item_id, action.record.watched_at)
                action.outcome = ActionOutcome.APPLIED
            except Exception as exc:  # noqa: BLE001 - report per-item, keep going
                action.outcome = ActionOutcome.FAILED
                action.error = str(exc)
        return plan
