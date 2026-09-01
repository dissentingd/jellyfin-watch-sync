"""Source/Target interfaces for collections -- structurally identical in spirit
to the watch-history ones, kept as a separate pair rather than reusing those
because they're typed around CollectionRecord, not WatchRecord."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator

from .models import CollectionRecord
from .plan import CollectionPlan


class CollectionSource(ABC):
    @abstractmethod
    def records(self) -> Iterator[CollectionRecord]:
        raise NotImplementedError

    def describe(self) -> str:
        return self.__class__.__name__


class CollectionTarget(ABC):
    @abstractmethod
    def plan(self, records: Iterable[CollectionRecord]) -> CollectionPlan:
        raise NotImplementedError

    @abstractmethod
    def apply(self, plan: CollectionPlan) -> CollectionPlan:
        raise NotImplementedError
