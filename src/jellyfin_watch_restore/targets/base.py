"""The Target interface: something records get restored into. Jellyfin is the
first (and currently only) implementation; Plex/Emby could be added the same way."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from ..models import WatchRecord
from ..plan import RestorePlan


class Target(ABC):
    @abstractmethod
    def plan(self, records: Iterable[WatchRecord], *, force: bool = False) -> RestorePlan:
        """Compute what WOULD happen, without writing anything. Always safe to call."""
        raise NotImplementedError

    @abstractmethod
    def apply(self, plan: RestorePlan) -> RestorePlan:
        """Execute a previously-computed plan's matched actions for real.
        Returns the plan with each action's outcome filled in."""
        raise NotImplementedError
