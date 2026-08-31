"""Exercises JellyfinCollectionsTarget.plan()/apply() against a mocked Jellyfin
API. The mock branches on IncludeItemTypes/ParentId, matching how JellyfinClient
actually queries."""

import re

import httpx
import pytest

from jellyfin_watch_restore.collections.models import (
    CollectionMember,
    CollectionMemberType,
    CollectionRecord,
)
from jellyfin_watch_restore.collections.targets import JellyfinCollectionsTarget
from jellyfin_watch_restore.plan import ActionOutcome
from jellyfin_watch_restore.targets.jellyfin_client import JellyfinClient

MOVIES = [{"Id": "movie-1", "Name": "Seventh Son", "ProviderIds": {"Tmdb": "68737"}}]
SERIES = [{"Id": "series-1", "Name": "Some Show", "ProviderIds": {"Tmdb": "111111"}}]
EXISTING_COLLECTIONS = [{"Id": "coll-1", "Name": "Existing Collection", "Type": "BoxSet"}]
EXISTING_COLLECTION_MEMBERS = [{"Id": "movie-1", "Name": "Seventh Son", "ProviderIds": {"Tmdb": "68737"}}]
EMPTY = {"TotalRecordCount": 0, "Items": []}


def _mock(httpx_mock):
    def callback(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if int(params.get("StartIndex", "0")) > 0:
            return httpx.Response(200, json=EMPTY)

        if params.get("ParentId") == "coll-1":
            return httpx.Response(200, json={"TotalRecordCount": 1, "Items": EXISTING_COLLECTION_MEMBERS})

        include = params.get("IncludeItemTypes")
        if include == "Movie":
            return httpx.Response(200, json={"TotalRecordCount": 1, "Items": MOVIES})
        if include == "Series":
            return httpx.Response(200, json={"TotalRecordCount": 1, "Items": SERIES})
        if include == "Episode":
            return httpx.Response(200, json=EMPTY)
        if include == "BoxSet":
            return httpx.Response(200, json={"TotalRecordCount": 1, "Items": EXISTING_COLLECTIONS})
        raise AssertionError(f"unexpected params: {dict(params)}")

    httpx_mock.add_callback(callback, url=re.compile(r".*/Items.*"), is_reusable=True)


@pytest.fixture
def client():
    with JellyfinClient("http://jellyfin.test", "fake-key", "user-1") as c:
        yield c


def test_new_collection_is_created_from_resolved_members(httpx_mock, client):
    _mock(httpx_mock)
    httpx_mock.add_response(method="POST", url=re.compile(r".*/Collections\?"), json={"Id": "new-coll-1"})

    target = JellyfinCollectionsTarget(client)
    record = CollectionRecord(
        name="Brand New Collection",
        members=[CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737)],
    )

    plan = target.plan([record])
    assert len(plan.actions) == 1
    assert plan.actions[0].existing_target_id is None
    assert plan.actions[0].matched_ids == ["movie-1"]

    target.apply(plan)
    assert plan.actions[0].outcome is ActionOutcome.APPLIED


def test_existing_collection_is_merged_not_replaced(httpx_mock, client):
    _mock(httpx_mock)
    add_calls = []

    def add_callback(request: httpx.Request) -> httpx.Response:
        add_calls.append(str(request.url))
        return httpx.Response(200, json={})

    httpx_mock.add_callback(add_callback, url=re.compile(r".*/Collections/coll-1/Items.*"))

    target = JellyfinCollectionsTarget(client)
    record = CollectionRecord(
        name="Existing Collection",
        members=[
            CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737),  # already in coll-1
            CollectionMember(media_type=CollectionMemberType.SERIES, tmdb_id=111111),  # new to coll-1
        ],
    )

    plan = target.plan([record])
    assert plan.actions[0].existing_target_id == "coll-1"

    target.apply(plan)

    assert len(add_calls) == 1
    assert "series-1" in add_calls[0]
    assert "movie-1" not in add_calls[0]  # already-present member must not be re-added


def test_series_member_matches_by_series_tmdb_id(httpx_mock, client):
    _mock(httpx_mock)
    target = JellyfinCollectionsTarget(client)
    record = CollectionRecord(
        name="TV Collection",
        members=[CollectionMember(media_type=CollectionMemberType.SERIES, tmdb_id=111111)],
    )

    plan = target.plan([record])
    assert plan.actions[0].matched_ids == ["series-1"]


def test_unmatched_member_is_reported_not_silently_dropped(httpx_mock, client):
    _mock(httpx_mock)
    target = JellyfinCollectionsTarget(client)
    record = CollectionRecord(
        name="Partial Collection",
        members=[
            CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=68737),
            CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=999999),
        ],
    )

    plan = target.plan([record])
    action = plan.actions[0]
    assert len(action.matched_ids) == 1
    assert len(action.unmatched) == 1
    assert action.unmatched[0].member.tmdb_id == 999999


def test_collection_with_no_resolvable_members_is_skipped(httpx_mock, client):
    _mock(httpx_mock)
    target = JellyfinCollectionsTarget(client)
    record = CollectionRecord(
        name="Nothing Resolves",
        members=[CollectionMember(media_type=CollectionMemberType.MOVIE, tmdb_id=999999)],
    )

    plan = target.plan([record])
    assert plan.actions == []
    assert len(plan.skipped_empty) == 1
