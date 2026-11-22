from .base import Target
from .floppy_client import FloppyClient
from .floppy_history import FloppyHistoryTarget
from .generic_csv import GenericCsvTarget
from .jellyfin import JellyfinTarget
from .jellyfin_index import JellyfinLibraryIndex

__all__ = [
    "FloppyClient",
    "FloppyHistoryTarget",
    "GenericCsvTarget",
    "JellyfinLibraryIndex",
    "JellyfinTarget",
    "Target",
]

try:  # optional: only importable if the [yamtrack-db] extra is installed
    from .yamtrack_history import YamtrackHistoryTarget  # noqa: F401

    __all__.append("YamtrackHistoryTarget")
except ImportError:
    pass
