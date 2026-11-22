"""FloppyClient + FloppyHistoryTarget + FloppyListsTarget against mocked
Floppy API responses, shaped like Floppy's own views (src/api/) return them."""

import json
import re
from datetime import UTC, datetime

import httpx
import pytest

from jellyfin_watch_sync.collections import (
    CollectionMember,
    CollectionMemberType,
    CollectionRecord,
    FloppyListsTarget,
)
from jellyfin_watch_sync.errors import describe_error
from jellyfin_watch_sync.models import MediaType, WatchRecord
from jellyfin_watch_sync.plan import ActionOutcome
from jellyfin_watch_sync.targets import floppy_client as floppy_client_module
from jellyfin_watch_sync.targets.floppy_client import FloppyClient, FloppyError
from jellyfin_watch_sync.targets.floppy_history import FloppyHistoryTarget, external_id_for

BASE = "http://floppy.test"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(floppy_client_module, "_RETRY_BACKOFF_SECONDS", 0)
    with FloppyClient(BASE, "tok") as c:
        yield c


def _history_entry(media_type, media_id, end_date, season=None, episode=None, source="tmdb"):
    entry = {
        "media_type": media_type,
        "item": {"id": 1, "media_type": media_type, "media_id": media_id, "source": source, "title": "t"},
        "end_date": end_date,
    }
    if media_type == "episode":
        entry |= {"season_number": season, "episode_number": episode}
    return entry


def _mock_history(httpx_mock, entries, page_size=200):
    def callback(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.params["flat"] == "1"
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={
            "pagination": {"total": len(entries), "limit": page_size, "offset": offset, "next": None, "previous": None},
            "results": entries[offset:offset + page_size],
        })

    httpx_mock.add_callback(callback, method="GET", url=re.compile(r".*/api/v1/history/.*"), is_reusable=True)


def _movie(tmdb_id, when):
    return WatchRecord(media_type=MediaType.MOVIE, tmdb_id=tmdb_id, watched_at=when, title=f"m{tmdb_id}")


def _episode(tmdb_id, s, e, when):
    return WatchRecord(media_type=MediaType.EPISODE, tmdb_id=tmdb_id, season=s, episode=e, watched_at=when)


# -- history ---------------------------------------------------------------

def test_history_pages_and_keeps_only_tmdb_movies_and_episodes(httpx_mock, client, monkeypatch):
    monkeypatch.setattr(floppy_client_module, "_HISTORY_PAGE_SIZE", 2)
    _mock_history(httpx_mock, [
        _history_entry("movie", "68737", "2019-03-12T23:32:00Z"),
        _history_entry("episode", "1399", "2020-01-02T03:04:05+00:00", season=1, episode=1),
        _history_entry("movie", "5114", "2021-01-01T00:00:00Z", source="mal"),
        _history_entry("movie", "603", None),
        _history_entry("music", "abc", "2021-01-01T00:00:00Z"),
    ], page_size=2)

    entries = client.history()

    assert [(e.media_type, e.tmdb_id, e.season, e.episode) for e in entries] == [
        ("movie", 68737, None, None),
        ("episode", 1399, 1, 1),
    ]


def test_history_target_skips_what_floppy_already_has(httpx_mock, client):
    _mock_history(httpx_mock, [
        _history_entry("movie", "1", "2020-06-01T00:00:00Z"),
        _history_entry("episode", "10", "2020-01-01T00:00:00Z", season=1, episode=2),
    ])
    target = FloppyHistoryTarget(client)
    records = [
        _movie(1, datetime(2020, 1, 1, tzinfo=UTC)),       # older than Floppy's -> skip
        _episode(10, 1, 2, datetime(2021, 1, 1, tzinfo=UTC)),  # newer -> write
        _movie(2, datetime(2020, 1, 1)),  # noqa: DTZ001 - not in Floppy, naive date -> write
        _movie(2, datetime(2020, 1, 1)),  # noqa: DTZ001 - duplicate -> ignored
    ]

    plan = target.plan(records)

    assert [m.record.tmdb_id for m in plan.matched] == [10, 2]
    assert [s.record.tmdb_id for s in plan.skipped_already_current] == [1]

    forced = target.plan(records, force=True)
    assert [m.record.tmdb_id for m in forced.matched] == [1, 10, 2]


def test_history_target_apply_posts_dated_plays_with_stable_external_ids(httpx_mock, client):
    _mock_history(httpx_mock, [])
    posted = []

    def callback(request: httpx.Request) -> httpx.Response:
        posted.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={})

    httpx_mock.add_callback(callback, method="POST", url=re.compile(r".*/watch/$"), is_reusable=True)
    movie = _movie(68737, datetime(2019, 3, 12, 23, 32, tzinfo=UTC))
    episode = _episode(1399, 1, 1, datetime(2020, 1, 2, 3, 4, 5, tzinfo=UTC))
    target = FloppyHistoryTarget(client)

    plan = target.apply(target.plan([movie, episode]))

    assert all(m.outcome is ActionOutcome.APPLIED for m in plan.matched)
    assert posted == [
        ("/api/v1/media/movie/tmdb/68737/watch/",
         {"end_date": "2019-03-12T23:32:00+00:00", "external_id": "jellyfin-watch-sync:tmdb:68737:20190312T233200Z"}),
        ("/api/v1/media/tv/tmdb/1399/1/episodes/1/watch/",
         {"end_date": "2020-01-02T03:04:05+00:00",
          "external_id": "jellyfin-watch-sync:tmdb:1399:s1e1:20200102T030405Z"}),
    ]
    assert external_id_for(movie) == external_id_for(movie.model_copy())  # deterministic


