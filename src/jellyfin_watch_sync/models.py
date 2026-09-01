"""Source-agnostic data model for a single watched item.

Every Source implementation yields WatchRecord instances; every Target
implementation consumes them. This is the seam that makes new sources/targets
pluggable without touching each other's code.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, field_validator, model_validator


class MediaType(str, Enum):
    MOVIE = "movie"
    EPISODE = "episode"


class WatchRecord(BaseModel):
    """One watched movie or episode, identified by TMDB id (+ season/episode for TV).

    TMDB id is the anchor because it's the identity that survives a media server's
    files being moved, renamed, or re-organized -- the exact problem this tool
    exists to work around. For an episode, `tmdb_id` is the *series'* TMDB id, not
    a per-episode id (TMDB doesn't give episodes their own top-level ids in a way
    that's useful here; season+episode numbers disambiguate within the series).
    """

    media_type: MediaType
    tmdb_id: int
    season: int | None = None
    episode: int | None = None
    watched_at: datetime
    play_count: int = 1
    title: str | None = None  # informational only; never used for matching

    @field_validator("tmdb_id")
    @classmethod
    def _positive_tmdb_id(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tmdb_id must be positive")
        return v

    @field_validator("play_count")
    @classmethod
    def _at_least_one(cls, v: int) -> int:
        return max(v, 1)

    @model_validator(mode="after")
    def _season_episode_together(self) -> WatchRecord:
        if self.media_type is MediaType.EPISODE:
            if self.season is None or self.episode is None:
                raise ValueError("episode records require both season and episode")
        elif self.season is not None or self.episode is not None:
            raise ValueError("movie records must not carry season/episode")
        return self

    @property
    def key(self) -> tuple:
        """Identity used for matching against a Jellyfin library index and for
        deduping records from a source before writing."""
        if self.media_type is MediaType.EPISODE:
            return (self.media_type, self.tmdb_id, self.season, self.episode)
        return (self.media_type, self.tmdb_id)
