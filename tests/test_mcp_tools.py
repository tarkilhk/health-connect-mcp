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
from health_hub.config import Settings
from health_hub.db.migrations import apply_migrations

# Importing tool modules registers @mcp.tool() decorators on the shared mcp.
from health_hub.mcp_server import tools_ops, tools_sleep, tools_summary  # noqa: F401
from health_hub.mcp_server.server import mcp, set_db

FIXTURES = Path(__file__).parent / "fixtures"
DEVICE_TOKEN = "test-device-token"
MCP_TOKEN = "test-mcp-token"
SIGNING_SECRET = "test-secret"

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
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
async def test_app():
    """Starlette app with in-memory DB, MCP routes, and session manager running."""
    settings = _make_settings()
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await apply_migrations(db)
    set_db(db)
    # Build MCP app with DNS-rebinding protection disabled for tests.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp", transport_security=None, host="test"
    )
    app = Starlette(
        routes=health_routes + ingest_routes + debug_routes + list(mcp_app.routes),
    )
    app.state.settings = settings
    app.state.db = db

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


# --- MCP initialize ---------------------------------------------------------


async def test_initialize_returns_capabilities(client):
    sid = await _init_session(client)
    assert len(sid) > 0


async def test_initialize_response_has_server_info(client):
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
    data = _parse_sse(r.text)
    result = data["result"]
    assert "capabilities" in result
    assert result["serverInfo"]["name"] == "health-hub"


# --- tools/list -------------------------------------------------------------


async def test_tools_list_returns_five_tools(client):
    sid = await _init_session(client)
    resp = await _mcp_call(client, "tools/list", {}, sid)
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    expected = {"daily_summary", "metric_series", "sleep_detail", "get_sync_health", "inspect_last_payload"}
    assert tool_names == expected, f"got {tool_names}"


# --- daily_summary ----------------------------------------------------------


async def test_daily_summary_no_data_returns_caveats(client):
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "daily_summary",
                              {"date_from": "2026-08-09", "date_to": "2026-08-09"})
    assert result["days"] == [{"local_date": "2026-08-09"}]
    assert result["coverage"]["2026-08-09"]["steps"] == "absent"
    assert len(result["caveats"]) > 0
    assert any("no data for 2026-08-09" in c for c in result["caveats"])


async def test_daily_summary_after_ingest_returns_rollup(client, db):
    # Ingest a health payload first.
    body = _load_full_payload()
    r = await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    assert r.status_code == 200

    sid = await _init_session(client)
    result = await _call_tool(client, sid, "daily_summary",
                              {"date_from": "2026-08-09", "date_to": "2026-08-09"})
    days = result["days"]
    assert len(days) == 1
    day = days[0]
    assert day["local_date"] == "2026-08-09"
    assert day["steps"] == 1801
    assert day["distance_m"] == 1230.5
    assert day["hr_avg"] == 75.4
    assert day["weight_kg"] == 72.4
    assert result["coverage"]["2026-08-09"]["steps"] == "ok"
    assert len(result["caveats"]) > 0


async def test_daily_summary_with_metrics_filter(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "daily_summary",
                              {"date_from": "2026-08-09", "date_to": "2026-08-09",
                               "metrics": ["steps"]})
    day = result["days"][0]
    assert "steps" in day
    assert "weight_kg" not in day
    assert "hr_avg" not in day
    assert any("showing only requested metrics" in c for c in result["caveats"])


# --- metric_series ----------------------------------------------------------


async def test_metric_series_returns_points(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "metric_series",
                              {"metric": "steps", "date_from": "2026-08-09",
                               "date_to": "2026-08-09"})
    assert result["metric"] == "steps"
    assert result["unit"] == "count"
    assert len(result["points"]) == 1
    assert result["points"][0]["value"] == 1801
    assert len(result["caveats"]) > 0


async def test_metric_series_unknown_metric(client):
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "metric_series",
                              {"metric": "nonexistent", "date_from": "2026-08-09",
                               "date_to": "2026-08-09"})
    assert result["points"] == []
    assert any("unknown metric" in c for c in result["caveats"])


# --- sleep_detail -----------------------------------------------------------


async def test_sleep_detail_returns_sessions(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "sleep_detail",
                              {"date_from": "2026-08-08", "date_to": "2026-08-09"})
    sessions = result["sessions"]
    assert len(sessions) >= 2
    # Each session has end_utc, durations, and stages.
    for s in sessions:
        assert "end_utc" in s
        assert "time_in_bed_s" in s
        assert "time_asleep_s" in s
        assert isinstance(s["stages"], list)
    assert len(result["caveats"]) > 0


# --- get_sync_health --------------------------------------------------------


async def test_get_sync_health_no_data(client):
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "get_sync_health", {"days": 7})
    assert result["period_days"] == 7
    assert len(result["metrics"]) == 34  # all data types
    assert all(m["coverage_days"] == 0 for m in result["metrics"])
    assert len(result["caveats"]) > 0


async def test_get_sync_health_after_ingest(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "get_sync_health", {"days": 30})
    steps_metric = next(m for m in result["metrics"] if m["data_type"] == "steps")
    assert steps_metric["coverage_days"] >= 1
    assert steps_metric["last_event_utc"] is not None
    assert len(result["caveats"]) > 0


# --- inspect_last_payload ---------------------------------------------------


async def test_inspect_last_payload_no_payloads(client):
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "inspect_last_payload", {})
    assert result["payload_id"] is None
    assert any("no payloads" in c for c in result["caveats"])


async def test_inspect_last_payload_after_ingest(client, db):
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)
    result = await _call_tool(client, sid, "inspect_last_payload", {})
    assert result["payload_id"] == 1
    assert result["source"] == "health_connect"
    assert result["app_version"] == "1.4.0"
    assert result["signature_valid"] is True
    assert len(result["diagnostics"]) >= 1
    assert result["diagnostics"][0]["data_type"] == "steps"
    assert len(result["caveats"]) > 0


# --- caveats always present -------------------------------------------------


async def test_caveats_always_present_in_every_tool(client, db):
    """Every tool response must include a non-empty caveats list (SPEC §9.2)."""
    body = _load_full_payload()
    await client.post(
        "/ingest/health", content=body,
        headers=_auth_headers({"X-Signature": _sign(body)}),
    )
    sid = await _init_session(client)

    calls = [
        ("daily_summary", {"date_from": "2026-08-09", "date_to": "2026-08-09"}),
        ("metric_series", {"metric": "steps", "date_from": "2026-08-09", "date_to": "2026-08-09"}),
        ("sleep_detail", {"date_from": "2026-08-08", "date_to": "2026-08-09"}),
        ("get_sync_health", {"days": 7}),
        ("inspect_last_payload", {}),
    ]
    for name, args in calls:
        result = await _call_tool(client, sid, name, args)
        assert "caveats" in result, f"{name} missing caveats key"
        assert isinstance(result["caveats"], list), f"{name} caveats not a list"
        assert len(result["caveats"]) > 0, f"{name} has empty caveats"
