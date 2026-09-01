"""Reads current watch state directly from a *live* Jellyfin server -- the
counterpart to JellyfinBackupSource's file-based read, used for the `backup`
direction (Jellyfin -> another tracker) rather than `restore` (another
tracker -> Jellyfin).

Uses the same JellyfinClient the restore-side targets already use, just as a
data source instead of something records get matched against.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..models import MediaType, WatchRecord
from ..targets.jellyfin_client import JellyfinClient
from .base import Source


class JellyfinLiveSource(Source):
    def __init__(self, client: JellyfinClient) -> None:
        self.client = client

    def describe(self) -> str:
        return "Jellyfin (live)"

    def records(self) -> Iterator[WatchRecord]:
        # Series first, same reasoning as JellyfinBackupSource's two-pass
        # design: an episode's own WatchRecord needs its *series'* TMDB id,
        # so the full series->tmdb map has to exist before any episode is
        # considered -- fetching series() up front guarantees that,
        # regardless of what order the API would otherwise imply.
        series_tmdb: dict[str, str] = {}
        for series in self.client.series():
            if series.tmdb_id:
                series_tmdb[series.item_id] = series.tmdb_id

        for movie in self.client.movies():
            if not (movie.played and movie.last_played_date and movie.tmdb_id):
                continue
            yield WatchRecord(
                media_type=MediaType.MOVIE,
                tmdb_id=int(movie.tmdb_id),
                watched_at=movie.last_played_date,
                play_count=movie.play_count,
                title=movie.name,
            )

        for ep in self.client.episodes():
            if not (ep.played and ep.last_played_date):
                continue
            tid = series_tmdb.get(ep.series_id or "")
            if not tid or ep.season_number is None or ep.episode_number is None:
                continue
            yield WatchRecord(
                media_type=MediaType.EPISODE,
                tmdb_id=int(tid),
                season=ep.season_number,
                episode=ep.episode_number,
                watched_at=ep.last_played_date,
                play_count=ep.play_count,
                title=ep.name,
            )
