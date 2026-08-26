"""Reads watch history out of a Jellyfin native backup (10.10+'s built-in
Settings -> Backups, or the `POST /Backup/Create` API) -- either the .zip file
directly or an already-extracted directory.

This is the distinguishing feature of this tool: restoring FROM an older backup
INTO a Jellyfin server whose current watch history has been damaged (files
relocated/re-organized, an upgrade gone wrong, etc.) -- the backup captures
watch state as of when it was taken, independent of whatever's happened to the
live server since.

The backup's Database/*.json files are Jellyfin's own EF Core table export
(JSON arrays, not a raw SQLite dump) -- BaseItems.json in particular is
commonly 1GB+, so it's read with `ijson` (streaming) rather than json.load.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import ijson

from ..models import MediaType, WatchRecord
from .base import Source

_NEEDED_FILES = (
    "Database/Users.json",
    "Database/UserData.json",
    "Database/BaseItems.json",
    "Database/BaseItemProviders.json",
)


class JellyfinBackupSource(Source):
    def __init__(self, path: str | Path, username: str) -> None:
        """path: a backup .zip, or a directory it's already been extracted into.
        username: the Jellyfin username whose watch history to read."""
        self.path = Path(path)
        self.username = username

    def describe(self) -> str:
        return f"Jellyfin backup ({self.path.name}), user={self.username}"

    def records(self) -> Iterator[WatchRecord]:
        with self._database_dir() as db_dir:
            user_id = self._resolve_user_id(db_dir)
            watched = self._load_watched(db_dir, user_id)
            tmdb_of = self._load_tmdb_ids(db_dir)
            yield from self._stream_watched_items(db_dir, watched, tmdb_of)

    @contextmanager
    def _database_dir(self):
        """Yield a directory containing Database/*.json, extracting the zip
        to a temp dir first if `path` is a zip file."""
        if self.path.is_dir():
            yield self.path
            return
        with tempfile.TemporaryDirectory(prefix="jf-backup-") as tmp:
            with zipfile.ZipFile(self.path) as zf:
                zf.extractall(tmp, members=_NEEDED_FILES)
            yield Path(tmp)

    def _resolve_user_id(self, db_dir: Path) -> str:
        users = json.loads((db_dir / "Database/Users.json").read_text(encoding="utf-8"))
        for u in users:
            if u.get("Username") == self.username:
                return u["Id"]
        raise ValueError(f"user {self.username!r} not found in this backup")

    @staticmethod
    def _load_watched(db_dir: Path, user_id: str) -> dict[str, dict]:
        watched: dict[str, dict] = {}
        with (db_dir / "Database/UserData.json").open("rb") as f:
            for row in ijson.items(f, "item"):
                if row.get("UserId") != user_id:
                    continue
                if not (row.get("Played") or (row.get("PlayCount") or 0) > 0):
                    continue
                watched[row["ItemId"]] = row
        return watched

    @staticmethod
    def _load_tmdb_ids(db_dir: Path) -> dict[str, str]:
        tmdb_of: dict[str, str] = {}
        with (db_dir / "Database/BaseItemProviders.json").open("rb") as f:
            for row in ijson.items(f, "item"):
                if row.get("ProviderId") == "Tmdb":
                    tmdb_of[row["ItemId"]] = row.get("ProviderValue")
        return tmdb_of

    def _stream_watched_items(
        self, db_dir: Path, watched: dict[str, dict], tmdb_of: dict[str, str],
    ) -> Iterator[WatchRecord]:
        # Two passes over BaseItems.json, deliberately: a Series record can appear
        # AFTER its own episodes in the file (order isn't guaranteed), so resolving
        # series_tmdb inline during a single pass would silently drop any episode
        # whose series hadn't been seen yet. Each pass still streams (ijson), so
        # this costs one extra sequential file read, not extra memory.
        series_tmdb: dict[str, str] = {}
        with (db_dir / "Database/BaseItems.json").open("rb") as f:
            for item in ijson.items(f, "item"):
                if (item.get("Type") or "").rsplit(".", 1)[-1] != "Series":
                    continue
                tid = tmdb_of.get(item["Id"])
                if tid:
                    series_tmdb[item["Id"]] = tid

        with (db_dir / "Database/BaseItems.json").open("rb") as f:
            for item in ijson.items(f, "item"):
                if item["Id"] not in watched:
                    continue
                item_type = (item.get("Type") or "").rsplit(".", 1)[-1]

                ud = watched[item["Id"]]
                when = ud.get("LastPlayedDate")
                if not when:
                    continue

                if item_type == "Movie":
                    tid = tmdb_of.get(item["Id"])
                    if not tid:
                        continue
                    yield WatchRecord(
                        media_type=MediaType.MOVIE, tmdb_id=int(tid),
                        watched_at=when, play_count=ud.get("PlayCount", 1) or 1,
                        title=item.get("Name"),
                    )
                elif item_type == "Episode":
                    series_id = item.get("SeriesId")
                    tid = series_tmdb.get(series_id)
                    season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
                    if not tid or season is None or episode is None:
                        continue
                    yield WatchRecord(
                        media_type=MediaType.EPISODE, tmdb_id=int(tid),
                        season=season, episode=episode, watched_at=when,
                        play_count=ud.get("PlayCount", 1) or 1, title=item.get("Name"),
                    )
