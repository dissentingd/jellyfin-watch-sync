"""Targets for CollectionRecord: live Jellyfin (restore) and YAMTrack's Lists
table (backup)."""

from __future__ import annotations

from collections.abc import Iterable

from ..plan import ActionOutcome
from ..targets.jellyfin_client import JellyfinClient
from ..targets.jellyfin_index import JellyfinLibraryIndex
from .base import CollectionTarget
from .models import CollectionMemberType, CollectionRecord
from .plan import CollectionAction, CollectionPlan, MemberResolution


class JellyfinCollectionsTarget(CollectionTarget):
    """Restores collections into a live Jellyfin server. Never destructive:
    an existing collection with a matching name is only ever added to, never
    replaced or pruned -- if a member was intentionally removed from the
    Jellyfin collection since the backup was taken, this won't put it back
    (it only adds what's newly resolvable), and it also won't remove anything
    that's there now but wasn't in the source."""

    def __init__(self, client: JellyfinClient, index: JellyfinLibraryIndex | None = None) -> None:
        self.client = client
        self.index = index if index is not None else JellyfinLibraryIndex(client)
        self._existing_by_name: dict[str, str] = {}
        self._existing_members_cache: dict[str, set[str]] = {}

    def describe(self) -> str:
        return "Jellyfin collections"

    def _build_existing_index(self) -> None:
        if self._existing_by_name:
            return
        for coll in self.client.collections():
            self._existing_by_name[coll.name] = coll.collection_id

    def _existing_members(self, collection_id: str) -> set[str]:
        if collection_id not in self._existing_members_cache:
            members = self.client.collection_members(collection_id)
            self._existing_members_cache[collection_id] = {m.item_id for m in members}
        return self._existing_members_cache[collection_id]

    def plan(self, records: Iterable[CollectionRecord]) -> CollectionPlan:
        self.index.build()
        self._build_existing_index()
        plan = CollectionPlan(source_description="", target_description=self.describe())

        for record in records:
            resolutions: list[MemberResolution] = []
            for member in record.members:
                if member.media_type is CollectionMemberType.MOVIE:
                    item = self.index.resolve_movie(member.tmdb_id)
                    reason = "no movie with this TMDB id currently in the Jellyfin library"
                else:
                    item = self.index.resolve_series(member.tmdb_id)
                    reason = "no series with this TMDB id currently in the Jellyfin library"
                resolutions.append(
                    MemberResolution(
                        member=member,
                        target_item_id=item.item_id if item else None,
                        reason=None if item else reason,
                    )
                )

            existing_id = self._existing_by_name.get(record.name)
            action = CollectionAction(record=record, existing_target_id=existing_id, members=resolutions)

            if not action.matched_ids:
                plan.skipped_empty.append(record)
                continue
            plan.actions.append(action)
        return plan

    def apply(self, plan: CollectionPlan) -> CollectionPlan:
        for action in plan.actions:
            if action.outcome is not ActionOutcome.PENDING:
                continue
            try:
                if action.existing_target_id:
                    already = self._existing_members(action.existing_target_id)
                    to_add = [i for i in action.matched_ids if i not in already]
                    self.client.add_to_collection(action.existing_target_id, to_add)
                else:
                    self.client.create_collection(action.record.name, action.matched_ids)
                action.outcome = ActionOutcome.APPLIED
            except Exception as exc:  # noqa: BLE001 - report per-collection, keep going
                action.outcome = ActionOutcome.FAILED
                action.error = str(exc)
        return plan


class YamtrackListsTarget(CollectionTarget):
    """Backs up collections into YAMTrack as Lists. Every member is always
    "matched" here -- unlike restoring into Jellyfin, there's no existing-item
    lookup to fail: a member simply gets its YAMTrack Item row created if it
    doesn't already exist (stub title/image, same as any other bare import)."""

    def __init__(self, dsn: str, user_id: int) -> None:
        self.dsn = dsn
        self.user_id = user_id

    def describe(self) -> str:
        return f"YAMTrack Lists (user_id={self.user_id})"

    def plan(self, records: Iterable[CollectionRecord]) -> CollectionPlan:
        plan = CollectionPlan(source_description="", target_description=self.describe())
        for record in records:
            resolutions = [
                MemberResolution(member=m, target_item_id=f"tmdb:{m.tmdb_id}") for m in record.members
            ]
            if not resolutions:
                plan.skipped_empty.append(record)
                continue
            plan.actions.append(CollectionAction(record=record, existing_target_id=None, members=resolutions))
        return plan

    def apply(self, plan: CollectionPlan) -> CollectionPlan:
        try:
            import psycopg
        except ImportError as exc:
            raise ImportError(
                "YamtrackListsTarget requires the 'yamtrack-db' extra: "
                "pip install jellyfin-watch-restore[yamtrack-db]"
            ) from exc

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            for action in plan.actions:
                if action.outcome is not ActionOutcome.PENDING:
                    continue
                try:
                    list_id = self._find_or_create_list(cur, action.record)
                    for resolution in action.members:
                        item_type = "tv" if resolution.member.media_type is CollectionMemberType.SERIES else "movie"
                        item_id = self._find_or_create_item(
                            cur, resolution.member.tmdb_id, item_type, resolution.member.title,
                        )
                        cur.execute(
                            "INSERT INTO lists_customlistitem (item_id, custom_list_id, date_added) "
                            "VALUES (%s, %s, now()) "
                            "ON CONFLICT (item_id, custom_list_id) DO NOTHING",
                            (item_id, list_id),
                        )
                    action.outcome = ActionOutcome.APPLIED
                except Exception as exc:  # noqa: BLE001 - report per-collection, keep going
                    action.outcome = ActionOutcome.FAILED
                    action.error = str(exc)
            conn.commit()
        return plan

    def _find_or_create_list(self, cur, record: CollectionRecord) -> int:
        cur.execute(
            "SELECT id FROM lists_customlist WHERE owner_id = %s AND name = %s LIMIT 1",
            (self.user_id, record.name),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO lists_customlist (name, description, owner_id) VALUES (%s, %s, %s) RETURNING id",
            (record.name, record.description, self.user_id),
        )
        return cur.fetchone()[0]

    @staticmethod
    def _find_or_create_item(cur, tmdb_id: int, media_type: str, title: str | None) -> int:
        cur.execute(
            "SELECT id FROM app_item WHERE source = 'tmdb' AND media_type = %s AND media_id = %s "
            "AND season_number IS NULL AND episode_number IS NULL",
            (media_type, str(tmdb_id)),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            "INSERT INTO app_item (source, media_type, media_id, title, image, season_number, episode_number) "
            "VALUES ('tmdb', %s, %s, %s, '', NULL, NULL) RETURNING id",
            (media_type, str(tmdb_id), title or f"tmdb:{tmdb_id}"),
        )
        return cur.fetchone()[0]
