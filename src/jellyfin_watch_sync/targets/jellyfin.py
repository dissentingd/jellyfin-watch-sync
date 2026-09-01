"""Restores WatchRecords into a live Jellyfin server."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ..models import MediaType, WatchRecord
from ..plan import ActionOutcome, MatchedAction, RestorePlan, SkippedAlreadyCurrent, UnmatchedRecord
from .base import Target
from .jellyfin_client import JellyfinClient, LibraryItem
from .jellyfin_index import JellyfinLibraryIndex


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
    def __init__(self, client: JellyfinClient, index: JellyfinLibraryIndex | None = None) -> None:
        """`index` can be shared with another Jellyfin-writing target (e.g. a
        collections restore run in the same process) so the library is only
        fetched once. Defaults to owning a private one."""
        self.client = client
        self.index = index if index is not None else JellyfinLibraryIndex(client)

    def describe(self) -> str:
        return "Jellyfin"

    def build_index(self) -> None:
        self.index.build()

    def _resolve(self, record: WatchRecord) -> tuple[LibraryItem | None, str]:
        if record.media_type is MediaType.MOVIE:
            item = self.index.resolve_movie(record.tmdb_id)
            reason = "no movie with this TMDB id currently in the Jellyfin library"
            return item, reason

        series = self.index.resolve_series(record.tmdb_id)
        if series is None:
            return None, "no series with this TMDB id currently in the Jellyfin library"
        item = self.index.resolve_episode(record.tmdb_id, record.season, record.episode)
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
