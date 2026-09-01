"""Writes WatchRecords to the same neutral CSV schema GenericCsvSource reads
-- the tracker-agnostic escape hatch, usable by any tool that can consume
this format, not just YAMTrack (see sources/generic_csv.py's docstring for
the column definitions).

Each `apply()` writes a fresh, complete file, not an append -- this is a
point-in-time export/backup of whatever was read from the source, not an
incrementally-merged log. Every record is always "matched": there's no
existing state in a bare CSV file to fail a lookup against, mirroring
YamtrackListsTarget's identical reasoning for Collections.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from ..models import MediaType, WatchRecord
from ..plan import ActionOutcome, MatchedAction, RestorePlan
from .base import Target

_FIELDNAMES = ["media_type", "tmdb_id", "season", "episode", "watched_at", "play_count", "title"]


class GenericCsvTarget(Target):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"generic CSV: {self.path}"

    def plan(self, records: Iterable[WatchRecord], *, force: bool = False) -> RestorePlan:
        # `force` has no meaning for a plain file target -- there's no
        # existing state being checked or regressed. Accepted only to
        # satisfy the shared Target interface.
        plan = RestorePlan(source_description="", target_description=self.describe())
        seen: set[tuple] = set()
        for record in records:
            if record.key in seen:  # dedupe: a source might yield the same item more than once
                continue
            seen.add(record.key)
            target_id = f"tmdb:{record.tmdb_id}"
            if record.media_type is MediaType.EPISODE:
                target_id += f":S{record.season}E{record.episode}"
            plan.matched.append(
                MatchedAction(record=record, target_item_id=target_id, target_title=record.title)
            )
        return plan

    def apply(self, plan: RestorePlan) -> RestorePlan:
        with self.path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()
            for action in plan.matched:
                if action.outcome is not ActionOutcome.PENDING:
                    continue
                try:
                    writer.writerow(self._row(action.record))
                    action.outcome = ActionOutcome.APPLIED
                except Exception as exc:  # noqa: BLE001 - report per-record, keep going
                    action.outcome = ActionOutcome.FAILED
                    action.error = str(exc)
        return plan

    @staticmethod
    def _row(r: WatchRecord) -> dict[str, str]:
        return {
            "media_type": r.media_type.value,
            "tmdb_id": str(r.tmdb_id),
            "season": "" if r.season is None else str(r.season),
            "episode": "" if r.episode is None else str(r.episode),
            "watched_at": r.watched_at.isoformat(),
            "play_count": str(r.play_count),
            "title": r.title or "",
        }
