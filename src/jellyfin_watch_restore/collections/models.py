"""Source-agnostic data model for a Jellyfin Collection (BoxSet).

A collection groups movies and/or whole series -- not individual episodes;
that's the granularity Jellyfin itself uses for BoxSets (confirmed against a
real collection on a real server: a "Kids TV" collection held 200 whole
series, no per-episode members).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator


class CollectionMemberType(str, Enum):
    MOVIE = "movie"
    SERIES = "series"


class CollectionMember(BaseModel):
    media_type: CollectionMemberType
    tmdb_id: int
    title: str | None = None  # informational only; never used for matching

    @field_validator("tmdb_id")
    @classmethod
    def _positive_tmdb_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tmdb_id must be positive")
        return v

    @property
    def key(self) -> tuple:
        return (self.media_type, self.tmdb_id)


class CollectionRecord(BaseModel):
    name: str
    description: str = ""
    members: list[CollectionMember] = []

    @field_validator("name")
    @classmethod
    def _nonempty_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("collection name must not be empty")
        return v
