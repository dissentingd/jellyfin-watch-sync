"""The Source interface every watch-history reader implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..models import WatchRecord


class Source(ABC):
    """Yields WatchRecord instances from wherever this source's data lives.

    Implementations should be read-only and side-effect-free: a Source's job is
    only to produce records, never to talk to Jellyfin or mutate anything.
    """

    @abstractmethod
    def records(self) -> Iterator[WatchRecord]:
        """Yield every watched item this source knows about.

        Implementations should stream where practical (a source's underlying
        data can be large -- a Jellyfin backup's item table is commonly 1GB+)
        rather than materializing everything in memory up front.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """One-line human-readable description for CLI output, e.g. in a dry-run
        summary ("YAMTrack CSV export: /path/to/export.csv")."""
        return self.__class__.__name__
