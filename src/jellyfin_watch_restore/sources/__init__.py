"""Pluggable watch-history sources. Each module exposes one Source subclass.

Adding a new source (another tracker, another export format) means writing one
class here that yields WatchRecord instances -- nothing else in the tool needs
to change. See base.py for the interface.
"""

from .base import Source
from .generic_csv import GenericCsvSource
from .jellyfin_backup import JellyfinBackupSource
from .yamtrack_csv import YamtrackCsvSource

__all__ = ["GenericCsvSource", "JellyfinBackupSource", "Source", "YamtrackCsvSource"]

try:  # optional: only importable if the [yamtrack-db] extra is installed
    from .yamtrack_db import YamtrackDbSource  # noqa: F401

    __all__.append("YamtrackDbSource")
except ImportError:
    pass
