from jellyfin_watch_sync.models import MediaType
from jellyfin_watch_sync.sources.generic_csv import GenericCsvSource

CSV = """media_type,tmdb_id,season,episode,watched_at,play_count,title
movie,68737,,,2019-03-12T23:32:00,1,Seventh Son
episode,111111,1,1,2025-09-28,,Some Show S1E1
movie,555,,,2020-01-01,3,Rewatched Movie
"""


def test_parses_movie_and_episode_rows(tmp_path):
    p = tmp_path / "history.csv"
    p.write_text(CSV, encoding="utf-8")

    records = list(GenericCsvSource(p).records())
    assert len(records) == 3

    movie = records[0]
    assert movie.media_type is MediaType.MOVIE
    assert movie.tmdb_id == 68737
    assert movie.season is None

    episode = records[1]
    assert episode.media_type is MediaType.EPISODE
    assert episode.season == 1
    assert episode.episode == 1

    rewatch = records[2]
    assert rewatch.play_count == 3


def test_date_only_watched_at_is_accepted(tmp_path):
    p = tmp_path / "history.csv"
    p.write_text(CSV, encoding="utf-8")
    records = list(GenericCsvSource(p).records())
    assert records[1].watched_at.year == 2025


def test_bad_row_raises_with_line_number(tmp_path):
    p = tmp_path / "history.csv"
    p.write_text("media_type,tmdb_id,season,episode,watched_at\nmovie,notanumber,,,2020-01-01\n", encoding="utf-8")
    try:
        list(GenericCsvSource(p).records())
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "history.csv:2" in str(exc)
