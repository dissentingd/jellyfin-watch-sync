"""FloppyCsvSource / FloppyCsvListsSource against a hand-built file laid out
like Floppy's own export (src/integrations/exports.py): a leading row_type
column, YAMTrack-style item/track columns, list columns, every field quoted."""

import csv
from datetime import datetime

import pytest

from jellyfin_watch_sync.collections import CollectionMemberType, FloppyCsvListsSource
from jellyfin_watch_sync.models import MediaType
from jellyfin_watch_sync.sources import FloppyCsvSource

HEADER = [
    "row_type", "media_id", "source", "media_type", "library_media_type", "title", "image",
    "season_number", "episode_number", "episode_order_data",
    "score", "progress", "status", "start_date", "end_date", "notes", "created_at", "progressed_at",
    "list_uid", "list_name", "list_description", "list_is_smart", "list_item_date_added",
    "collection_format", "tags",
]


def _row(**values):
    return [values.get(col, "") for col in HEADER]


def _write(path, rows, header=HEADER):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def export(tmp_path):
    return _write(tmp_path / "floppy.csv", [
        _row(row_type="media", media_id="68737", source="tmdb", media_type="movie", title="Seventh Son",
             status="Completed", end_date="2019-03-12T23:32:00+00:00"),
        _row(row_type="media", media_id="1399", source="tmdb", media_type="tv", title="Game of Thrones",
             status="In progress"),
        _row(row_type="media", media_id="1399", source="tmdb", media_type="season", title="Game of Thrones",
             season_number="1"),
        _row(row_type="media", media_id="1399", source="tmdb", media_type="episode", title="Winter Is Coming",
             season_number="1", episode_number="1", end_date="2020-01-02T03:04:05+00:00",
             episode_order_data="x" * 200_000),  # bigger than csv's default field cap
        _row(row_type="media", media_id="5114", source="mal", media_type="movie", title="Some Anime Film",
             end_date="2021-01-01T00:00:00+00:00"),
        _row(row_type="media", media_id="603", source="tmdb", media_type="movie", title="No Date"),
        _row(row_type="media", media_id="12345", source="openlibrary", media_type="book", title="A Book",
             end_date="2021-01-01T00:00:00+00:00"),
        _row(row_type="list", list_uid="7", list_name="Kids TV", list_description="for the kids"),
        _row(row_type="list_item", list_uid="7", list_name="Kids TV", media_id="1399", source="tmdb",
             media_type="tv", title="Game of Thrones"),
        _row(row_type="list_item", list_uid="7", list_name="Kids TV", media_id="68737", source="tmdb",
             media_type="movie", title="Seventh Son"),
        _row(row_type="list_item", list_uid="7", list_name="Kids TV", media_id="68737", source="tmdb",
             media_type="movie", title="Seventh Son"),  # duplicate
        _row(row_type="list_item", list_uid="7", list_name="Kids TV", media_id="5114", source="mal",
             media_type="anime", title="Some Anime"),
        _row(row_type="list", list_uid="9", list_name="Empty One"),
        _row(row_type="collection", media_id="603", source="tmdb", media_type="movie", collection_format="bluray"),
    ])


def test_reads_movie_and_episode_watches_only(export):
    records = list(FloppyCsvSource(export).records())

    assert [(r.media_type, r.tmdb_id, r.season, r.episode) for r in records] == [
        (MediaType.MOVIE, 68737, None, None),
        (MediaType.EPISODE, 1399, 1, 1),
    ]
    assert records[0].watched_at == datetime.fromisoformat("2019-03-12T23:32:00+00:00")
    assert records[0].title == "Seventh Son"


def test_rejects_a_non_floppy_csv(tmp_path):
    path = _write(tmp_path / "yamtrack.csv", [["68737", "tmdb", "movie"]], header=["media_id", "source", "media_type"])
    with pytest.raises(ValueError, match="yamtrack-csv"):
        list(FloppyCsvSource(path).records())


def test_lists_are_grouped_filtered_and_deduped(export):
    lists = list(FloppyCsvListsSource(export).records())

    assert [lst.name for lst in lists] == ["Kids TV", "Empty One"]
    kids = lists[0]
    assert kids.description == "for the kids"
    assert [(m.media_type, m.tmdb_id) for m in kids.members] == [
        (CollectionMemberType.SERIES, 1399),
        (CollectionMemberType.MOVIE, 68737),
    ]
    assert lists[1].members == []


def test_list_item_without_a_list_row_still_lands_in_its_list(tmp_path):
    path = _write(tmp_path / "f.csv", [
        _row(row_type="list_item", list_uid="3", list_name="Orphan", media_id="603", source="tmdb",
             media_type="movie", title="The Matrix"),
    ])
    (only,) = FloppyCsvListsSource(path).records()
    assert only.name == "Orphan"
    assert only.members[0].tmdb_id == 603
