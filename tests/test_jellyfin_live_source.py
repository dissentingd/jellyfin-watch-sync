"""Exercises JellyfinLiveSource against a mocked Jellyfin API, in the same
style as test_jellyfin_target.py -- the mock responds based on the request's
IncludeItemTypes param. Series are fetched first regardless of response
order, so an episode's series doesn't need to have been "seen" already for
its TMDB id to resolve -- mirrors JellyfinBackupSource's two-pass reasoning,
just against the live API instead of a backup file."""

import re

import httpx
import pytest

from jellyfin_watch_sync.models import MediaType
from jellyfin_watch_sync.sources.jellyfin_live import JellyfinLiveSource
from jellyfin_watch_sync.targets.jellyfin_client import JellyfinClient

MOVIES = {
    "TotalRecordCount": 1,
    "Items": [{
        "Id": "movie-item-1", "Name": "Seventh Son",
        "ProviderIds": {"Tmdb": "68737"},
        "UserData": {"Played": True, "LastPlayedDate": "2019-03-12T23:32:00.0000000Z", "PlayCount": 2},
    }],
}
UNPLAYED_MOVIE = {
    "TotalRecordCount": 1,
    "Items": [{
        "Id": "movie-item-2", "Name": "Never Watched",
        "ProviderIds": {"Tmdb": "999999"},
        "UserData": {"Played": False, "LastPlayedDate": None, "PlayCount": 0},
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
        "ProviderIds": {}, "UserData": {"Played": True, "LastPlayedDate": "2025-09-28T00:00:00.0000000Z", "PlayCount": 1},
    }],
}
EMPTY = {"TotalRecordCount": 0, "Items": []}


def _mock_library(httpx_mock, *, movies=MOVIES, episodes=EPISODES):
    def callback(request: httpx.Request) -> httpx.Response:
        include_types = request.url.params.get("IncludeItemTypes")
        start_index = int(request.url.params.get("StartIndex", "0"))
        if start_index > 0:
            return httpx.Response(200, json=EMPTY)
        if include_types == "Movie":
            return httpx.Response(200, json=movies)
        if include_types == "Series":
            return httpx.Response(200, json=SERIES)
        if include_types == "Episode":
            return httpx.Response(200, json=episodes)
        raise AssertionError(f"unexpected IncludeItemTypes={include_types!r}")

    httpx_mock.add_callback(callback, url=re.compile(r".*/Items.*"), is_reusable=True)


@pytest.fixture
def client():
    with JellyfinClient("http://jellyfin.test", "fake-key", "user-1") as c:
        yield c


def test_played_movie_is_yielded(httpx_mock, client):
    _mock_library(httpx_mock)
    records = list(JellyfinLiveSource(client).records())

    movies = [r for r in records if r.media_type is MediaType.MOVIE]
    assert len(movies) == 1
    assert movies[0].tmdb_id == 68737
    assert movies[0].play_count == 2
    assert movies[0].title == "Seventh Son"


def test_unplayed_movie_is_excluded(httpx_mock, client):
    _mock_library(httpx_mock, movies=UNPLAYED_MOVIE, episodes=EMPTY)
    records = list(JellyfinLiveSource(client).records())

    assert records == []


def test_played_episode_resolves_series_tmdb_id(httpx_mock, client):
    _mock_library(httpx_mock)
    records = list(JellyfinLiveSource(client).records())

    episodes = [r for r in records if r.media_type is MediaType.EPISODE]
    assert len(episodes) == 1
    assert episodes[0].tmdb_id == 111111
    assert episodes[0].season == 1
    assert episodes[0].episode == 1
    assert episodes[0].play_count == 1


def test_episode_with_no_matching_series_tmdb_id_is_excluded(httpx_mock, client):
    # SERIES response has no Tmdb id at all -> no series can ever resolve.
    # A single callback registered here (not layered on top of
    # _mock_library's) -- pytest-httpx matches the first reusable callback
    # registered, so registering two for the same URL pattern would silently
    # keep using the first and never exercise this test's own response.
    no_tmdb_series = {"TotalRecordCount": 1, "Items": [{"Id": "series-item-1", "Name": "Some Show"}]}

    def callback(request: httpx.Request) -> httpx.Response:
        include_types = request.url.params.get("IncludeItemTypes")
        start_index = int(request.url.params.get("StartIndex", "0"))
        if start_index > 0:
            return httpx.Response(200, json=EMPTY)
        if include_types == "Movie":
            return httpx.Response(200, json=EMPTY)
        if include_types == "Series":
            return httpx.Response(200, json=no_tmdb_series)
        if include_types == "Episode":
            return httpx.Response(200, json=EPISODES)
        raise AssertionError(f"unexpected IncludeItemTypes={include_types!r}")

    httpx_mock.add_callback(callback, url=re.compile(r".*/Items.*"), is_reusable=True)

    records = list(JellyfinLiveSource(client).records())
    assert records == []


def test_describe():
    assert "live" in JellyfinLiveSource(client=object()).describe().lower()
