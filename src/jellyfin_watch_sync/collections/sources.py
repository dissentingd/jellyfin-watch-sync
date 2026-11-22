"""Sources of CollectionRecord: live Jellyfin (for backup), and YAMTrack's
Lists table or a Floppy CSV export's Lists (for restore)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from ..sources.floppy_csv import open_floppy_csv
from ..targets.jellyfin_client import JellyfinClient
from .base import CollectionSource
from .models import CollectionMember, CollectionMemberType, CollectionRecord

_ITEM_TYPE_TO_MEMBER_TYPE = {
    "Movie": CollectionMemberType.MOVIE,
    "Series": CollectionMemberType.SERIES,
}


class JellyfinCollectionsSource(CollectionSource):
    """Reads collections directly from a live Jellyfin server -- the backup
    direction. Members with no TMDB id, or of a type collections don't
    actually use (anything other than movie/series), are skipped."""

    def __init__(self, client: JellyfinClient) -> None:
        self.client = client

    def describe(self) -> str:
        return "Jellyfin collections (live)"

    def records(self) -> Iterator[CollectionRecord]:
        for coll in self.client.collections():
            members = []
            for item in self.client.collection_members(coll.collection_id):
                member_type = _ITEM_TYPE_TO_MEMBER_TYPE.get(item.item_type)
                if member_type is None or not item.tmdb_id:
                    continue
                members.append(CollectionMember(media_type=member_type, tmdb_id=int(item.tmdb_id), title=item.name))
            yield CollectionRecord(name=coll.name, members=members)


class YamtrackListsSource(CollectionSource):
    """Reads YAMTrack's own Lists (CustomList/CustomListItem) -- the restore
    direction. Optional (`pip install jellyfin-watch-sync[yamtrack-db]`)."""

    def __init__(self, dsn: str, user_id: int) -> None:
        """dsn: a standard Postgres connection string.
        user_id: the YAMTrack users_user.id whose lists to read (owned lists only,
        not lists they merely collaborate on)."""
        self.dsn = dsn
        self.user_id = user_id

    def describe(self) -> str:
        return f"YAMTrack Lists (user_id={self.user_id})"

    def records(self) -> Iterator[CollectionRecord]:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "YamtrackListsSource requires the 'yamtrack-db' extra: "
                "pip install jellyfin-watch-sync[yamtrack-db]"
            ) from exc

        sql = """
            SELECT cl.id, cl.name, cl.description, i.media_type, i.media_id, i.title
            FROM lists_customlist cl
            JOIN lists_customlistitem cli ON cli.custom_list_id = cl.id
            JOIN app_item i ON cli.item_id = i.id
            WHERE cl.owner_id = %(user_id)s AND i.source = 'tmdb'
            ORDER BY cl.id, cli.date_added
        """
        lists: dict[int, CollectionRecord] = {}
        list_order: list[int] = []
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, {"user_id": self.user_id})
            for list_id, name, description, media_type, media_id, title in cur:
                member_type = {"movie": CollectionMemberType.MOVIE, "tv": CollectionMemberType.SERIES}.get(media_type)
                if member_type is None:
                    continue  # a list can hold anime/manga/games/etc; collections can't
                if list_id not in lists:
                    lists[list_id] = CollectionRecord(name=name, description=description or "")
                    list_order.append(list_id)
                lists[list_id].members.append(
                    CollectionMember(media_type=member_type, tmdb_id=int(media_id), title=title)
                )
        for list_id in list_order:
            yield lists[list_id]


class FloppyCsvListsSource(CollectionSource):
    """Reads Lists out of a Floppy CSV export -- the restore direction, with
    no database access needed (Floppy can run on SQLite as well as Postgres,
    so a DB-level reader like YamtrackListsSource wouldn't work for everyone).

    Floppy writes one `list` row per List, then one `list_item` row per
    member, tied together by `list_uid` (checked against Floppy's own
    src/integrations/exports.py). Smart lists are included as they stand at
    export time: their stored members become a plain Jellyfin collection.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def describe(self) -> str:
        return f"Floppy Lists (CSV export: {self.path})"

    def records(self) -> Iterator[CollectionRecord]:
        lists: dict[str, CollectionRecord] = {}
        list_order: list[str] = []
        seen: dict[str, set[tuple]] = {}

        def ensure(uid: str, name: str, description: str = "") -> CollectionRecord | None:
            if uid not in lists:
                if not name.strip():
                    return None
                lists[uid] = CollectionRecord(name=name, description=description)
                list_order.append(uid)
                seen[uid] = set()
            return lists[uid]

        for row in open_floppy_csv(self.path):
            row_type = row.get("row_type", "").strip()
            uid = row.get("list_uid", "").strip()
            if row_type == "list" and uid:
                record = ensure(uid, row.get("list_name", ""), row.get("list_description", "") or "")
                if record is not None and not record.description:
                    record.description = row.get("list_description", "") or ""
            elif row_type == "list_item" and uid:
                # list rows always precede their items, but fall back to the
                # list_name carried on the item row itself just in case.
                record = ensure(uid, row.get("list_name", ""))
                if record is None:
                    continue
                member_type = {"movie": CollectionMemberType.MOVIE, "tv": CollectionMemberType.SERIES}.get(
                    row.get("media_type", "").strip().lower()
                )
                if member_type is None or row.get("source", "").strip().lower() != "tmdb":
                    continue  # anime/books/games/etc. or a non-TMDB id: not a Jellyfin collection member
                media_id = row.get("media_id", "").strip()
                if not media_id.isdigit():
                    continue
                member = CollectionMember(
                    media_type=member_type, tmdb_id=int(media_id), title=row.get("title", "").strip() or None,
                )
                if member.key in seen[uid]:
                    continue
                seen[uid].add(member.key)
                record.members.append(member)

        for uid in list_order:
            yield lists[uid]
