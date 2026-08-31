import pytest
from pydantic import ValidationError

from jellyfin_watch_restore.collections.models import (
    CollectionMember,
    CollectionMemberType,
    CollectionRecord,
)


def test_member_key_distinguishes_media_type():
    movie = CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=1)
    series = CollectionMember(media_type=CollectionMemberType.SERIES, tmdb_id=1)
    assert movie.key != series.key


def test_nonpositive_tmdb_id_rejected():
    with pytest.raises(ValidationError):
        CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=0)


def test_empty_collection_name_rejected():
    with pytest.raises(ValidationError):
        CollectionRecord(name="   ")


def test_collection_name_is_stripped():
    r = CollectionRecord(name="  Kids TV  ")
    assert r.name == "Kids TV"


def test_collection_defaults_to_no_members():
    r = CollectionRecord(name="Empty List")
    assert r.members == []
