"""CLI wiring for the Floppy options: bad/missing args fail before any
network traffic, and a floppy-api backup is dry-run unless --apply."""

import re

import httpx
from typer.testing import CliRunner

from jellyfin_watch_sync.cli import app

runner = CliRunner()


def _plain(output: str) -> str:
    """Rich colorizes output under GitHub Actions, splitting option names
    like --floppy-token with ANSI codes (same fix as test_cli_backup.py)."""
    return re.sub(r"\x1b\[[0-9;]*m", "", output)


JELLYFIN = ["--jellyfin-url", "http://jellyfin.test", "--jellyfin-api-key", "k", "--jellyfin-user-id", "u"]


def test_backup_floppy_api_needs_url_and_token_before_crawling(monkeypatch):
    monkeypatch.delenv("FLOPPY_URL", raising=False)
    monkeypatch.delenv("FLOPPY_TOKEN", raising=False)
    # no httpx_mock responses registered: any request at all would fail the test
    result = runner.invoke(app, ["backup", "--target-type", "floppy-api", *JELLYFIN])
    assert result.exit_code == 2
    assert "floppy-token" in _plain(result.output)


def test_backup_collections_still_defaults_to_yamtrack(monkeypatch):
    monkeypatch.delenv("YAMTRACK_DSN", raising=False)
    monkeypatch.delenv("YAMTRACK_USER_ID", raising=False)
    result = runner.invoke(app, ["backup-collections", *JELLYFIN])
    assert result.exit_code == 2
    assert "yamtrack-dsn" in _plain(result.output)


def test_restore_collections_floppy_csv_needs_a_path():
    result = runner.invoke(app, ["restore-collections", "--source-type", "floppy-csv", *JELLYFIN])
    assert result.exit_code == 2
    assert "source-path" in _plain(result.output)


def test_backup_to_floppy_is_a_dry_run_without_apply(httpx_mock):
    movies = {"TotalRecordCount": 1, "Items": [{
        "Id": "m1", "Name": "Seventh Son", "ProviderIds": {"Tmdb": "68737"},
        "UserData": {"Played": True, "LastPlayedDate": "2019-03-12T23:32:00.0000000Z", "PlayCount": 1},
    }]}
    empty = {"TotalRecordCount": 0, "Items": []}

    def jellyfin(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("IncludeItemTypes") == "Movie" and request.url.params.get("StartIndex") == "0":
            return httpx.Response(200, json=movies)
        return httpx.Response(200, json=empty)

    httpx_mock.add_callback(jellyfin, url=re.compile(r"http://jellyfin\.test/.*"), is_reusable=True)
    httpx_mock.add_response(method="GET", url=re.compile(r"http://floppy\.test/api/v1/history/.*"),
                            json={"pagination": {"total": 0}, "results": []})

    result = runner.invoke(app, [
        "backup", "--target-type", "floppy-api",
        "--floppy-url", "http://floppy.test", "--floppy-token", "tok", *JELLYFIN,
    ])

    assert result.exit_code == 0, result.output
    assert "Dry run only" in result.output
    assert not [r for r in httpx_mock.get_requests() if r.method == "POST"]
