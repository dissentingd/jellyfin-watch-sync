"""Writes WatchRecords into YAMTrack's database -- the write-side counterpart
to YamtrackDbSource's read. Used by the `backup` command (Jellyfin ->
YAMTrack) to turn a live Jellyfin crawl into real YAMTrack watch-history
rows, replacing the manual "export a CSV, re-import it by hand" round trip
that predates this tool (see CLAUDE.md's general-usage backlog, item 8, for
the one-off scripts this generalizes and retires).

Schema verified against the LIVE database before writing this (`\\d app_item`
/ `app_movie` / `app_tv` / `app_season` / `app_episode`, plus a spot-check
that an episode's own app_item row, its season's, and its show's all carry
the *same* media_id -- the show's TMDB id), not assumed from YAMTrack's
Django models -- same discipline as YamtrackListsTarget for Collections.

Movies are one level: app_item -> app_movie. Episodes are three, because
YAMTrack tracks a show, its seasons, and its episodes as separate rows
(unlike Collections, which only ever needed a single app_item per member):

    app_item(tv) -> app_tv -> app_item(season) -> app_season
                                                        |
                              app_item(episode) -> app_episode

A freshly-created show/season defaults to status 'In progress', never
'Completed' -- a single episode being played doesn't mean the rest of the
show or season has been watched, and this tool has no way to know that from
a Jellyfin crawl alone. An existing show/season row's status is never
touched; only a newly-created one gets a default.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from ..models import MediaType, WatchRecord
from ..plan import ActionOutcome, MatchedAction, RestorePlan, SkippedAlreadyCurrent
from .base import Target

_EXISTING_MOVIES_SQL = """
    SELECT i.media_id, m.end_date FROM app_movie m
    JOIN app_item i ON m.item_id = i.id
    WHERE i.source = 'tmdb' AND i.media_type = 'movie' AND m.user_id = %(user_id)s
"""

_EXISTING_EPISODES_SQL = """
    SELECT ei.media_id, ei.season_number, ei.episode_number, e.end_date
    FROM app_episode e
    JOIN app_item ei ON e.item_id = ei.id
    JOIN app_season s ON e.related_season_id = s.id
    WHERE ei.source = 'tmdb' AND ei.media_type = 'episode' AND s.user_id = %(user_id)s
