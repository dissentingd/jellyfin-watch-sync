"""Exercises YamtrackListsTarget.apply() against a tiny in-memory fake of the
3 real tables it writes to (lists_customlist, app_item, lists_customlistitem)
-- not a live database, but real enough to cover the find-or-create and
ON-CONFLICT-dedup behavior, which a fully scripted mock wouldn't."""

import pytest

from jellyfin_watch_sync.collections.models import (
    CollectionMember,
    CollectionMemberType,
    CollectionRecord,
)
from jellyfin_watch_sync.collections.targets import YamtrackListsTarget
from jellyfin_watch_sync.plan import ActionOutcome


class FakeDb:
    """A tiny in-memory stand-in for the 3 real tables, enough to exercise
    the exact SQL statements YamtrackListsTarget issues."""

    def __init__(self):
        self.lists: dict[tuple[int, str], int] = {}          # (owner_id, name) -> id
        self.items: dict[tuple[str, str], int] = {}           # (media_type, media_id) -> id
        self.list_items: set[tuple[int, int]] = set()         # (item_id, list_id)
        self._next_id = 1

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id


class FakeCursor:
    def __init__(self, db: FakeDb):
        self.db = db
        self.executed: list[tuple[str, tuple]] = []
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((sql, params))
        sql_norm = " ".join(sql.split())

        # NOTE: check the longer/more-specific table name ("lists_customlistitem")
        # before its own prefix ("lists_customlist") -- string startswith() would
        # otherwise misroute every customlistitem statement into the customlist
        # branch, since "lists_customlist" is literally a prefix of
        # "lists_customlistitem".
        if sql_norm.startswith("INSERT INTO lists_customlistitem"):
            item_id, list_id = params
            self.db.list_items.add((item_id, list_id))  # ON CONFLICT DO NOTHING == set semantics
            self._result = None

        elif sql_norm.startswith("SELECT id FROM lists_customlist"):
            owner_id, name = params
            item_id = self.db.lists.get((owner_id, name))
            self._result = (item_id,) if item_id is not None else None

        elif sql_norm.startswith("INSERT INTO lists_customlist"):
            name, _description, owner_id = params
            new_id = self.db._new_id()
            self.db.lists[(owner_id, name)] = new_id
            self._result = (new_id,)

        elif sql_norm.startswith("SELECT id FROM app_item"):
            media_type, media_id = params
            item_id = self.db.items.get((media_type, media_id))
            self._result = (item_id,) if item_id is not None else None

        elif sql_norm.startswith("INSERT INTO app_item"):
            media_type, media_id, _title = params
            new_id = self.db._new_id()
            self.db.items[(media_type, media_id)] = new_id
            self._result = (new_id,)

        else:
            raise AssertionError(f"unexpected SQL: {sql_norm}")

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
    import types

    fake_module = types.SimpleNamespace(connect=lambda dsn: FakeConnection(db))
    monkeypatch.setitem(__import__("sys").modules, "psycopg", fake_module)
    return fake_module


def test_creates_new_list_and_items(patch_psycopg, db):
    target = YamtrackListsTarget(dsn="fake", user_id=4)
    record = CollectionRecord(
        name="Kids TV",
        members=[
            CollectionMember(media_type=CollectionMemberType.SERIES, tmdb_id=1996, title="The Flintstones"),
            CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737, title="Seventh Son"),
        ],
    )
    plan = target.plan([record])

    target.apply(plan)

    assert plan.actions[0].outcome is ActionOutcome.APPLIED
    assert (4, "Kids TV") in db.lists
    assert ("tv", "1996") in db.items
    assert ("movie", "68737") in db.items
    list_id = db.lists[(4, "Kids TV")]
    assert (db.items[("tv", "1996")], list_id) in db.list_items
    assert (db.items[("movie", "68737")], list_id) in db.list_items


def test_reuses_existing_list_and_item(patch_psycopg, db):
    db.lists[(4, "Kids TV")] = 100
    db.items[("movie", "68737")] = 200

    target = YamtrackListsTarget(dsn="fake", user_id=4)
    record = CollectionRecord(
        name="Kids TV",
        members=[CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737)],
    )
    plan = target.plan([record])
    target.apply(plan)

    assert len(db.lists) == 1  # no duplicate list created
    assert len(db.items) == 1  # no duplicate item created
    assert (200, 100) in db.list_items


def test_reapplying_is_idempotent(patch_psycopg, db):
    target = YamtrackListsTarget(dsn="fake", user_id=4)
    record = CollectionRecord(
        name="Kids TV",
        members=[CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737)],
    )

    target.apply(target.plan([record]))
    target.apply(target.plan([record]))  # running it twice must not duplicate anything

    assert len(db.lists) == 1
    assert len(db.items) == 1
    assert len(db.list_items) == 1


def test_empty_collection_is_skipped_before_apply(patch_psycopg, db):
    target = YamtrackListsTarget(dsn="fake", user_id=4)
    record = CollectionRecord(name="Empty", members=[])
    plan = target.plan([record])

    assert plan.actions == []
    assert len(plan.skipped_empty) == 1
