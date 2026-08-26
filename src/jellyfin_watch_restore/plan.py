"""The plan/dry-run data model shared by every Target.

A restore always goes plan() -> (review) -> apply(plan) -> never records straight
to writes. This mirrors the dry-run-then-confirm discipline this tool exists to
make automatic, rather than something a user has to remember to do by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .models import WatchRecord


class ActionOutcome(str, Enum):
    PENDING = "pending"       # planned, apply() not yet run
    APPLIED = "applied"       # write succeeded
    FAILED = "failed"         # write attempted, target rejected it


@dataclass
class MatchedAction:
    """One record that resolves to a real, current item in the target and is
    (or would be) written."""

    record: WatchRecord
    target_item_id: str
    target_title: str | None
    outcome: ActionOutcome = ActionOutcome.PENDING
    error: str | None = None


@dataclass
class SkippedAlreadyCurrent:
    """A record whose target item is already marked watched with a date at
    least as recent as the record's -- writing would only ever regress it, so
    it's skipped by default. `--force` overrides this."""

    record: WatchRecord
    target_item_id: str
    current_watched_at: str


@dataclass
class UnmatchedRecord:
    """A record with no corresponding item currently in the target -- most
    often because the title was removed from the library outright, not just
    relocated (TMDB-id matching survives relocation; it can't survive removal)."""

    record: WatchRecord
    reason: str


@dataclass
class RestorePlan:
    source_description: str
    target_description: str
    matched: list[MatchedAction] = field(default_factory=list)
    skipped_already_current: list[SkippedAlreadyCurrent] = field(default_factory=list)
    unmatched: list[UnmatchedRecord] = field(default_factory=list)

    def summary(self) -> str:
        applied = sum(1 for m in self.matched if m.outcome is ActionOutcome.APPLIED)
        failed = sum(1 for m in self.matched if m.outcome is ActionOutcome.FAILED)
        pending = sum(1 for m in self.matched if m.outcome is ActionOutcome.PENDING)
        matched_line = f"matched: {len(self.matched)} (applied={applied} failed={failed} pending={pending})"
        lines = [
            f"source: {self.source_description}",
            f"target: {self.target_description}",
            matched_line,
            f"skipped (target already current): {len(self.skipped_already_current)}",
            f"unmatched (no item in target): {len(self.unmatched)}",
        ]
        return "\n".join(lines)
