"""MCPServer instance, DB accessor, and lifespan helper.

The MCP 2.0 SDK (``mcp.server.mcpserver.MCPServer``) is used instead of the
deprecated ``FastMCP``. Key differences from the SPEC §9.3 examples (which
were written for MCP 1.x):

* ``transport_security`` is passed to ``streamable_http_app()``, NOT to the
  constructor.
* The session manager is created lazily inside ``streamable_http_app()``;
  ``mcp.session_manager`` raises if that method has not been called yet.
* The built-in lifespan of the inner Starlette app does NOT run when its
  routes are mounted in a parent app, so ``session_manager.run()`` must be
  invoked manually from the parent lifespan.

DB access: a module-level ``_db`` connection set during the parent lifespan.
It is a dedicated, query-only connection: concurrent ingestion uses a
separate writer and never exposes uncommitted events.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from health_hub import __version__
from health_hub.config import Settings

mcp: MCPServer = MCPServer("health-connect-mcp", version=__version__)

_db: aiosqlite.Connection | None = None


def set_db(db: aiosqlite.Connection | None) -> None:
    """Set the committed-read connection, or clear it during shutdown."""
    global _db
    _db = db


def get_db() -> aiosqlite.Connection:
    """Return the DB connection set during lifespan startup."""
    if _db is None:
        raise RuntimeError("DB not initialized — set_db() not called yet")
    return _db


def create_mcp_app(settings: Settings) -> Starlette:
    """Build the streamable-HTTP Starlette app for the MCP server.

    Called once during ``create_app()`` so that ``mcp.session_manager`` is
    available for the parent lifespan. The returned app's routes are
    extracted directly into the parent app (see app.py) so that ``POST /mcp``
    works without a trailing-slash redirect.
    """
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(settings.allowed_hosts),
        ),
    )


@asynccontextmanager
async def mcp_lifespan(app: Starlette) -> AsyncIterator[None]:
    """Run the MCP session manager inside the parent app's lifespan.

    Without this, the first MCP request hangs with no error because the
    session manager's task group is never initialized (SPEC §9.3.2).
    """
    async with mcp.session_manager.run():
        yield
