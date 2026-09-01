"""Exercises JellyfinBackupSource against a synthetic backup directory, with the
Series record placed AFTER its own episodes in BaseItems.json -- this is the
exact ordering that would silently drop episodes if the two-pass series
resolution (see the source's own docstring) were collapsed into one pass."""

import json

from jellyfin_watch_sync.models import MediaType
from jellyfin_watch_sync.sources.jellyfin_backup import JellyfinBackupSource

USER_ID = "user-guid-1"
MOVIE_ID = "movie-item-1"
SERIES_ID = "series-item-1"
EP1_ID = "ep-item-1"
EP2_ID = "ep-item-2"


def _write_backup(tmp_path):
    db = tmp_path / "Database"
    db.mkdir()

    (db / "Users.json").write_text(json.dumps([
        {"Id": USER_ID, "Username": "seed"},
        {"Id": "other-user", "Username": "someone-else"},
    ]), encoding="utf-8")

    (db / "UserData.json").write_text(json.dumps([
        {"UserId": USER_ID, "ItemId": MOVIE_ID, "Played": True, "PlayCount": 1,
         "LastPlayedDate": "2019-03-12T23:32:00.0000000Z"},
        {"UserId": USER_ID, "ItemId": EP1_ID, "Played": True, "PlayCount": 1,
         "LastPlayedDate": "2025-09-28T00:00:00.0000000Z"},
        {"UserId": USER_ID, "ItemId": EP2_ID, "Played": False, "PlayCount": 0,
         "LastPlayedDate": None},
        # a different user's watch -- must not leak into the "seed" source's records
        {"UserId": "other-user", "ItemId": MOVIE_ID, "Played": True, "PlayCount": 1,
         "LastPlayedDate": "2020-01-01T00:00:00.0000000Z"},
    ]), encoding="utf-8")

    (db / "BaseItemProviders.json").write_text(json.dumps([
        {"ItemId": MOVIE_ID, "ProviderId": "Tmdb", "ProviderValue": "68737"},
        {"ItemId": SERIES_ID, "ProviderId": "Tmdb", "ProviderValue": "111111"},
    ]), encoding="utf-8")

    # Deliberately: the episodes appear BEFORE their Series record.
    (db / "BaseItems.json").write_text(json.dumps([
        {"Id": EP1_ID, "Type": "MediaBrowser.Controller.Entities.TV.Episode", "Name": "Pilot",
         "SeriesId": SERIES_ID, "ParentIndexNumber": 1, "IndexNumber": 1},
        {"Id": EP2_ID, "Type": "MediaBrowser.Controller.Entities.TV.Episode", "Name": "Ep2",
         "SeriesId": SERIES_ID, "ParentIndexNumber": 1, "IndexNumber": 2},
        {"Id": MOVIE_ID, "Type": "MediaBrowser.Controller.Entities.Movies.Movie", "Name": "Seventh Son"},
        {"Id": SERIES_ID, "Type": "MediaBrowser.Controller.Entities.TV.Series", "Name": "Some Show"},
    ]), encoding="utf-8")

    return tmp_path


def test_episode_before_its_series_in_the_file_is_still_matched(tmp_path):
    backup_dir = _write_backup(tmp_path)
    records = list(JellyfinBackupSource(backup_dir, username="seed").records())

    episodes = [r for r in records if r.media_type is MediaType.EPISODE]
    assert len(episodes) == 1  # EP2 wasn't played, correctly excluded
    assert episodes[0].tmdb_id == 111111
    assert episodes[0].season == 1
    assert episodes[0].episode == 1


def test_movie_is_matched(tmp_path):
    backup_dir = _write_backup(tmp_path)
    records = list(JellyfinBackupSource(backup_dir, username="seed").records())

    movies = [r for r in records if r.media_type is MediaType.MOVIE]
    assert len(movies) == 1
    assert movies[0].tmdb_id == 68737


def test_other_users_watches_are_not_included(tmp_path):
    backup_dir = _write_backup(tmp_path)
    records = list(JellyfinBackupSource(backup_dir, username="seed").records())
    # only 2 records total: the one movie + the one played episode, both "seed"'s
    assert len(records) == 2


def test_unknown_username_raises(tmp_path):
    backup_dir = _write_backup(tmp_path)
    try:
        list(JellyfinBackupSource(backup_dir, username="nobody").records())
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "nobody" in str(exc)
