"""Record kind enumeration for Health Connect types."""
from enum import Enum


class RecordKind(Enum):
    """Three high-level kinds: interval, instant, session."""

    INTERVAL = "interval"
    INSTANT = "instant"
    SESSION = "session"
