"""Reads Floppy's own CSV export (Settings -> Export -> CSV in the Floppy UI).

Floppy (github.com/dannyvfilms/Floppy) began as a fork of YAMTrack, and its
export keeps YAMTrack's column names (media_id, source, media_type, title,
season_number, episode_number, start_date, end_date, progressed_at, ...) but
adds a leading `row_type` column, because one file now holds several kinds of
row (checked against Floppy's own src/integrations/exports.py, not assumed):

- `media`: one tracked movie/tv/season/episode/etc. -- the only kind that
  carries a watch date.
- `list` / `list_item`: the user's custom Lists and their members (read by
  collections.sources.FloppyCsvListsSource, not here).
- collection-entry rows (owned-media formats), which this tool ignores.

As with YAMTrack, only `movie` and `episode` rows are actual watched moments
("tv"/"season" rows hold a status, not a watch), and only source=="tmdb" rows
can be matched to Jellyfin. Floppy stores a rewatch as another row, so the
same movie or episode can legitimately appear more than once; the targets
already dedupe by key.

For an episode, `media_id` is the show's TMDB id -- Floppy's exporter even
rewrites it to the show's id when a custom episode order is in effect, so it
is always safe to match on.
"""

from __future__ import annotations

import csv
import logging
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ..models import MediaType, WatchRecord
from .base import Source

logger = logging.getLogger(__name__)


def open_floppy_csv(path: Path) -> Iterator[dict[str, str]]:
    """Yields the rows of a Floppy CSV export as dicts.

    Shared by the watch-history and Lists readers. Raises the csv module's
    per-field size cap first: Floppy's export carries JSON blobs (episode
    orders, smart-list filters) that can exceed csv's 128 KiB default and
    would otherwise abort the whole read."""
    csv.field_size_limit(min(sys.maxsize, 2**31 - 1))
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or "row_type" not in reader.fieldnames:
            raise ValueError(
                f"{path} doesn't look like a Floppy CSV export (no 'row_type' column). "
                "For a YAMTrack export, use --source-type yamtrack-csv instead."
            )
        yield from reader


class FloppyCsvSource(Source):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"Floppy CSV export: {self.path}"

    def records(self) -> Iterator[WatchRecord]:
        for row in open_floppy_csv(self.path):
            if row.get("row_type", "").strip() != "media":
                continue
            record = self._row_to_record(row)
            if record is not None:
                yield record

    @staticmethod
    def _row_to_record(row: dict[str, str]) -> WatchRecord | None:
        media_type_raw = row.get("media_type", "").strip().lower()
        if media_type_raw not in ("movie", "episode"):
            return None  # tv/season/books/games/etc: not a watched moment

        if row.get("source", "").strip().lower() != "tmdb":
            logger.warning(
                "skipping %s %r: source=%r has no TMDB id to match on",
                media_type_raw, row.get("title"), row.get("source"),
            )
            return None

        # end_date is the watch itself (a movie's latest play, an episode
        # play's own date); progressed_at is only a fallback.
        when = row.get("end_date", "").strip() or row.get("progressed_at", "").strip()
        if not when:
            logger.warning("skipping %s %r: no end_date/progressed_at", media_type_raw, row.get("title"))
            return None

        media_type = MediaType(media_type_raw)
        season = row.get("season_number", "").strip()
        episode = row.get("episode_number", "").strip()
        if media_type is MediaType.EPISODE and (not season or not episode):
            logger.warning("skipping episode %r: missing season/episode number", row.get("title"))
            return None
        return WatchRecord(
            media_type=media_type,
            tmdb_id=int(row["media_id"]),
            season=int(season) if media_type is MediaType.EPISODE else None,
            episode=int(episode) if media_type is MediaType.EPISODE else None,
            watched_at=datetime.fromisoformat(when),
            title=row.get("title", "").strip() or None,
        )
