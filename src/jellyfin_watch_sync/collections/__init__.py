"""Backup and restore Jellyfin Collections (BoxSets) via YAMTrack or Floppy Lists.

Same shape as the watch-history feature: a Source yields CollectionRecords,
a Target consumes them. Backup direction is
JellyfinCollectionsSource -> YamtrackListsTarget; restore is the mirror,
YamtrackListsSource -> JellyfinCollectionsTarget.
"""

from .models import CollectionMember, CollectionMemberType, CollectionRecord
from .plan import CollectionPlan
from .sources import FloppyCsvListsSource, JellyfinCollectionsSource, YamtrackListsSource
from .targets import FloppyListsTarget, JellyfinCollectionsTarget, YamtrackListsTarget

__all__ = [
    "CollectionMember",
    "CollectionMemberType",
    "CollectionPlan",
    "CollectionRecord",
    "FloppyCsvListsSource",
    "FloppyListsTarget",
    "JellyfinCollectionsSource",
    "JellyfinCollectionsTarget",
    "YamtrackListsSource",
    "YamtrackListsTarget",
]
