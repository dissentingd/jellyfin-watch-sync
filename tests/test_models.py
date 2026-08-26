from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jellyfin_watch_restore.models import MediaType, WatchRecord


def test_movie_record_valid():
    r = WatchRecord(media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC))
    assert r.key == (MediaType.MOVIE, 68737)


def test_episode_requires_season_and_episode():
    with pytest.raises(ValidationError):
        WatchRecord(media_type=MediaType.EPISODE, tmdb_id=111111, watched_at=datetime.now(UTC))


def test_movie_rejects_season_episode():
    with pytest.raises(ValidationError):
        WatchRecord(
            media_type=MediaType.MOVIE, tmdb_id=68737, season=1, episode=1,
            watched_at=datetime.now(UTC),
        )


def test_episode_key_includes_season_episode():
    r = WatchRecord(
        media_type=MediaType.EPISODE, tmdb_id=111111, season=1, episode=1,
        watched_at=datetime.now(UTC),
    )
    assert r.key == (MediaType.EPISODE, 111111, 1, 1)


def test_nonpositive_tmdb_id_rejected():
    with pytest.raises(ValidationError):
        WatchRecord(media_type=MediaType.MOVIE, tmdb_id=0, watched_at=datetime.now(UTC))


def test_play_count_floors_at_one():
    r = WatchRecord(
        media_type=MediaType.MOVIE, tmdb_id=68737, watched_at=datetime.now(UTC), play_count=0,
    )
    assert r.play_count == 1
