"""Generic CSV source: the escape hatch for anything not covered by a dedicated
source. Anyone can hand-produce this format from their own tracker/spreadsheet.

Expected columns (header required, extra columns ignored):
    media_type   "movie" or "episode"
    tmdb_id      the movie's TMDB id, or the SERIES' TMDB id for an episode
    season       required for episode rows, blank for movies
    episode      required for episode rows, blank for movies
    watched_at   ISO 8601 datetime (date-only is accepted, treated as midnight UTC)
    play_count   optional, defaults to 1
    title        optional, informational only
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import MediaType, WatchRecord
from .base import Source


class GenericCsvSource(Source):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"generic CSV: {self.path}"

    def records(self) -> Iterator[WatchRecord]:
        with self.path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for lineno, row in enumerate(reader, start=2):  # header is line 1
                try:
                    yield self._row_to_record(row)
                except (KeyError, ValueError) as exc:
                    raise ValueError(f"{self.path}:{lineno}: {exc}") from exc

    @staticmethod
    def _row_to_record(row: dict[str, str]) -> WatchRecord:
        media_type = MediaType(row["media_type"].strip().lower())
        season = row.get("season", "").strip()
        episode = row.get("episode", "").strip()
        return WatchRecord(
            media_type=media_type,
            tmdb_id=int(row["tmdb_id"]),
            season=int(season) if season else None,
            episode=int(episode) if episode else None,
            watched_at=datetime.fromisoformat(row["watched_at"].strip()),
            play_count=int(row["play_count"]) if row.get("play_count", "").strip() else 1,
            title=row.get("title", "").strip() or None,
        )
