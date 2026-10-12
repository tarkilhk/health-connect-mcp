"""Authenticated HTTP transport for the debug inspector and its JSON API."""
import hmac
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from health_hub.db import inspect_payloads, inspect_status

_DEBUG_HTML_PATH = Path(__file__).parent / "debug.html"
Handler = Callable[[Request], Awaitable[Response]]


def _authenticated(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapped(request: Request) -> Response:
        expected = request.app.state.settings.mcp_token
        provided = request.query_params.get("token", "")
        if not expected or not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
            return Response(status_code=401)
        return await handler(request)
    return wrapped


def _integer(request: Request, name: str, default: int, maximum: int) -> int:
    value = int(request.query_params.get(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


async def serve_debug_html(request: Request) -> Response:
    return HTMLResponse(_DEBUG_HTML_PATH.read_text(encoding="utf-8"))


async def list_payloads(request: Request) -> Response:
    try:
        limit = _integer(request, "limit", 20, 100)
    except ValueError:
        return JSONResponse({"error": "invalid_limit"}, status_code=400)
    return JSONResponse(await inspect_payloads.list_payloads(request.app.state.db, limit))


async def get_payload(request: Request) -> Response:
    try:
        payload_id = int(request.path_params["id"])
    except ValueError:
        return JSONResponse({"error": "invalid_id"}, status_code=400)
    payload = await inspect_payloads.get_payload(request.app.state.db, payload_id)
    if payload is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(payload)


async def get_diagnostics(request: Request) -> Response:
    try:
        payload_id = int(request.query_params.get("payload_id", ""))
    except ValueError:
        return JSONResponse({"error": "invalid_payload_id"}, status_code=400)
    return JSONResponse(await inspect_payloads.load_diagnostics(request.app.state.db, payload_id))


async def get_coverage(request: Request) -> Response:
    try:
        days = _integer(request, "days", 30, 365)
    except ValueError:
        return JSONResponse({"error": "invalid_days"}, status_code=400)
    return JSONResponse(await inspect_status.get_coverage(
        request.app.state.db, days, request.app.state.settings,
    ))


async def get_rollup(request: Request) -> Response:
    try:
        result = await inspect_status.get_rollup(
            request.app.state.db, request.path_params["local_date"], request.app.state.settings,
        )
    except ValueError:
        return JSONResponse({"error": "invalid_date"}, status_code=400)
    return JSONResponse(result)


async def get_stats(request: Request) -> Response:
    return JSONResponse(await inspect_status.get_stats(request.app.state.db))


routes: list[Route] = [Route(path, _authenticated(handler), methods=["GET"]) for path, handler in [
    ("/debug", serve_debug_html),
    ("/debug/api/payloads", list_payloads),
    ("/debug/api/payload/{id}", get_payload),
    ("/debug/api/diagnostics", get_diagnostics),
    ("/debug/api/coverage", get_coverage),
    ("/debug/api/rollup/{local_date}", get_rollup),
    ("/debug/api/stats", get_stats),
]]
