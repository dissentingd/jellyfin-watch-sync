"""Locks in the retry behavior added after a real dry-run against a live server
hit a mid-crawl 502 with no retry, killing an otherwise-successful run."""

import httpx
import pytest

from jellyfin_watch_restore.targets.jellyfin_client import JellyfinClient


@pytest.fixture
def client():
    with JellyfinClient("http://jellyfin.test", "fake-key", "user-1") as c:
        yield c


def test_retries_502_then_succeeds(httpx_mock, client, monkeypatch):
    monkeypatch.setattr("jellyfin_watch_restore.targets.jellyfin_client.time.sleep", lambda _: None)
    attempts = {"n": 0}

    def callback(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, json={"TotalRecordCount": 0, "Items": []})

    httpx_mock.add_callback(callback, is_reusable=True)

    result = client.movies()

    assert result == []
    assert attempts["n"] == 3


def test_gives_up_after_max_retries(httpx_mock, client, monkeypatch):
    monkeypatch.setattr("jellyfin_watch_restore.targets.jellyfin_client.time.sleep", lambda _: None)
    httpx_mock.add_response(status_code=502, is_reusable=True)

    with pytest.raises(httpx.HTTPStatusError):
        client.movies()


def test_does_not_retry_4xx(httpx_mock, client, monkeypatch):
    monkeypatch.setattr("jellyfin_watch_restore.targets.jellyfin_client.time.sleep", lambda _: None)
    attempts = {"n": 0}

    def callback(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(401)

    httpx_mock.add_callback(callback, is_reusable=True)

    with pytest.raises(httpx.HTTPStatusError):
        client.movies()
    assert attempts["n"] == 1  # no retries burned on a guaranteed-repeat failure
