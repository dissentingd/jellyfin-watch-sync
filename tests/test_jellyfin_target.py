"""Exercises JellyfinTarget.plan()/apply() against a mocked Jellyfin API --
the mock responds based on the request's IncludeItemTypes param, matching how
JellyfinClient actually queries, rather than assuming exact call order."""

import re
from datetime import UTC, datetime

import httpx
import pytest

from jellyfin_watch_sync.models import MediaType, WatchRecord
from jellyfin_watch_sync.plan import ActionOutcome
from jellyfin_watch_sync.targets.jellyfin import JellyfinTarget
from jellyfin_watch_sync.targets.jellyfin_client import JellyfinClient

MOVIES = {
    "TotalRecordCount": 1,
    "Items": [{
        "Id": "movie-item-1", "Name": "Seventh Son",
        "ProviderIds": {"Tmdb": "68737"},
        "UserData": {"Played": False, "LastPlayedDate": None},
    }],
}
SERIES = {
    "TotalRecordCount": 1,
    "Items": [{"Id": "series-item-1", "Name": "Some Show", "ProviderIds": {"Tmdb": "111111"}}],
}
EPISODES = {
    "TotalRecordCount": 1,
    "Items": [{
        "Id": "episode-item-1", "Name": "Pilot",
        "SeriesId": "series-item-1", "ParentIndexNumber": 1, "IndexNumber": 1,
        "ProviderIds": {}, "UserData": {"Played": False, "LastPlayedDate": None},
    }],
}
EMPTY = {"TotalRecordCount": 0, "Items": []}


def _mock_library(httpx_mock, *, movie_played=False, movie_last_played=None):
    def callback(request: httpx.Request) -> httpx.Response:
        include_types = request.url.params.get("IncludeItemTypes")
        start_index = int(request.url.params.get("StartIndex", "0"))
        if start_index > 0:
            return httpx.Response(200, json=EMPTY)  # second page: stop pagination
        if include_types == "Movie":
            body = {**MOVIES}
            body["Items"][0] = {**body["Items"][0]}
            body["Items"][0]["UserData"] = {"Played": movie_played, "LastPlayedDate": movie_last_played}
            return httpx.Response(200, json=body)
        if include_types == "Series":
            return httpx.Response(200, json=SERIES)
        if include_types == "Episode":
            return httpx.Response(200, json=EPISODES)
        raise AssertionError(f"unexpected IncludeItemTypes={include_types!r}")

    httpx_mock.add_callback(callback, url=re.compile(r".*/Items.*"), is_reusable=True)


@pytest.fixture
def client():
    with JellyfinClient("http://jellyfin.test", "fake-key", "user-1") as c:
        yield c


def test_movie_matches_by_tmdb_id(httpx_mock, client):
    _mock_library(httpx_mock)
    target = JellyfinTarget(client)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))

    plan = target.plan([record])

    assert len(plan.matched) == 1
    assert plan.matched[0].target_item_id == "movie-item-1"
    assert plan.unmatched == []


def test_episode_matches_via_series_tmdb_id(httpx_mock, client):
    _mock_library(httpx_mock)
    target = JellyfinTarget(client)
    record = WatchRecord(
        media_type=MediaType.EPISODE, tmdb_id=111111, season=1, episode=1,
        watched_at=datetime.now(UTC),
    )

    plan = target.plan([record])

    assert len(plan.matched) == 1
    assert plan.matched[0].target_item_id == "episode-item-1"


def test_unmatched_when_tmdb_id_not_in_library(httpx_mock, client):
    _mock_library(httpx_mock)
    target = JellyfinTarget(client)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=999999, watched_at=datetime.now(UTC))

    plan = target.plan([record])

    assert plan.matched == []
    assert len(plan.unmatched) == 1


def test_skips_already_current_by_default(httpx_mock, client):
    _mock_library(httpx_mock, movie_played=True, movie_last_played="2026-01-01T00:00:00.0000000Z")
    target = JellyfinTarget(client)
    older_watch = datetime(2019, 3, 12, tzinfo=UTC)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=older_watch)

    plan = target.plan([record])

    assert plan.matched == []
    assert len(plan.skipped_already_current) == 1


def test_force_overrides_already_current_skip(httpx_mock, client):
    _mock_library(httpx_mock, movie_played=True, movie_last_played="2026-01-01T00:00:00.0000000Z")
    target = JellyfinTarget(client)
    older_watch = datetime(2019, 3, 12, tzinfo=UTC)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=older_watch)

    plan = target.plan([record], force=True)

    assert len(plan.matched) == 1
    assert plan.skipped_already_current == []


def test_older_current_date_does_not_block_a_newer_restore(httpx_mock, client):
    """If Jellyfin's current watched date is OLDER than what we're restoring
    (e.g. the record reflects a later rewatch), it should still be applied --
    the skip only guards against regressing an already-newer date."""
    _mock_library(httpx_mock, movie_played=True, movie_last_played="2015-01-01T00:00:00.0000000Z")
    target = JellyfinTarget(client)
    newer_watch = datetime(2024, 6, 1, tzinfo=UTC)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=newer_watch)

    plan = target.plan([record])

    assert len(plan.matched) == 1


def test_apply_marks_played_and_records_outcome(httpx_mock, client):
    _mock_library(httpx_mock)
    httpx_mock.add_response(method="POST", url=re.compile(r".*/PlayedItems/movie-item-1.*"), json={})

    target = JellyfinTarget(client)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))
    plan = target.plan([record])

    target.apply(plan)

    assert plan.matched[0].outcome is ActionOutcome.APPLIED


def test_duplicate_records_are_deduped_before_matching(httpx_mock, client):
    _mock_library(httpx_mock)
    target = JellyfinTarget(client)
    record = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))

    plan = target.plan([record, record])

    assert len(plan.matched) == 1
