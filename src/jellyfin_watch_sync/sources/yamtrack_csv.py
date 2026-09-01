"""Reads YAMTrack's own CSV export (Settings -> Export -> CSV in the YAMTrack UI).

Header (YAMTrack's own, from integrations/exports.py -- Item fields + the union of
all track-model fields): media_id,source,media_type,title,image,season_number,
episode_number,score,status,notes,start_date,end_date,progress,created_at,progressed_at

Only "movie" and "episode" rows carry an actual watch date -- "tv" and "season"
rows are YAMTrack's own organizational scaffolding (they hold a status, not a
watched moment) and are skipped here. Only rows with source=="tmdb" can be
matched back into Jellyfin (see matching.py) -- rows from Trakt/MAL/etc. IDs are
skipped with a warning since there's no TMDB id to anchor the match on.

YAMTrack's export doesn't carry a play-count field, so every record here gets
play_count=1 -- honest given what the source actually provides, not a guess.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import MediaType, WatchRecord
from .base import Source

logger = logging.getLogger(__name__)


class YamtrackCsvSource(Source):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"YAMTrack CSV export: {self.path}"

    def records(self) -> Iterator[WatchRecord]:
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                record = self._row_to_record(row)
                if record is not None:
                    yield record

    @staticmethod
    def _row_to_record(row: dict[str, str]) -> WatchRecord | None:
        media_type_raw = row.get("media_type", "").strip().lower()
        if media_type_raw not in ("movie", "episode"):
            return None  # tv/season/etc: structural rows, not a watched moment

        if row.get("source", "").strip().lower() != "tmdb":
            logger.warning(
                "skipping %s %r: source=%r has no TMDB id to match on",
                media_type_raw, row.get("title"), row.get("source"),
            )
            return None

        when = row.get("progressed_at", "").strip() or row.get("end_date", "").strip()
        if not when:
            logger.warning("skipping %s %r: no end_date/progressed_at", media_type_raw, row.get("title"))
            return None

        media_type = MediaType(media_type_raw)
        season = row.get("season_number", "").strip()
        episode = row.get("episode_number", "").strip()
        return WatchRecord(
            media_type=media_type,
            tmdb_id=int(row["media_id"]),
            season=int(season) if media_type is MediaType.EPISODE and season else None,
            episode=int(episode) if media_type is MediaType.EPISODE and episode else None,
            watched_at=datetime.fromisoformat(when),  # 3.11+ parses a trailing "Z" natively
            title=row.get("title", "").strip() or None,
        )
