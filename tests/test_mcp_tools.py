"""Integration tests for authenticated MCP summaries and discovery."""
from tests.mcp_client import (
    _MCP_HEADERS,
    _auth_headers,
    _call_tool,
    _init_session,
    _load_full_payload,
    _mcp_call,
    _parse_sse,
    _sign,
)

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
    assert result["serverInfo"]["name"] == "health-connect-mcp"


# --- tools/list -------------------------------------------------------------


async def test_tools_list_returns_seven_tools(client):
    sid = await _init_session(client)
    resp = await _mcp_call(client, "tools/list", {}, sid)
    tool_names = {t["name"] for t in resp["result"]["tools"]}
    expected = {"daily_summary", "metric_series", "sleep_detail", "get_sync_health", "inspect_last_payload", "list_data_types", "health_records"}
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
