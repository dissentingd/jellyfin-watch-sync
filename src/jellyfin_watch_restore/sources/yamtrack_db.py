"""Reads watch history directly from YAMTrack's Postgres database.

Optional (`pip install jellyfin-watch-restore[yamtrack-db]`) -- for anyone who'd
rather point this at their live YAMTrack DB than export/re-import a CSV. Only
tmdb-sourced movies and episodes are considered, matching yamtrack_csv.py's
same TMDB-only constraint (a title from Trakt/MAL/etc. has no TMDB id to match
Jellyfin on).
"""

from __future__ import annotations

from collections.abc import Iterator

from ..models import MediaType, WatchRecord
from .base import Source

try:
    import psycopg
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "YamtrackDbSource requires the 'yamtrack-db' extra: "
        "pip install jellyfin-watch-restore[yamtrack-db]"
    ) from exc

_MOVIES_SQL = """
    SELECT i.media_id, m.end_date
    FROM app_movie m
    JOIN app_item i ON m.item_id = i.id
    WHERE m.user_id = %(user_id)s AND i.source = 'tmdb'
"""

_EPISODES_SQL = """
    SELECT si.media_id, ei.season_number, ei.episode_number, e.end_date
    FROM app_episode e
    JOIN app_item ei ON e.item_id = ei.id
    JOIN app_season s ON e.related_season_id = s.id
    JOIN app_item si ON s.item_id = si.id
    WHERE s.user_id = %(user_id)s AND si.source = 'tmdb'
"""


class YamtrackDbSource(Source):
    def __init__(self, dsn: str, user_id: int) -> None:
        """dsn: a standard Postgres connection string, e.g.
        "postgresql://yamtrack:PASSWORD@localhost:5432/yamtrack".
        user_id: the YAMTrack users_user.id whose history to read."""
        self.dsn = dsn
        self.user_id = user_id

    def describe(self) -> str:
        return f"YAMTrack DB (user_id={self.user_id})"

    def records(self) -> Iterator[WatchRecord]:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(_MOVIES_SQL, {"user_id": self.user_id})
            for tmdb_id, end_date in cur:
                if end_date is None:
                    continue
                yield WatchRecord(media_type=MediaType.MOVIE, tmdb_id=int(tmdb_id), watched_at=end_date)

            cur.execute(_EPISODES_SQL, {"user_id": self.user_id})
            for tmdb_id, season, episode, end_date in cur:
                if end_date is None:
                    continue
                yield WatchRecord(
                    media_type=MediaType.EPISODE,
                    tmdb_id=int(tmdb_id),
                    season=season,
                    episode=episode,
                    watched_at=end_date,
                )
