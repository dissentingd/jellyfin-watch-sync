"""End-to-end check that a real command invocation surfaces a friendly
message on a common misconfiguration, not a raw traceback -- test_errors.py
covers the translation logic in isolation; this confirms it's actually wired
into the CLI."""

from typer.testing import CliRunner

from jellyfin_watch_restore.cli import app

runner = CliRunner()


def test_bad_api_key_prints_friendly_message_not_traceback(httpx_mock, tmp_path):
    csv_path = tmp_path / "history.csv"
    csv_path.write_text(
        "media_type,tmdb_id,season,episode,watched_at,play_count,title\n"
        "movie,68737,,,2019-03-12T23:32:00,1,Seventh Son\n",
        encoding="utf-8",
    )

    httpx_mock.add_response(status_code=401, is_reusable=True)

    result = runner.invoke(app, [
        "restore",
        "--source-type", "generic-csv",
        "--source-path", str(csv_path),
        "--jellyfin-url", "http://jellyfin.test",
        "--jellyfin-api-key", "wrong-key",
        "--jellyfin-user-id", "some-user-id",
    ])

    assert result.exit_code == 1
    assert "JELLYFIN_API_KEY" in result.stdout
    assert "Traceback" not in result.stdout
