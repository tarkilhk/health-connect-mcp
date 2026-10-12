"""aiosqlite connection: WAL mode, foreign keys, app-level dependency."""
from __future__ import annotations

import os

import aiosqlite

from health_hub.aggregate.timewindow import utc_sort_key
from health_hub.config import Settings


async def open_connection(settings: Settings) -> aiosqlite.Connection:
    """Open a connection with WAL and foreign_keys pragmas applied."""
    _ensure_data_dir(settings.database_path)
    db = await aiosqlite.connect(settings.database_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        db.row_factory = aiosqlite.Row
        await db.create_function("utc_instant", 1, utc_sort_key, deterministic=True)
    except BaseException:
        await db.close()
        raise
    return db


def _ensure_data_dir(database_path: str) -> None:
    directory = os.path.dirname(database_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
