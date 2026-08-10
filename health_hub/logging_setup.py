"""Logging configuration. Uses %-formatting in log calls (no f-strings)."""
from __future__ import annotations

import logging

from health_hub.config import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(settings: Settings) -> None:
    """Configure root logging from settings. JSON-free, single line."""
    level = _level_from_name(settings.log_level)
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
    )
    logging.getLogger("health_hub").setLevel(level)


def _level_from_name(name: str) -> int:
    upper = name.upper()
    level = logging.getLevelName(upper)
    return level if isinstance(level, int) else logging.INFO
