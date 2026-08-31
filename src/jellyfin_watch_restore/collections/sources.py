"""Sources of CollectionRecord: live Jellyfin (for backup) and YAMTrack's
Lists table (for restore)."""

from __future__ import annotations

from collections.abc import Iterator

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
    direction. Optional (`pip install jellyfin-watch-restore[yamtrack-db]`)."""

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
                "pip install jellyfin-watch-restore[yamtrack-db]"
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