def test_history_target_reports_a_failed_write_and_keeps_going(httpx_mock, client):
    _mock_history(httpx_mock, [])
    httpx_mock.add_response(method="POST", url=re.compile(r".*/movie/tmdb/1/watch/$"),
                            status_code=404, json={"detail": "Could not resolve movie."})
    httpx_mock.add_response(method="POST", url=re.compile(r".*/movie/tmdb/2/watch/$"), status_code=201, json={})
    target = FloppyHistoryTarget(client)

    plan = target.apply(target.plan([_movie(1, datetime(2020, 1, 1, tzinfo=UTC)),
                                     _movie(2, datetime(2020, 1, 1, tzinfo=UTC))]))

    assert [m.outcome for m in plan.matched] == [ActionOutcome.FAILED, ActionOutcome.APPLIED]
    assert "Could not resolve movie." in plan.matched[0].error


# -- transport -------------------------------------------------------------

def test_retries_busy_floppy_then_succeeds(httpx_mock, client):
    httpx_mock.add_response(method="GET", status_code=503, json={"detail": "Database is busy."})
    httpx_mock.add_response(method="GET", json={"pagination": {"total": 0}, "results": []})
    assert client.lists() == []


def test_bad_token_is_a_friendly_error_without_retrying(httpx_mock, client):
    httpx_mock.add_response(method="GET", status_code=401, json={"detail": "Invalid token"})
    with pytest.raises(FloppyError) as excinfo:
        client.lists()
    message = describe_error(excinfo.value)
    assert "FLOPPY_TOKEN" in message
    assert "Invalid token" in message
    assert len(httpx_mock.get_requests()) == 1


# -- lists -----------------------------------------------------------------

def _record(name, *tmdb_ids):
    return CollectionRecord(name=name, members=[
        CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=t, title=f"m{t}") for t in tmdb_ids
    ])


def _mock_lists(httpx_mock, lists):
    httpx_mock.add_response(method="GET", url=re.compile(r".*/api/v1/lists/\?.*"), json={
        "pagination": {"total": len(lists), "limit": 100, "offset": 0, "next": None, "previous": None},
        "results": lists,
    })


def test_lists_target_plans_existing_vs_new_and_skips_empty(httpx_mock, client):
    _mock_lists(httpx_mock, [{"id": 5, "name": "Kids TV"}])
    target = FloppyListsTarget(client)

    plan = target.plan([_record("Kids TV", 1), _record("New One", 2), _record("Empty")])

    assert [(a.record.name, a.existing_target_id) for a in plan.actions] == [("Kids TV", "5"), ("New One", None)]
    assert [r.name for r in plan.skipped_empty] == ["Empty"]


def test_lists_target_apply_creates_missing_list_and_tolerates_existing_members(httpx_mock, client):
    _mock_lists(httpx_mock, [])
    httpx_mock.add_response(method="POST", url=f"{BASE}/api/v1/lists/", status_code=201, json={"id": 42, "name": "New"})
    httpx_mock.add_response(method="PUT", url=f"{BASE}/api/v1/media/movie/tmdb/1/lists/42/", json=[])
    httpx_mock.add_response(method="PUT", url=f"{BASE}/api/v1/media/movie/tmdb/2/lists/42/",
                            status_code=409, json={"detail": "Media already in the list."})
    target = FloppyListsTarget(client)

    plan = target.apply(target.plan([_record("New", 1, 2)]))

    (action,) = plan.actions
    assert action.outcome is ActionOutcome.APPLIED, action.error
    assert action.existing_target_id == "42"
    create = next(r for r in httpx_mock.get_requests() if r.method == "POST")
    assert json.loads(create.content) == {"name": "New", "description": ""}


def test_lists_target_one_bad_member_does_not_stop_the_rest(httpx_mock, client):
    _mock_lists(httpx_mock, [{"id": 5, "name": "Kids TV"}])
    httpx_mock.add_response(method="PUT", url=f"{BASE}/api/v1/media/movie/tmdb/1/lists/5/",
                            status_code=404, json={"detail": "Not found on TMDB."})
    httpx_mock.add_response(method="PUT", url=f"{BASE}/api/v1/media/movie/tmdb/2/lists/5/", json=[])
    target = FloppyListsTarget(client)

    plan = target.apply(target.plan([_record("Kids TV", 1, 2)]))

    (action,) = plan.actions
    assert action.outcome is ActionOutcome.FAILED
    assert "1 of 2 member(s)" in action.error
    assert len([r for r in httpx_mock.get_requests() if r.method == "PUT"]) == 2
