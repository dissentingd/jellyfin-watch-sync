"""Exercises YamtrackHistoryTarget.plan()/apply() against a tiny in-memory
fake of the 5 real tables it touches (app_item, app_movie, app_tv,
app_season, app_episode) -- not a live database, but real enough to cover
the find-or-create chain (movie is 1 level; episode is 3, through
app_tv -> app_season -> app_episode) and the already-current skip logic,
which a fully scripted mock wouldn't."""

from datetime import UTC, datetime

import pytest

from jellyfin_watch_restore.models import MediaType, WatchRecord
from jellyfin_watch_restore.plan import ActionOutcome
from jellyfin_watch_restore.targets.yamtrack_history import YamtrackHistoryTarget


class FakeDb:
    def __init__(self):
        # (media_type, media_id, season_number, episode_number) -> item id
        self.items: dict[tuple, int] = {}
        # (user_id, item_id) -> {"id": int, "end_date": datetime | None}
        self.movies: dict[tuple[int, int], dict] = {}
        # (user_id, item_id) -> tv id
        self.tvs: dict[tuple[int, int], int] = {}
        # (related_tv_id, item_id) -> season id
        self.seasons: dict[tuple[int, int], int] = {}
        # (related_season_id, item_id) -> {"id": int, "end_date": datetime | None}
        self.episodes: dict[tuple[int, int], dict] = {}
        self._next_id = 1

    def new_id(self) -> int:
        self._next_id += 1
        return self._next_id


class FakeCursor:
    def __init__(self, db: FakeDb):
        self.db = db
        self._result = None
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._rows)

    def execute(self, sql: str, params=()) -> None:
        sql_norm = " ".join(sql.split())
        self._result = None
        self._rows = []

        if sql_norm.startswith("SELECT i.media_id, m.end_date FROM app_movie"):
            user_id = params["user_id"]
            self._rows = [
                (self._item_key_for(item_id)[0], row["end_date"])
                for (uid, item_id), row in self.db.movies.items()
                if uid == user_id
            ]
        elif sql_norm.startswith("SELECT ei.media_id, ei.season_number, ei.episode_number, e.end_date"):
            # filtered by the episode's season's user_id -- our fake stores
            # episodes directly, so re-derive "belongs to this user" via the
            # season/tv chain recorded when the episode was created.
            user_id = params["user_id"]
            self._rows = [
                (*self._item_key_for(item_id), row["end_date"])
                for (_rsid, item_id), row in self.db.episodes.items()
                if row.get("user_id") == user_id
            ]
        elif sql_norm.startswith("SELECT id FROM app_item"):
            media_type, media_id, season_number, episode_number = params
            item_id = self.db.items.get((media_type, media_id, season_number, episode_number))
            self._result = (item_id,) if item_id is not None else None
        elif sql_norm.startswith("INSERT INTO app_item"):
            media_type, media_id, _title, season_number, episode_number = params
            new_id = self.db.new_id()
            self.db.items[(media_type, media_id, season_number, episode_number)] = new_id
            self._result = (new_id,)
        elif sql_norm.startswith("SELECT id FROM app_movie"):
            user_id, item_id = params
            row = self.db.movies.get((user_id, item_id))
            self._result = (row["id"],) if row else None
        elif sql_norm.startswith("UPDATE app_movie"):
            end_date, movie_id = params
            for row in self.db.movies.values():
                if row["id"] == movie_id:
                    row["end_date"] = end_date
        elif sql_norm.startswith("INSERT INTO app_movie"):
            user_id, item_id, end_date = params
            new_id = self.db.new_id()
            self.db.movies[(user_id, item_id)] = {"id": new_id, "end_date": end_date}
            self._result = (new_id,)
        elif sql_norm.startswith("SELECT id FROM app_tv"):
            user_id, item_id = params
            tv_id = self.db.tvs.get((user_id, item_id))
            self._result = (tv_id,) if tv_id is not None else None
        elif sql_norm.startswith("INSERT INTO app_tv"):
            user_id, item_id = params
            new_id = self.db.new_id()
            self.db.tvs[(user_id, item_id)] = new_id
            self._result = (new_id,)
        elif sql_norm.startswith("SELECT id FROM app_season"):
            tv_id, item_id = params
            season_id = self.db.seasons.get((tv_id, item_id))
            self._result = (season_id,) if season_id is not None else None
        elif sql_norm.startswith("INSERT INTO app_season"):
            user_id, tv_id, item_id = params
            new_id = self.db.new_id()
            self.db.seasons[(tv_id, item_id)] = new_id
            self._result = (new_id,)
            self._last_season_user_id = user_id  # stashed for the episode row created right after
        elif sql_norm.startswith("SELECT id FROM app_episode"):
            related_season_id, item_id = params
            row = self.db.episodes.get((related_season_id, item_id))
            self._result = (row["id"],) if row else None
        elif sql_norm.startswith("UPDATE app_episode"):
            end_date, episode_id = params
            for row in self.db.episodes.values():
                if row["id"] == episode_id:
                    row["end_date"] = end_date
        elif sql_norm.startswith("INSERT INTO app_episode"):
            end_date, related_season_id, item_id = params
            new_id = self.db.new_id()
            # Real ownership is via related_season -> app_season.user_id;
            # the fake just remembers it directly since it never models
            # app_season as its own row with a separate user_id lookup.
            user_id = getattr(self, "_last_season_user_id", None)
            self.db.episodes[(related_season_id, item_id)] = {
                "id": new_id, "end_date": end_date, "user_id": user_id,
            }
        else:
            raise AssertionError(f"unexpected SQL: {sql_norm}")

    def _item_key_for(self, item_id: int) -> tuple:
        for key, iid in self.db.items.items():
            if iid == item_id:
                _media_type, media_id, season, episode = key
                return media_id, season, episode
        raise AssertionError(f"no app_item with id={item_id}")

    def fetchone(self):
        return self._result


