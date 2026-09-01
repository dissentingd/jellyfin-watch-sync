"""The movie/series/episode TMDB-id index, shared by every Jellyfin-writing
target (watch-restore, collections-restore) so the library is only crawled
once per run and the resolution logic lives in exactly one place."""

from __future__ import annotations

from .jellyfin_client import JellyfinClient, LibraryItem


class JellyfinLibraryIndex:
    def __init__(self, client: JellyfinClient) -> None:
        self.client = client
        self._built = False
        self.movies_by_tmdb: dict[str, LibraryItem] = {}
        self.series_by_tmdb: dict[str, LibraryItem] = {}
        self.episodes_by_key: dict[tuple[str, int, int], LibraryItem] = {}

    def build(self) -> None:
        """Fetches the current library once. Safe to call repeatedly; only
        does the work the first time."""
        if self._built:
            return

        for item in self.client.movies():
            if item.tmdb_id:
                self.movies_by_tmdb[item.tmdb_id] = item

        for item in self.client.series():
            if item.tmdb_id:
                self.series_by_tmdb[item.tmdb_id] = item

        for item in self.client.episodes():
            if item.series_id is None or item.season_number is None or item.episode_number is None:
                continue
            self.episodes_by_key[(item.series_id, item.season_number, item.episode_number)] = item

        self._built = True

    def resolve_movie(self, tmdb_id: int) -> LibraryItem | None:
        return self.movies_by_tmdb.get(str(tmdb_id))

    def resolve_series(self, tmdb_id: int) -> LibraryItem | None:
        return self.series_by_tmdb.get(str(tmdb_id))

    def resolve_episode(self, series_tmdb_id: int, season: int, episode: int) -> LibraryItem | None:
        series = self.resolve_series(series_tmdb_id)
        if series is None:
            return None
        return self.episodes_by_key.get((series.item_id, season, episode))
