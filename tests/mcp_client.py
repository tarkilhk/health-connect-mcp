"""Integration tests for the MCP server layer (Phase 6).

Uses httpx.ASGITransport against a Starlette app with an in-memory SQLite DB.
The MCP session manager is started manually in the fixture because
httpx.ASGITransport does not emit ASGI lifespan events.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import aiosqlite
import httpx
import pytest
from starlette.applications import Starlette

from health_hub.api.routes_debug import routes as debug_routes
from health_hub.api.routes_health import routes as health_routes
from health_hub.api.routes_ingest import routes as ingest_routes
from health_hub.app import _MCPAuthMiddleware
from health_hub.config import Settings
from health_hub.db.migrations import apply_migrations

# Importing tool modules registers @mcp.tool() decorators on the shared mcp.
from health_hub.mcp_server.server import mcp, set_db

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_TOKEN = "test-device-token"
MCP_TOKEN = "test-mcp-token"
SIGNING_SECRET = "test-secret"

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {MCP_TOKEN}",
}


def _sign(body: bytes, secret: str = SIGNING_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _auth_headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {DEVICE_TOKEN}"}
    if extra:
        headers.update(extra)
    return headers


def _load_full_payload() -> bytes:
    return (FIXTURES / "payload_health_full.json").read_bytes()


def _parse_sse(text: str) -> dict:
    """Extract JSON from the 'data:' line of an SSE response."""
    for line in text.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise ValueError(f"no data line in response: {text[:200]}")


def _tool_result(resp_json: dict) -> dict:
    """Extract the tool's return dict from an MCP tools/call response."""
    for item in resp_json["result"]["content"]:
        if item.get("type") == "text":
            return json.loads(item["text"])
    raise ValueError(f"no text content: {resp_json}")


def _make_settings() -> Settings:
    return Settings(
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        allowed_hosts=["localhost", "localhost:*"],
        signing_key=SIGNING_SECRET,
        database_path=":memory:",
        timezone="UTC",
        day_boundary_hour=0,
        log_level="ERROR",
        port=8000,
    )


@pytest.fixture
async def test_app(monkeypatch):
    """Starlette app with in-memory DB, MCP routes, and session manager running."""
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setenv("DAY_BOUNDARY_HOUR", "0")
    settings = _make_settings()
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await apply_migrations(db)
    await set_db(db)
    # Build MCP app with DNS-rebinding protection disabled for tests.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", transport_security=None, host="test"
    )
    app = Starlette(
        routes=health_routes + ingest_routes + debug_routes + list(mcp_app.routes),
    )
    app.state.settings = settings
    app.state.db = db
    app.add_middleware(_MCPAuthMiddleware, mcp_token=MCP_TOKEN)

    # Run session_manager.run() in a background task so its anyio cancel
    # scope is entered and exited in the same task (avoids the cross-task
    # teardown error that pytest-asyncio would otherwise trigger).
    async def _run_session():
        async with mcp.session_manager.run():
            await asyncio.Event().wait()  # block until cancelled

    session_task = asyncio.create_task(_run_session())
    await asyncio.sleep(0.05)  # let the session manager initialize
    try:
        yield app
    finally:
        session_task.cancel()
        try:
            await session_task
        except asyncio.CancelledError:
            pass
        await db.close()


@pytest.fixture
async def client(test_app):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db(test_app):
    return test_app.state.db


async def _mcp_call(client, method: str, params: dict | None = None, sid: str | None = None) -> dict:
    """Send a JSON-RPC request to /mcp and return the parsed response."""
    body: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers = dict(_MCP_HEADERS)
    if sid:
        headers["Mcp-Session-Id"] = sid
    r = await client.post("/mcp", json=body, headers=headers)
    assert r.status_code == 200, f"{method} failed: {r.status_code} {r.text[:200]}"
    return _parse_sse(r.text)


async def _init_session(client) -> str:
    """Initialize an MCP session and return the session ID."""
    r = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
        headers=_MCP_HEADERS,
    )
    assert r.status_code == 200, f"initialize failed: {r.status_code}"
    sid = r.headers.get("mcp-session-id")
    assert sid, "no session ID in initialize response"
    return sid


async def _call_tool(client, sid: str, name: str, args: dict) -> dict:
    """Call an MCP tool and return the parsed result dict."""
    resp = await _mcp_call(
        client, "tools/call",
        {"name": name, "arguments": args}, sid,
    )
    return _tool_result(resp)




async def _ingest(client, payload):
    body = json.dumps(payload).encode()
    response = await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert response.status_code == 200
    assert response.json()["errors"] == []
    return response.json()