class FakeConnection:
    def __init__(self, db: FakeDb):
        self.db = db
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.committed = True


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def patch_psycopg(monkeypatch, db):
    import sys
    import types

    fake_module = types.SimpleNamespace(connect=lambda dsn: FakeConnection(db))
    monkeypatch.setitem(sys.modules, "psycopg", fake_module)
    return fake_module


def test_movie_creates_item_and_history_row(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    watched = datetime(2019, 3, 12, tzinfo=UTC)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=watched, title="Seventh Son")

    plan = target.plan([record])
    assert len(plan.matched) == 1  # nothing pre-existing -- not skipped
    target.apply(plan)

    assert plan.matched[0].outcome is ActionOutcome.APPLIED
    item_id = db.items[("movie", "68737", None, None)]
    assert db.movies[(4, item_id)]["end_date"] == watched


def test_episode_creates_full_tv_season_episode_chain(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    watched = datetime(2025, 9, 28, tzinfo=UTC)
    record = WatchRecord(
        media_type=MediaType.EPISODE, tmdb_id=111111, season=1, episode=1,
        watched_at=watched, title="Pilot",
    )

    plan = target.plan([record])
    target.apply(plan)

    assert plan.matched[0].outcome is ActionOutcome.APPLIED
    tv_item = db.items[("tv", "111111", None, None)]
    season_item = db.items[("season", "111111", 1, None)]
    episode_item = db.items[("episode", "111111", 1, 1)]
    tv_id = db.tvs[(4, tv_item)]
    season_id = db.seasons[(tv_id, season_item)]
    assert db.episodes[(season_id, episode_item)]["end_date"] == watched


def test_skips_already_current_by_default(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    older = datetime(2019, 1, 1, tzinfo=UTC)
    newer_existing = datetime(2026, 1, 1, tzinfo=UTC)

    item_id = db.new_id()
    db.items[("movie", "68737", None, None)] = item_id
    db.movies[(4, item_id)] = {"id": db.new_id(), "end_date": newer_existing}

    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=older)
    plan = target.plan([record])

    assert plan.matched == []
    assert len(plan.skipped_already_current) == 1


def test_force_overrides_already_current_skip(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    older = datetime(2019, 1, 1, tzinfo=UTC)
    newer_existing = datetime(2026, 1, 1, tzinfo=UTC)

    item_id = db.new_id()
    db.items[("movie", "68737", None, None)] = item_id
    db.movies[(4, item_id)] = {"id": db.new_id(), "end_date": newer_existing}

    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=older)
    plan = target.plan([record], force=True)

    assert len(plan.matched) == 1


def test_reapplying_is_idempotent(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    watched = datetime(2019, 3, 12, tzinfo=UTC)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=watched)

    target.apply(target.plan([record]))
    plan2 = target.plan([record])  # second run: already current, nothing to do
    target.apply(plan2)

    assert len(db.items) == 1
    assert len(db.movies) == 1
    assert plan2.matched == []


def test_duplicate_records_are_deduped_before_matching(patch_psycopg, db):
    target = YamtrackHistoryTarget(dsn="fake", user_id=4)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))

    plan = target.plan([record, record])

    assert len(plan.matched) == 1
