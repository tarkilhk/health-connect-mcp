"""aiosqlite connection: WAL mode, foreign keys, app-level dependency."""
from __future__ import annotations

import os
from typing import Any

import aiosqlite

from health_hub.config import Settings


async def open_connection(settings: Settings) -> aiosqlite.Connection:
    """Open a connection with WAL and foreign_keys pragmas applied."""
    _ensure_data_dir(settings.database_path)
    db = await aiosqlite.connect(settings.database_path)
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = aiosqlite.Row
    return db


def _ensure_data_dir(database_path: str) -> None:
    directory = os.path.dirname(database_path)
    if directory:
        os.makedirs(directory, exist_ok=True)


async def get_db(request: Any) -> aiosqlite.Connection:
    """Starlette dependency: returns the app-level DB connection."""
    return request.app.state.db
