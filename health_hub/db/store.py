"""Own the database connections and serialize complete ingestion write units."""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from health_hub.config import Settings
from health_hub.db.connection import open_connection
from health_hub.db.migrations import apply_migrations


class Database:
    """Committed reads on one connection, serialized writes on another.

    A write unit can commit raw input independently before its event
    transaction. Its entire lifetime holds the lock, so another request's
    commit or rollback cannot affect it. Uncommitted work is always discarded
    on exit, including cancellation.
    """

    def __init__(self, reader: aiosqlite.Connection, writer: aiosqlite.Connection):
        self.reader = reader
        self._writer = writer
        self._write_lock = asyncio.Lock()

    @classmethod
    async def open(cls, settings: Settings) -> "Database":
        if settings.database_path == ":memory:":
            raise ValueError("DATABASE_PATH must be a file for isolated, committed reads")
        writer = await open_connection(settings)
        try:
            await apply_migrations(writer)
            reader = await open_connection(settings)
            try:
                await reader.execute("PRAGMA query_only=ON")
            except BaseException:
                await reader.close()
                raise
        except BaseException:
            await writer.close()
            raise
        return cls(reader, writer)

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            try:
                yield self._writer
            finally:
                if self._writer.in_transaction:
                    await self._rollback()

    async def _rollback(self) -> None:
        rollback = asyncio.create_task(self._writer.rollback())
        cancelled = False
        while not rollback.done():
            try:
                await asyncio.shield(rollback)
            except asyncio.CancelledError:
                cancelled = True
        await rollback
        if cancelled:
            raise asyncio.CancelledError

    async def close(self) -> None:
        try:
            await self.reader.close()
        finally:
            await self._writer.close()
