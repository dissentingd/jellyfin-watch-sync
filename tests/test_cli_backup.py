"""End-to-end check that `backup` is actually wired up: reads live Jellyfin
via JellyfinLiveSource, writes via the requested target, and respects the
same dry-run-unless---apply gate as `restore`."""

import re

import httpx
from typer.testing import CliRunner

from jellyfin_watch_restore.cli import app

runner = CliRunner()

MOVIES = {
    "TotalRecordCount": 1,
    "Items": [{
        "Id": "movie-item-1", "Name": "Seventh Son",
        "ProviderIds": {"Tmdb": "68737"},
        "UserData": {"Played": True, "LastPlayedDate": "2019-03-12T23:32:00.0000000Z", "PlayCount": 1},
    }],
}
EMPTY = {"TotalRecordCount": 0, "Items": []}


def _mock_library(httpx_mock):
    def callback(request: httpx.Request) -> httpx.Response:
        include_types = request.url.params.get("IncludeItemTypes")
        start_index = int(request.url.params.get("StartIndex", "0"))
        if start_index > 0:
            return httpx.Response(200, json=EMPTY)
        if include_types == "Movie":
            return httpx.Response(200, json=MOVIES)
        return httpx.Response(200, json=EMPTY)  # Series, Episode

    httpx_mock.add_callback(callback, url=re.compile(r".*/Items.*"), is_reusable=True)


def test_dry_run_does_not_write_the_file(httpx_mock, tmp_path):
    _mock_library(httpx_mock)
    out = tmp_path / "out.csv"

    result = runner.invoke(app, [
        "backup",
        "--target-type", "generic-csv",
        "--target-path", str(out),
        "--jellyfin-url", "http://jellyfin.test",
        "--jellyfin-api-key", "fake-key",
        "--jellyfin-user-id", "user-1",
    ])

    assert result.exit_code == 0, result.stdout
    assert "Dry run only" in result.stdout
    assert not out.exists()


def test_apply_writes_the_file(httpx_mock, tmp_path):
    _mock_library(httpx_mock)
    out = tmp_path / "out.csv"

    result = runner.invoke(app, [
        "backup",
        "--target-type", "generic-csv",
        "--target-path", str(out),
        "--jellyfin-url", "http://jellyfin.test",
        "--jellyfin-api-key", "fake-key",
        "--jellyfin-user-id", "user-1",
        "--apply",
    ])

    assert result.exit_code == 0, result.stdout
    assert "applied: 1" in result.stdout
    assert "movie,68737" in out.read_text(encoding="utf-8")


def test_missing_target_path_is_a_clean_error_before_any_jellyfin_call(httpx_mock, tmp_path):
    # Deliberately no _mock_library() call: this must fail on argument
    # validation alone, before ever touching Jellyfin -- if the code
    # regressed to validating after the crawl, pytest-httpx's default
    # "no unmocked request" assertion would catch that too.
    result = runner.invoke(app, [
        "backup",
        "--target-type", "generic-csv",
        "--jellyfin-url", "http://jellyfin.test",
        "--jellyfin-api-key", "fake-key",
        "--jellyfin-user-id", "user-1",
    ])

    assert result.exit_code != 0
    assert "--target-path" in result.output
