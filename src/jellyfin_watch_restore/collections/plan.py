"""The plan/dry-run data model for collections, mirroring the top-level
`plan.py` used for watch-history restores -- same discipline: plan() first,
apply() only writes what was already reviewed."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..plan import ActionOutcome
from .models import CollectionMember, CollectionRecord


@dataclass
class MemberResolution:
    """What happened trying to resolve one collection member against the
    target's current library."""

    member: CollectionMember
    target_item_id: str | None  # None if unmatched
    reason: str | None = None  # set only when target_item_id is None


@dataclass
class CollectionAction:
    """One collection to create or merge into the target."""

    record: CollectionRecord
    existing_target_id: str | None  # set if a collection with this name already exists
    members: list[MemberResolution]
    outcome: ActionOutcome = ActionOutcome.PENDING
    error: str | None = None

    @property
    def matched_ids(self) -> list[str]:
        return [m.target_item_id for m in self.members if m.target_item_id is not None]

    @property
    def unmatched(self) -> list[MemberResolution]:
        return [m for m in self.members if m.target_item_id is None]


@dataclass
class CollectionPlan:
    source_description: str
    target_description: str
    actions: list[CollectionAction] = field(default_factory=list)
    skipped_empty: list[CollectionRecord] = field(default_factory=list)  # zero members resolved

    def summary(self) -> str:
        applied = sum(1 for a in self.actions if a.outcome is ActionOutcome.APPLIED)
        failed = sum(1 for a in self.actions if a.outcome is ActionOutcome.FAILED)
        pending = sum(1 for a in self.actions if a.outcome is ActionOutcome.PENDING)
        total_members = sum(len(a.members) for a in self.actions)
        total_matched = sum(len(a.matched_ids) for a in self.actions)
        return "\n".join([
            f"source: {self.source_description}",
            f"target: {self.target_description}",
            f"collections: {len(self.actions)} (applied={applied} failed={failed} pending={pending})",
            f"members: {total_matched}/{total_members} resolved",
            f"skipped (no members resolved at all): {len(self.skipped_empty)}",
        ])
