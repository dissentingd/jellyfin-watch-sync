"""A small, purpose-built Jellyfin API client -- not a generic wrapper.

Deliberately NOT built on `jellyfin-apiclient-python`: that package is an
opinionated client extracted from Jellyfin Kodi, documented as incomplete for
anything outside its wrapped playback/session methods, and would need
extending for exactly the calls this tool needs anyway. A thin `httpx` client
covering just those calls is less code and less indirection.

Also deliberately does NOT use `AnyProviderIdEquals` as a server-side filter --
that parameter does not exist on `/Items` in current Jellyfin versions (checked
against the live OpenAPI spec, not assumed from older docs/other projects'
source). Matching is done client-side instead: fetch the library with
`Fields=ProviderIds` and match on TMDB id in Python. This is the same technique
already proven at scale (500k+ item libraries) rather than a compromise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Self

import httpx

_PAGE_SIZE = 500
_MAX_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 1.0  # doubles each retry: 1s, 2s, 4s, 8s


@dataclass(frozen=True)
class LibraryItem:
    item_id: str
    name: str
    tmdb_id: str | None
    played: bool
    last_played_date: str | None
    series_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    item_type: str = ""


@dataclass(frozen=True)
class JellyfinCollection:
    collection_id: str
    name: str


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, user_id: str, timeout: float = 60.0) -> None:
        self.user_id = user_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f'MediaBrowser Token="{api_key}"'},
            timeout=timeout,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Retries transient failures (5xx, connection errors) with exponential
        backoff -- a real-world necessity, not a hypothetical one: a dry-run
        against a real ~2000-episode-page library over a reverse-proxied HTTPS
        path hit a mid-crawl `502 Bad Gateway` with no retry logic, killing an
        otherwise-successful run at page 274. 4xx errors (bad auth, bad request)
        fail immediately -- retrying those would just waste time on the same
        guaranteed failure."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
                if resp.status_code < 500:
                    resp.raise_for_status()  # raises on 4xx, returns normally on 2xx/3xx
                    return resp
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp,
                )
            except httpx.TransportError as exc:
                last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
        raise last_exc

    def _paginated_items(self, params: dict) -> list[dict]:
        items: list[dict] = []
        start = 0
        while True:
            page_params = {**params, "StartIndex": start, "Limit": _PAGE_SIZE}
            resp = self._request_with_retry("GET", f"/Users/{self.user_id}/Items", params=page_params)
            data = resp.json()
            page = data.get("Items", [])
            items.extend(page)
            start += _PAGE_SIZE
            if start >= data.get("TotalRecordCount", 0) or not page:
                break
        return items

    def movies(self) -> list[LibraryItem]:
        raw = self._paginated_items({
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Fields": "ProviderIds,UserData",
        })
        return [self._to_library_item(i) for i in raw]

    def series(self) -> list[LibraryItem]:
        raw = self._paginated_items({
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Fields": "ProviderIds",
        })
        return [self._to_library_item(i) for i in raw]

    def episodes(self) -> list[LibraryItem]:
        raw = self._paginated_items({
            "IncludeItemTypes": "Episode",
            "Recursive": "true",
            "Fields": "ProviderIds,UserData,ParentIndexNumber,IndexNumber,SeriesId",
        })
        return [self._to_library_item(i) for i in raw]

    def collections(self) -> list[JellyfinCollection]:
        raw = self._paginated_items({"IncludeItemTypes": "BoxSet", "Recursive": "true"})
        return [JellyfinCollection(collection_id=i["Id"], name=i.get("Name", "")) for i in raw]

    def collection_members(self, collection_id: str) -> list[LibraryItem]:
        """A collection's direct children -- movies or whole series (not
        individual episodes; Jellyfin collections group at that granularity)."""
        raw = self._paginated_items({"ParentId": collection_id, "Fields": "ProviderIds"})
        return [self._to_library_item(i) for i in raw]

    def create_collection(self, name: str, item_ids: list[str]) -> str:
        """Creates a new collection with the given items as initial members.
        Returns the new collection's Jellyfin item id."""
        resp = self._request_with_retry(
            "POST", "/Collections", params={"Name": name, "Ids": ",".join(item_ids)},
        )
        return resp.json()["Id"]

    def add_to_collection(self, collection_id: str, item_ids: list[str]) -> None:
        if not item_ids:
            return
        self._request_with_retry(
            "POST", f"/Collections/{collection_id}/Items", params={"Ids": ",".join(item_ids)},
        )

    def mark_played(self, item_id: str, date_played: datetime) -> None:
        """Sets Played=true AND the specific historical DatePlayed -- confirmed
        (against a live server, with read-back verification) that this actually
        persists the given date rather than substituting 'now'."""
        self._request_with_retry(
            "POST",
            f"/Users/{self.user_id}/PlayedItems/{item_id}",
            params={"DatePlayed": date_played.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
        )

    @staticmethod
    def _to_library_item(raw: dict) -> LibraryItem:
        provider_ids = raw.get("ProviderIds") or {}
        user_data = raw.get("UserData") or {}
        return LibraryItem(
            item_id=raw["Id"],
            name=raw.get("Name", ""),
            tmdb_id=provider_ids.get("Tmdb") or provider_ids.get("tmdb"),
            played=bool(user_data.get("Played", False)),
            last_played_date=user_data.get("LastPlayedDate"),
            series_id=raw.get("SeriesId"),
            season_number=raw.get("ParentIndexNumber"),
            episode_number=raw.get("IndexNumber"),
            item_type=(raw.get("Type") or "").rsplit(".", 1)[-1],
        )
