"""Apply schema.sql on startup and track the schema version."""
from __future__ import annotations

import logging
from importlib.resources import files

import aiosqlite

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2


async def apply_migrations(db: aiosqlite.Connection) -> None:
    """Execute schema.sql and record the current version.

    Idempotent DDL adds the current indexes and removes obsolete indexes.
    Stored events and raw payloads are unchanged.
    """
    schema_sql = _read_schema_sql()
    await db.executescript(schema_sql)
    await _record_version(db, SCHEMA_VERSION)
    await db.commit()
    log.info("Schema migrations applied (version=%d)", SCHEMA_VERSION)


def _read_schema_sql() -> str:
    return (
        files("health_hub.db")
        .joinpath("schema.sql")
        .read_text(encoding="utf-8")
    )


async def _record_version(db: aiosqlite.Connection, version: int) -> None:
    await db.execute("DELETE FROM schema_version")
    await db.execute(
        "INSERT INTO schema_version(version) VALUES (?)", (version,)
    )
