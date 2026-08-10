"""Composition root: builds the Starlette app and manages the DB + MCP lifespan."""
from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from health_hub.api.routes_debug import routes as debug_routes
from health_hub.api.routes_health import routes as health_routes
from health_hub.api.routes_ingest import routes as ingest_routes
from health_hub.config import Settings, load_settings
from health_hub.db.connection import open_connection
from health_hub.db.migrations import apply_migrations
from health_hub.logging_setup import configure_logging

# Import tool modules to register @mcp.tool() decorators at import time.
from health_hub.mcp_server import tools_ops, tools_sleep, tools_summary  # noqa: F401
from health_hub.mcp_server.server import create_mcp_app, mcp_lifespan, set_db

log = logging.getLogger(__name__)


class _MCPAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` for /mcp routes (SPEC §9.2)."""

    def __init__(self, app: ASGIApp, mcp_token: str) -> None:
        self._app = app
        self._mcp_token = mcp_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            auth = next(
                (v for k, v in scope.get("headers", []) if k == b"authorization"), None
            )
            token = auth[7:].decode() if auth and auth.startswith(b"Bearer ") else ""
            if not hmac.compare_digest(token, self._mcp_token):
                resp = Response(status_code=401)
                await resp(scope, receive, send)
                return
        await self._app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings)
    log.info(
        "Starting Health Hub (port=%d, log_level=%s)", settings.port, settings.log_level
    )
    db = await open_connection(settings)
    await apply_migrations(db)
    app.state.db = db
    # Expose the DB to MCP tools via the module-level accessor (SPEC §9.3.3:
    # single worker, so one process-wide connection is safe).
    set_db(db)
    try:
        # Run the MCP session manager inside the DB lifespan so both are
        # alive for the duration of the app (SPEC §9.3.2: the built-in
        # lifespan of the mounted MCP app does not run).
        async with mcp_lifespan(app):
            yield
    finally:
        await db.close()
        log.info("Health Hub stopped")


def create_app() -> Starlette:
    settings = load_settings()
    mcp_app = create_mcp_app(settings)
    # Extract the MCP app's routes directly into the parent so POST /mcp
    # works without a trailing-slash redirect (Mount("/mcp", ...) would 307
    # to /mcp/ and break the SPEC §9 acceptance curl).
    mcp_routes = list(mcp_app.routes)
    app = Starlette(
        debug=settings.log_level.upper() == "DEBUG",
        routes=health_routes + ingest_routes + debug_routes + mcp_routes,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Gate /mcp routes behind MCP_TOKEN (SPEC §9.2: health data must not be
    # public).  The middleware checks Authorization: Bearer before any route
    # handler runs.
    app.add_middleware(_MCPAuthMiddleware, mcp_token=settings.mcp_token)
    return app


app = create_app()
