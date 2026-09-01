from jellyfin_watch_sync.models import MediaType
from jellyfin_watch_sync.sources.yamtrack_csv import YamtrackCsvSource

HEADER = (
    "media_id,source,media_type,title,image,season_number,episode_number,"
    "score,status,notes,start_date,end_date,progress,created_at,progressed_at\n"
)


def _row(**overrides) -> str:
    row = {
        "media_id": "68737", "source": "tmdb", "media_type": "movie", "title": "Seventh Son",
        "image": "", "season_number": "", "episode_number": "", "score": "", "status": "Completed",
        "notes": "", "start_date": "", "end_date": "2019-01-03", "progress": "0", "created_at": "",
        "progressed_at": "2019-01-03T23:51:00Z",
    }
    row.update(overrides)
    return ",".join(row[k] for k in (
        "media_id", "source", "media_type", "title", "image", "season_number", "episode_number",
        "score", "status", "notes", "start_date", "end_date", "progress", "created_at", "progressed_at",
    ))


def test_movie_row_parsed(tmp_path):
    p = tmp_path / "yamtrack.csv"
    p.write_text(HEADER + _row() + "\n", encoding="utf-8")
    records = list(YamtrackCsvSource(p).records())
    assert len(records) == 1
    assert records[0].media_type is MediaType.MOVIE
    assert records[0].tmdb_id == 68737


def test_tv_and_season_rows_are_skipped(tmp_path):
    p = tmp_path / "yamtrack.csv"
    content = HEADER + _row(media_type="tv", end_date="", progressed_at="") + "\n"
    content += _row(media_type="season", season_number="1", end_date="", progressed_at="") + "\n"
    p.write_text(content, encoding="utf-8")
    records = list(YamtrackCsvSource(p).records())
    assert records == []


def test_non_tmdb_source_is_skipped(tmp_path):
    p = tmp_path / "yamtrack.csv"
    p.write_text(HEADER + _row(source="mal") + "\n", encoding="utf-8")
    records = list(YamtrackCsvSource(p).records())
    assert records == []


def test_episode_row_uses_season_episode_numbers(tmp_path):
    p = tmp_path / "yamtrack.csv"
    row = _row(
        media_id="111111", media_type="episode", season_number="1", episode_number="1",
        end_date="2025-09-28", progressed_at="",
    )
    p.write_text(HEADER + row + "\n", encoding="utf-8")
    records = list(YamtrackCsvSource(p).records())
    assert len(records) == 1
    assert records[0].season == 1
    assert records[0].episode == 1


def test_prefers_progressed_at_over_end_date(tmp_path):
    p = tmp_path / "yamtrack.csv"
    p.write_text(HEADER + _row(end_date="2019-01-03", progressed_at="2019-01-03T23:51:00Z") + "\n", encoding="utf-8")
    records = list(YamtrackCsvSource(p).records())
    assert records[0].watched_at.hour == 23