"""


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class YamtrackHistoryTarget(Target):
    def __init__(self, dsn: str, user_id: int) -> None:
        self.dsn = dsn
        self.user_id = user_id

    def describe(self) -> str:
        return f"YAMTrack (user_id={self.user_id})"

    def plan(self, records: Iterable[WatchRecord], *, force: bool = False) -> RestorePlan:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "YamtrackHistoryTarget requires the 'yamtrack-db' extra: "
                "pip install jellyfin-watch-sync[yamtrack-db]"
            ) from exc

        # Two bulk queries, not one query per record -- mirrors
        # JellyfinLibraryIndex's build-once-then-resolve-in-memory shape
        # rather than YamtrackHistoryTarget doing its own round trip per
        # record, which would be the dominant cost at real scale (a full
        # history backup can be 100k+ records; see targets/jellyfin.py's
        # own note on why a per-page 502 mattered at similar scale).
        existing_movies: dict[str, datetime] = {}
        existing_episodes: dict[tuple[str, int, int], datetime] = {}
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(_EXISTING_MOVIES_SQL, {"user_id": self.user_id})
            for media_id, end_date in cur:
                if end_date is not None:
                    existing_movies[media_id] = end_date
            cur.execute(_EXISTING_EPISODES_SQL, {"user_id": self.user_id})
            for media_id, season, episode, end_date in cur:
                if end_date is not None:
                    existing_episodes[(media_id, season, episode)] = end_date

        plan = RestorePlan(source_description="", target_description=self.describe())
        seen: set[tuple] = set()
        for record in records:
            if record.key in seen:  # dedupe: a source might yield the same item more than once
                continue
            seen.add(record.key)

            target_id = self._target_id(record)
            if record.media_type is MediaType.MOVIE:
                existing = existing_movies.get(str(record.tmdb_id))
            else:
                existing = existing_episodes.get((str(record.tmdb_id), record.season, record.episode))

            if not force and existing is not None and _as_aware(existing) >= _as_aware(record.watched_at):
                plan.skipped_already_current.append(
                    SkippedAlreadyCurrent(record=record, target_item_id=target_id, current_watched_at=str(existing))
                )
                continue
            plan.matched.append(MatchedAction(record=record, target_item_id=target_id, target_title=record.title))
        return plan

    @staticmethod
    def _target_id(record: WatchRecord) -> str:
        if record.media_type is MediaType.EPISODE:
            return f"tmdb:{record.tmdb_id}:S{record.season}E{record.episode}"
        return f"tmdb:{record.tmdb_id}"

    def apply(self, plan: RestorePlan) -> RestorePlan:
        import psycopg  # already confirmed importable by plan()

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            for action in plan.matched:
                if action.outcome is not ActionOutcome.PENDING:
                    continue
                try:
                    if action.record.media_type is MediaType.MOVIE:
                        self._apply_movie(cur, action.record)
                    else:
                        self._apply_episode(cur, action.record)
                    action.outcome = ActionOutcome.APPLIED
                except Exception as exc:  # noqa: BLE001 - report per-record, keep going
                    action.outcome = ActionOutcome.FAILED
                    action.error = str(exc)
            conn.commit()
        return plan

    def _apply_movie(self, cur, record: WatchRecord) -> None:
        item_id = self._find_or_create_item(cur, "movie", str(record.tmdb_id), record.title)
        cur.execute("SELECT id FROM app_movie WHERE user_id = %s AND item_id = %s", (self.user_id, item_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE app_movie SET end_date = %s WHERE id = %s", (record.watched_at, row[0]))
        else:
            cur.execute(
                "INSERT INTO app_movie (status, notes, user_id, progress, item_id, progressed_at, created_at, end_date) "
                "VALUES ('Completed', '', %s, 0, %s, now(), now(), %s)",
                (self.user_id, item_id, record.watched_at),
            )

    def _apply_episode(self, cur, record: WatchRecord) -> None:
        series_media_id = str(record.tmdb_id)

        tv_item_id = self._find_or_create_item(cur, "tv", series_media_id, record.title)
        tv_id = self._find_or_create_tv(cur, tv_item_id)

        season_item_id = self._find_or_create_item(
            cur, "season", series_media_id, record.title, season_number=record.season,
        )
        season_id = self._find_or_create_season(cur, tv_id, season_item_id)

        episode_item_id = self._find_or_create_item(
            cur, "episode", series_media_id, record.title,
            season_number=record.season, episode_number=record.episode,
        )
        cur.execute(
            "SELECT id FROM app_episode WHERE related_season_id = %s AND item_id = %s",
            (season_id, episode_item_id),
        )
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE app_episode SET end_date = %s WHERE id = %s", (record.watched_at, row[0]))
        else:
            cur.execute(
                "INSERT INTO app_episode (end_date, related_season_id, item_id, created_at) "
                "VALUES (%s, %s, %s, now())",
                (record.watched_at, season_id, episode_item_id),
            )

    def _find_or_create_tv(self, cur, item_id: int) -> int:
        cur.execute("SELECT id FROM app_tv WHERE user_id = %s AND item_id = %s", (self.user_id, item_id))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO app_tv (status, notes, user_id, item_id, created_at) "
            "VALUES ('In progress', '', %s, %s, now()) RETURNING id",
            (self.user_id, item_id),
        )
        return cur.fetchone()[0]

    def _find_or_create_season(self, cur, tv_id: int, item_id: int) -> int:
        cur.execute("SELECT id FROM app_season WHERE related_tv_id = %s AND item_id = %s", (tv_id, item_id))
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO app_season (status, notes, user_id, related_tv_id, item_id, created_at) "
            "VALUES ('In progress', '', %s, %s, %s, now()) RETURNING id",
            (self.user_id, tv_id, item_id),
        )
        return cur.fetchone()[0]

    @staticmethod
    def _find_or_create_item(
        cur, media_type: str, media_id: str, title: str | None,
        *, season_number: int | None = None, episode_number: int | None = None,
    ) -> int:
        # IS NOT DISTINCT FROM (rather than a hardcoded IS NULL, as
        # Collections' equivalent helper uses) because this one helper
        # covers all four media_types, including the two (movie, tv) where
        # both columns really are NULL and the two (season, episode) where
        # they're not.
        cur.execute(
            "SELECT id FROM app_item WHERE source = 'tmdb' AND media_type = %s AND media_id = %s "
            "AND season_number IS NOT DISTINCT FROM %s AND episode_number IS NOT DISTINCT FROM %s",
            (media_type, media_id, season_number, episode_number),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO app_item (source, media_type, media_id, title, image, season_number, episode_number) "
            "VALUES ('tmdb', %s, %s, %s, '', %s, %s) RETURNING id",
            (media_type, media_id, title or f"tmdb:{media_id}", season_number, episode_number),
        )
        return cur.fetchone()[0]
