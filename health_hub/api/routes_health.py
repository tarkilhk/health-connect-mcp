"""GET /health — unauthenticated liveness probe."""
from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from health_hub import __version__

log = logging.getLogger(__name__)


async def health(request: Request) -> JSONResponse:
    """Return service status. Always 200; db field reflects DB reachability."""
    db_status = await _check_db(request)
    return JSONResponse(
        {"status": "ok", "version": __version__, "db": db_status}
    )


async def _check_db(request: Request) -> str:
    try:
        db = request.app.state.db
        cursor = await db.execute("SELECT 1")
        await cursor.fetchone()
    except Exception:
        log.exception("DB health check failed")
        return "error"
    return "ok"


routes: list[Route] = [Route("/health", health, methods=["GET"])]
