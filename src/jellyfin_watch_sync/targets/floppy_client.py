"""A lean httpx client for Floppy's REST API (`/api/v1/`).

Floppy (github.com/dannyvfilms/Floppy) is the YAMTrack fork much of the
self-hosting community has moved to. Unlike YAMTrack, it's written to through
its API here rather than with SQL against its database, for three reasons
found by reading Floppy's source (not assumed):

- Floppy can run on SQLite as well as Postgres, so a psycopg-only writer
  would exclude a large share of installs.
- Its schema has diverged a long way from YAMTrack's (per-play rows such as
  `MoviePlay`, a separate watched-state log, ~100 columns on `app_item` with
  Python-side defaults), so raw INSERTs would bypass logic Floppy relies on.
- The API already does what this tool needs: dated watch endpoints that take
  an `external_id` (so replaying the same write never adds a duplicate play),
  Lists endpoints that create a missing item from TMDB on demand, and a flat
  history listing to plan against.

Endpoints used (all verified against Floppy's src/api/ on 2026-11-22):

    GET  /api/v1/history/?flat=1&types=movie,episode&limit=&offset=
    POST /api/v1/media/movie/tmdb/{id}/watch/                 {end_date, external_id}
    POST /api/v1/media/tv/tmdb/{id}/{season}/episodes/{ep}/watch/   {end_date, external_id}
    GET  /api/v1/lists/?limit=&offset=
    POST /api/v1/lists/                                       {name, description}
    PUT  /api/v1/media/{movie|tv}/tmdb/{id}/lists/{list_id}/  (409 = already in the list)

Auth is `Authorization: Bearer <token>` -- an API token from Floppy's
Settings, or an integration token.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

import httpx

_MAX_RETRIES = 4
_RETRY_BACKOFF_SECONDS = 1.0
_HISTORY_PAGE_SIZE = 200  # Floppy's documented maximum for /history/
_LISTS_PAGE_SIZE = 100


class FloppyError(Exception):
    """A Floppy API failure, already phrased for the user."""


@dataclass(frozen=True)
class FloppyHistoryEntry:
    media_type: str  # "movie" or "episode"
    tmdb_id: int  # the show's id for an episode
    season: int | None
    episode: int | None
    watched_at: datetime


@dataclass(frozen=True)
class FloppyList:
    list_id: int
    name: str


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


class FloppyClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- transport -------------------------------------------------------

    def _request(self, method: str, url: str, *, ok_statuses: tuple[int, ...] = (), **kwargs) -> httpx.Response:
        """Retries 5xx/429 and connection errors with exponential backoff
        (Floppy answers 503 when its SQLite writer is busy, which is exactly
        the transient case worth waiting out). Other 4xx fail immediately,
        unless listed in `ok_statuses`."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if resp.status_code in ok_statuses or resp.status_code < 400:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    raise FloppyError(self._describe_status(resp))
                last_exc = FloppyError(self._describe_status(resp))
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
        if isinstance(last_exc, httpx.ConnectError):
            raise FloppyError(
                f"Could not connect to Floppy at {self.base_url}.\n"
                "Check that FLOPPY_URL is correct (including http:// vs https://) and reachable."
            ) from last_exc
        if isinstance(last_exc, httpx.TransportError):
            raise FloppyError(f"Request to Floppy failed: {last_exc!r}") from last_exc
        raise last_exc  # a FloppyError

    def _describe_status(self, resp: httpx.Response) -> str:
        status = resp.status_code
        try:
            detail = resp.json().get("detail")
        except ValueError:
            detail = None
        where = f"{resp.request.method} {resp.request.url.path}"
        if status in (401, 403):
            return (
                f"Floppy rejected the request (HTTP {status}) for {where}.\n"
                "Check that FLOPPY_TOKEN is a valid API token for this Floppy user."
                + (f"\nFloppy said: {detail}" if detail else "")
            )
        suffix = f": {detail}" if detail else ""
        return f"Floppy returned HTTP {status} for {where}{suffix}"

    # -- history ---------------------------------------------------------

    def history(self) -> list[FloppyHistoryEntry]:
        """Every TMDB-sourced movie and episode watch this user has in Floppy."""
        entries: list[FloppyHistoryEntry] = []
        offset = 0
        while True:
            resp = self._request("GET", "/history/", params={
                "flat": "1", "types": "movie,episode", "limit": _HISTORY_PAGE_SIZE, "offset": offset,
            })
            data = resp.json()
            page = data.get("results", [])
            for raw in page:
                entry = self._to_history_entry(raw)
                if entry is not None:
                    entries.append(entry)
            offset += _HISTORY_PAGE_SIZE
            if not page or offset >= data.get("pagination", {}).get("total", 0):
                break
        return entries

    @staticmethod
    def _to_history_entry(raw: dict) -> FloppyHistoryEntry | None:
        media_type = raw.get("media_type")
        item = raw.get("item") or {}
        if media_type not in ("movie", "episode") or item.get("source") != "tmdb":
            return None
        media_id = str(item.get("media_id") or "")
        when = raw.get("end_date") or raw.get("played_at_local")
        if not media_id.isdigit() or not when:
            return None
        season = raw.get("season_number", item.get("season_number"))
        episode = raw.get("episode_number", item.get("episode_number"))
        if media_type == "episode" and (season is None or episode is None):
            return None
        return FloppyHistoryEntry(
            media_type=media_type,
            tmdb_id=int(media_id),
            season=int(season) if media_type == "episode" else None,
            episode=int(episode) if media_type == "episode" else None,
            watched_at=datetime.fromisoformat(str(when)),
        )

    def watch_movie(self, tmdb_id: int, watched_at: datetime, external_id: str) -> None:
        self._request("POST", f"/media/movie/tmdb/{tmdb_id}/watch/", json={
            "end_date": _iso(watched_at), "external_id": external_id,
        })

    def watch_episode(self, tmdb_id: int, season: int, episode: int, watched_at: datetime, external_id: str) -> None:
        self._request("POST", f"/media/tv/tmdb/{tmdb_id}/{season}/episodes/{episode}/watch/", json={
            "end_date": _iso(watched_at), "external_id": external_id,
        })

    # -- lists -----------------------------------------------------------

    def lists(self) -> list[FloppyList]:
        found: list[FloppyList] = []
        offset = 0
        while True:
            resp = self._request("GET", "/lists/", params={"limit": _LISTS_PAGE_SIZE, "offset": offset})
            data = resp.json()
            page = data.get("results", [])
            found.extend(FloppyList(list_id=int(r["id"]), name=r.get("name", "")) for r in page)
            offset += _LISTS_PAGE_SIZE
            if not page or offset >= data.get("pagination", {}).get("total", 0):
                break
        return found

    def create_list(self, name: str, description: str = "") -> int:
        resp = self._request("POST", "/lists/", json={"name": name, "description": description})
        return int(resp.json()["id"])

    def add_to_list(self, list_id: int, media_type: str, tmdb_id: int) -> bool:
        """Adds a movie or show (media_type "movie"/"tv") to a list. Returns
        False if it was already there (Floppy answers 409), True if added."""
        resp = self._request("PUT", f"/media/{media_type}/tmdb/{tmdb_id}/lists/{list_id}/", ok_statuses=(409,))
        return resp.status_code != 409
